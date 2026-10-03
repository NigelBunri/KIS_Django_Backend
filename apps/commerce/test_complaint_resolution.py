"""Tests for the MarketplaceComplaint review/resolve lifecycle (closing the
dispute-resolution gap: previously PENDING/REVIEWED/RESOLVED existed only as
an enum with no API path to move off PENDING, and staff had no visibility
into complaints at all)."""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from rest_framework import status
from rest_framework.test import APITestCase

from .models import MarketplaceComplaint, Product, Shop, ShopPayoutAccountStatus
from .services import create_marketplace_complaint, place_marketplace_order
from .signals import on_product_save


def _disable_recommendation_signal(test_case):
    post_save.disconnect(on_product_save, sender=Product)
    test_case.addCleanup(post_save.connect, on_product_save, sender=Product)


def _make_shop(owner, key, **overrides):
    defaults = dict(
        owner=owner, name=f"{key} Shop", slug=f"{key}-shop",
        payout_account_status=ShopPayoutAccountStatus.ACTIVE,
        flutterwave_subaccount_id=f"RS_TEST_{key.upper()}",
    )
    defaults.update(overrides)
    return Shop.objects.create(**defaults)


class ComplaintResolutionAPITests(APITestCase):
    def setUp(self):
        _disable_recommendation_signal(self)
        User = get_user_model()
        self.owner = User.objects.create_user(phone="5559050001", username="cx_owner", password="secret", country="NG")
        self.other_owner = User.objects.create_user(phone="5559050002", username="cx_other_owner", password="secret", country="NG")
        self.staff = User.objects.create_user(phone="5559050003", username="cx_staff", password="secret", country="NG", is_staff=True)
        self.buyer = User.objects.create_user(phone="5559050004", username="cx_buyer", password="secret", country="NG")
        self.shop = _make_shop(self.owner, "cxtest")
        self.product = Product.objects.create(
            shop=self.shop, sku="CX-001", name="Complaint Widget", slug="complaint-widget",
            price=Decimal("10.00"), stock_qty=5, currency="USD",
        )
        self.order = place_marketplace_order(
            buyer=self.buyer, shop_id=self.shop.id,
            items=[{"product_id": str(self.product.id), "quantity": 1}],
        )
        self.complaint = create_marketplace_complaint(order=self.order, user=self.buyer, text="Item never arrived.")

    def test_seller_can_resolve(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(f"/api/v1/commerce/marketplace-complaints/{self.complaint.id}/resolve/", {
            "resolution_notes": "Reshipped the item at no cost.",
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["status"], "resolved")
        self.assertEqual(response.data["resolution_notes"], "Reshipped the item at no cost.")
        self.complaint.refresh_from_db()
        self.assertEqual(self.complaint.resolved_by_id, self.owner.id)
        self.assertIsNotNone(self.complaint.resolved_at)

    def test_staff_can_review_and_resolve(self):
        self.client.force_authenticate(self.staff)
        review = self.client.post(f"/api/v1/commerce/marketplace-complaints/{self.complaint.id}/review/", {}, format="json")
        self.assertEqual(review.status_code, status.HTTP_200_OK, review.data)
        self.assertEqual(review.data["status"], "reviewed")
        resolve = self.client.post(f"/api/v1/commerce/marketplace-complaints/{self.complaint.id}/resolve/", {
            "resolution_notes": "Platform mediated a refund.",
        }, format="json")
        self.assertEqual(resolve.status_code, status.HTTP_200_OK, resolve.data)

    def test_buyer_cannot_resolve_own_complaint(self):
        self.client.force_authenticate(self.buyer)
        response = self.client.post(f"/api/v1/commerce/marketplace-complaints/{self.complaint.id}/resolve/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unrelated_seller_gets_404_not_403(self):
        self.client.force_authenticate(self.other_owner)
        response = self.client.post(f"/api/v1/commerce/marketplace-complaints/{self.complaint.id}/resolve/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_unrelated_seller_does_not_see_complaint_in_list(self):
        self.client.force_authenticate(self.other_owner)
        response = self.client.get("/api/v1/commerce/marketplace-complaints/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {row["id"] for row in response.data}
        self.assertNotIn(str(self.complaint.id), ids)

    def test_staff_sees_complaint_in_list(self):
        self.client.force_authenticate(self.staff)
        response = self.client.get("/api/v1/commerce/marketplace-complaints/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {row["id"] for row in response.data}
        self.assertIn(str(self.complaint.id), ids)
