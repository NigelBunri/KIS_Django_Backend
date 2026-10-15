"""AND-247 / AND-260: the mobile "Create Shop" quick action had no way to
know a shop requires a Business+ plan (ShopViewSet.perform_create enforces
the "shops_limit" tier feature, apps/commerce/views.py) - unlike "Create
Partner", which already disables its CTA via partner_profiles_can_create
on GET /api/v1/profiles/me/. A free/Pro-tier user could fill out the whole
create-shop form, submit it, and only then learn (via an Alert) that their
plan doesn't allow it - read by testers as "shop information were not
saved, no shop created". This covers the new shop_profiles_can_create
flag added to the same /me/ payload so the client can gate the CTA the
same way it already does for partners.
"""
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import AccountTier, Subscription, User
from apps.accounts.tiers import ensure_default_account_tiers
from apps.commerce.models import Shop


class ShopProfileCreateEntryPointTests(APITestCase):
    def setUp(self):
        ensure_default_account_tiers()

    def test_free_tier_user_cannot_create_shop(self):
        user = User.objects.create_user(
            phone="+237670050001", password="TestPass123!", country="CM",
        )
        self.client.force_authenticate(user=user)

        response = self.client.get(reverse("profiles-me"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["shop_profiles_can_create"])
        self.assertEqual(response.data["shop_profiles_count"], 0)
        self.assertEqual(response.data["shop_profiles_limit_value"], 0)

    def test_business_tier_user_can_create_shop_until_limit_reached(self):
        user = User.objects.create_user(
            phone="+237670050002", password="TestPass123!", country="CM",
        )
        business_tier = AccountTier.objects.filter(name__iexact="Business").first()
        Subscription.objects.create(user=user, tier=business_tier, status="active")
        self.client.force_authenticate(user=user)

        response = self.client.get(reverse("profiles-me"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["shop_profiles_can_create"])
        self.assertEqual(response.data["shop_profiles_limit_value"], 2)

        Shop.objects.create(owner=user, name="Shop One", slug="shop-one")
        Shop.objects.create(owner=user, name="Shop Two", slug="shop-two")

        response = self.client.get(reverse("profiles-me"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["shop_profiles_can_create"])
        self.assertEqual(response.data["shop_profiles_count"], 2)

    def test_partner_pro_tier_user_has_unlimited_shops(self):
        user = User.objects.create_user(
            phone="+237670050003", password="TestPass123!", country="CM",
        )
        partner_pro_tier = AccountTier.objects.filter(name__iexact="Partner Pro").first()
        Subscription.objects.create(user=user, tier=partner_pro_tier, status="active")
        self.client.force_authenticate(user=user)

        response = self.client.get(reverse("profiles-me"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["shop_profiles_can_create"])
        self.assertIsNone(response.data["shop_profiles_limit_value"])
