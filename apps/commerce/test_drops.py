"""Tests for the Market Drops endpoint (MarketDropViewSet, MarketDrop
model) - closing the "Browse limited-time Market Drops" gap, which was
previously a hardcoded empty list in CommerceDiscoveryView. See
drops_models.py's docstring for why `is_live` is computed, not stored."""
from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from .models import MarketDrop, Product, Shop, ShopPayoutAccountStatus
from .signals import on_product_save


def _disable_recommendation_signal(test_case):
    post_save.disconnect(on_product_save, sender=Product)
    test_case.addCleanup(post_save.connect, on_product_save, sender=Product)


class MarketDropViewSetTests(APITestCase):
    def setUp(self):
        _disable_recommendation_signal(self)
        User = get_user_model()
        self.owner = User.objects.create_user(phone="5559030001", username="drop_owner", password="secret", country="NG")
        self.other_owner = User.objects.create_user(phone="5559030002", username="drop_other_owner", password="secret", country="NG")
        self.shopper = User.objects.create_user(phone="5559030003", username="drop_shopper", password="secret", country="NG")
        self.shop = Shop.objects.create(owner=self.owner, name="Drop Shop", slug="drop-shop", payout_account_status=ShopPayoutAccountStatus.ACTIVE)
        self.other_shop = Shop.objects.create(owner=self.other_owner, name="Other Shop", slug="other-shop", payout_account_status=ShopPayoutAccountStatus.ACTIVE)
        self.product = Product.objects.create(shop=self.shop, name="Dropped Item", price=10, stock_qty=5)

    def test_list_shows_live_and_upcoming_not_long_ended(self):
        now = timezone.now()
        live = MarketDrop.objects.create(shop=self.shop, title="Live Drop", starts_at=now - timedelta(minutes=5), ends_at=now + timedelta(hours=1))
        upcoming = MarketDrop.objects.create(shop=self.shop, title="Upcoming Drop", starts_at=now + timedelta(hours=2), ends_at=now + timedelta(hours=3))
        MarketDrop.objects.create(shop=self.shop, title="Long Ended", starts_at=now - timedelta(days=10), ends_at=now - timedelta(days=9))
        live.products.add(self.product)

        self.client.force_authenticate(self.shopper)
        resp = self.client.get(reverse("drops-list"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data.get("results", resp.data)
        ids = {r["id"] for r in results}
        self.assertIn(str(live.id), ids)
        self.assertIn(str(upcoming.id), ids)
        self.assertEqual(len(results), 2)

        live_row = next(r for r in results if r["id"] == str(live.id))
        self.assertTrue(live_row["is_live"])
        self.assertEqual(live_row["product_ids"], [str(self.product.id)])
        upcoming_row = next(r for r in results if r["id"] == str(upcoming.id))
        self.assertFalse(upcoming_row["is_live"])

    def test_owner_can_create_drop_for_own_shop(self):
        now = timezone.now()
        self.client.force_authenticate(self.owner)
        resp = self.client.post(reverse("drops-list"), {
            "shop": str(self.shop.id),
            "title": "New Drop",
            "starts_at": (now + timedelta(hours=1)).isoformat(),
            "ends_at": (now + timedelta(hours=2)).isoformat(),
            "product_ids": [str(self.product.id)],
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        drop = MarketDrop.objects.get(id=resp.data["id"])
        self.assertEqual(drop.shop_id, self.shop.id)
        self.assertEqual(list(drop.products.all()), [self.product])

    def test_cannot_create_drop_for_a_shop_you_do_not_own(self):
        now = timezone.now()
        self.client.force_authenticate(self.other_owner)
        resp = self.client.post(reverse("drops-list"), {
            "shop": str(self.shop.id),
            "title": "Hijack Drop",
            "starts_at": (now + timedelta(hours=1)).isoformat(),
            "ends_at": (now + timedelta(hours=2)).isoformat(),
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_end_before_start_is_rejected(self):
        now = timezone.now()
        self.client.force_authenticate(self.owner)
        resp = self.client.post(reverse("drops-list"), {
            "shop": str(self.shop.id),
            "title": "Backwards Drop",
            "starts_at": (now + timedelta(hours=2)).isoformat(),
            "ends_at": (now + timedelta(hours=1)).isoformat(),
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_owner_cannot_update_someone_elses_drop(self):
        now = timezone.now()
        drop = MarketDrop.objects.create(shop=self.shop, title="Owned Drop", starts_at=now, ends_at=now + timedelta(hours=1))
        self.client.force_authenticate(self.other_owner)
        resp = self.client.patch(reverse("drops-detail", args=[drop.id]), {"title": "Hijacked"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_discovery_feed_includes_real_drops_not_hardcoded_empty(self):
        now = timezone.now()
        live = MarketDrop.objects.create(shop=self.shop, title="Feed Drop", starts_at=now - timedelta(minutes=1), ends_at=now + timedelta(hours=1))
        self.client.force_authenticate(self.shopper)
        resp = self.client.get(reverse("commerce-discovery"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        drop_ids = {d["id"] for d in resp.data["drops"]}
        self.assertIn(str(live.id), drop_ids)
        self.assertEqual(resp.data["featured_drop"]["id"], str(live.id))
