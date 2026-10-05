"""Tests for the Invoice list/detail endpoints (InvoiceListView/
InvoiceDetailView in views.py, InvoiceSerializer in serializers.py) -
closing the "View and use invoices" gap identified in the mega-shop
capability audit. There is deliberately no stored Invoice model: these
views are a read-only, buyer-scoped presentation over paid
MarketplaceOrder rows, so money has one source of truth. See
InvoiceSerializer's docstring."""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Product, Shop, ShopPayoutAccountStatus
from .services import place_marketplace_order
from .signals import on_product_save
from .test_returns_refunds import _satisfy


def _disable_recommendation_signal(test_case):
    post_save.disconnect(on_product_save, sender=Product)
    test_case.addCleanup(post_save.connect, on_product_save, sender=Product)


class InvoiceListViewTests(APITestCase):
    def setUp(self):
        _disable_recommendation_signal(self)
        User = get_user_model()
        self.owner = User.objects.create_user(phone="5559020001", username="inv_owner", password="secret", country="NG")
        self.buyer = User.objects.create_user(phone="5559020002", username="inv_buyer", password="secret", country="NG")
        self.other_buyer = User.objects.create_user(phone="5559020003", username="inv_other", password="secret", country="NG")
        self.shop = Shop.objects.create(
            owner=self.owner, name="Invoice Shop", slug="invoice-shop",
            payout_account_status=ShopPayoutAccountStatus.ACTIVE,
            flutterwave_subaccount_id="RS_TEST_INVOICE",
        )
        self.product = Product.objects.create(
            shop=self.shop, sku="INV-001", name="Invoiceable Widget", slug="invoiceable-widget",
            price=Decimal("50.00"), stock_qty=10, currency="USD",
        )

    def _place_order(self, buyer, qty=1):
        return place_marketplace_order(
            buyer=buyer, shop_id=self.shop.id,
            items=[{"product_id": str(self.product.id), "quantity": qty}],
        )

    def test_unauthenticated_request_rejected(self):
        resp = self.client.get(reverse("commerce-invoices"))
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_unpaid_order_excluded_from_list(self):
        self._place_order(self.buyer)
        self.client.force_authenticate(self.buyer)
        resp = self.client.get(reverse("commerce-invoices"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data.get("results", resp.data)
        self.assertEqual(len(results), 0)

    def test_paid_order_appears_as_invoice_with_expected_shape(self):
        order = self._place_order(self.buyer)
        _satisfy(order)
        self.client.force_authenticate(self.buyer)
        resp = self.client.get(reverse("commerce-invoices"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data.get("results", resp.data)
        self.assertEqual(len(results), 1)
        invoice = results[0]
        self.assertEqual(invoice["id"], str(order.id))
        self.assertTrue(invoice["invoice_number"].startswith("INV-"))
        self.assertEqual(invoice["status"], "paid")
        self.assertEqual(Decimal(invoice["amount"]), order.total_amount)
        self.assertEqual(invoice["issuer"]["id"], str(self.shop.id))
        self.assertEqual(invoice["recipient"]["id"], str(self.buyer.id))
        self.assertEqual(len(invoice["items"]), 1)
        self.assertIsNotNone(invoice["download_url"])
        self.assertIn(f"/marketplace-orders/{order.id}/receipt/", invoice["download_url"])

    def test_buyer_cannot_see_another_buyers_invoice(self):
        order = self._place_order(self.buyer)
        _satisfy(order)
        self.client.force_authenticate(self.other_buyer)
        resp = self.client.get(reverse("commerce-invoices"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data.get("results", resp.data)
        self.assertEqual(len(results), 0)
        detail_resp = self.client.get(reverse("commerce-invoice-detail", args=[order.id]))
        self.assertEqual(detail_resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_buyer_can_retrieve_own_invoice_detail(self):
        order = self._place_order(self.buyer)
        _satisfy(order)
        self.client.force_authenticate(self.buyer)
        resp = self.client.get(reverse("commerce-invoice-detail", args=[order.id]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["id"], str(order.id))
