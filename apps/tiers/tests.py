from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate
from unittest.mock import patch

from .models import BillingPlan, Subscription, User
from .views import SubscriptionViewSet, UserViewSet

class TiersSmokeTest(TestCase):
    def test_plan_create(self):
        p = BillingPlan.objects.create(slug='basic', display_name='Basic', price_per_month=0)
        self.assertIsNotNone(p.id)


class TiersAccessBoundaryTests(TestCase):
    def setUp(self):
        auth_user = get_user_model()
        self.owner = auth_user.objects.create_user(phone="+237670004001", password="TestPass123!", country="CM")
        self.other = auth_user.objects.create_user(phone="+237670004002", password="TestPass123!", country="CM")
        self.staff = auth_user.objects.create_user(
            phone="+237670004003",
            password="TestPass123!",
            country="CM",
            is_staff=True,
        )
        self.plan = BillingPlan.objects.create(slug="owner-plan", display_name="Owner Plan", price_per_month=0)
        with patch("apps.tiers.signals.reconcile_subscription.delay"):
            self.owner_subscription = Subscription.objects.create(owner_type="user", owner_id=self.owner.id, plan=self.plan)
            self.other_subscription = Subscription.objects.create(owner_type="user", owner_id=self.other.id, plan=self.plan)
        self.shadow_user = User.objects.create(
            email="shadow@example.com",
            username="shadow",
            password_hash="raw-hash-should-never-leak",
        )
        self.factory = APIRequestFactory()

    def _rows(self, response):
        payload = response.data
        return payload.get("results", payload)

    def _list_view(self, viewset, user):
        request = self.factory.get("/")
        force_authenticate(request, user=user)
        response = viewset.as_view({"get": "list"})(request)
        response.render()
        return response

    def test_subscription_list_is_limited_to_authenticated_owner(self):
        response = self._list_view(SubscriptionViewSet, self.owner)
        self.assertEqual(response.status_code, 200)
        ids = {row["id"] for row in self._rows(response)}
        self.assertIn(str(self.owner_subscription.id), ids)
        self.assertNotIn(str(self.other_subscription.id), ids)

    def test_staff_can_see_all_subscriptions(self):
        response = self._list_view(SubscriptionViewSet, self.staff)
        self.assertEqual(response.status_code, 200)
        ids = {row["id"] for row in self._rows(response)}
        self.assertIn(str(self.owner_subscription.id), ids)
        self.assertIn(str(self.other_subscription.id), ids)

    def test_tiers_shadow_users_are_staff_only_and_mask_password_hash(self):
        denied = self._list_view(UserViewSet, self.owner)
        self.assertEqual(denied.status_code, 403)

        response = self._list_view(UserViewSet, self.staff)
        self.assertEqual(response.status_code, 200)
        rows = self._rows(response)
        matched = [row for row in rows if row["id"] == str(self.shadow_user.id)]
        self.assertTrue(matched)
        self.assertNotIn("password_hash", matched[0])
