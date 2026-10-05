"""Tests for apps.billing.direct_payments.reconcile_direct_payment_callback's
idempotency/out-of-order handling against a MarketplaceOrder target - this
is the production (default) payment-webhook path for KIS Commerce checkout.
Existing coverage (tests.py, test_flutterwave_webhook_cross_reconciliation.py)
exercises signature validation and cross-system tx_ref matching, but not
duplicate delivery or out-of-order events against a real commerce order, so
those are the two gaps this file closes (spec phase: payment lifecycle)."""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.test import TestCase, override_settings

from apps.billing.direct_payments import reconcile_direct_payment_callback
from apps.billing.models import DirectPaymentAuditEvent, DirectPaymentIntent
from apps.commerce.models import MarketplaceOrder, MarketplaceOrderStatus, Product, Shop, ShopPayoutAccountStatus
from apps.commerce.services import place_marketplace_order
from apps.commerce.signals import on_product_save


def _disable_recommendation_signal(test_case):
    post_save.disconnect(on_product_save, sender=Product)
    test_case.addCleanup(post_save.connect, on_product_save, sender=Product)


@override_settings(FLW_WEBHOOK_SECRET="whsec_test")
class DirectPaymentWebhookIdempotencyTests(TestCase):
    def setUp(self):
        _disable_recommendation_signal(self)
        User = get_user_model()
        self.owner = User.objects.create_user(phone="5559060001", username="wh_owner", password="secret", country="NG")
        self.buyer = User.objects.create_user(phone="5559060002", username="wh_buyer", password="secret", country="NG")
        self.shop = Shop.objects.create(
            owner=self.owner, name="Webhook Shop", slug="webhook-shop",
            payout_account_status=ShopPayoutAccountStatus.ACTIVE, flutterwave_subaccount_id="RS_TEST_WH",
        )
        self.product = Product.objects.create(
            shop=self.shop, sku="WH-001", name="Webhook Widget", slug="webhook-widget",
            price=Decimal("40.00"), stock_qty=5, currency="USD",
        )
        # place_marketplace_order already provisions a pending
        # DirectPaymentIntent for the order itself (services.py's
        # payment_required branch - there's no wallet buyer_tx here, so a
        # provider intent is always created). Fetching that real intent,
        # rather than manually creating a second one, is required - a
        # second pending intent for the same target would violate
        # DirectPaymentIntent's own uniq_pending_direct_payment_target
        # constraint, which is exactly the kind of double-intent bug this
        # file's docstring is testing for everywhere else.
        self.order = place_marketplace_order(
            buyer=self.buyer, shop_id=self.shop.id,
            items=[{"product_id": str(self.product.id), "quantity": 1}],
        )
        self.intent = DirectPaymentIntent.objects.get(
            target_type=DirectPaymentIntent.TARGET_MARKETPLACE_ORDER,
            target_id=self.order.id,
            status=DirectPaymentIntent.STATUS_PENDING,
        )

    def _send(self, status_flag, provider_tx_id="flw-123"):
        return reconcile_direct_payment_callback(
            payload={"data": {"tx_ref": self.intent.tx_ref, "status": status_flag, "id": provider_tx_id}},
            signature="whsec_test",
        )

    def test_successful_webhook_moves_order_to_awaiting_satisfaction(self):
        ok, result, intent = self._send("successful")
        self.assertTrue(ok)
        self.assertEqual(result, "paid")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, MarketplaceOrderStatus.AWAITING_SATISFACTION)
        self.assertEqual(intent.status, DirectPaymentIntent.STATUS_PAID)

    def test_duplicate_successful_webhook_does_not_reprocess(self):
        self._send("successful")
        paid_events_after_first = DirectPaymentAuditEvent.objects.filter(
            intent=self.intent, event="callback.paid"
        ).count()
        self.assertEqual(paid_events_after_first, 1)

        ok, result, intent = self._send("successful")
        self.assertTrue(ok)
        self.assertEqual(result, "paid")
        # The second delivery must be logged as a duplicate, not reprocessed
        # into a second "paid" transition.
        self.assertEqual(
            DirectPaymentAuditEvent.objects.filter(intent=self.intent, event="callback.paid").count(), 1
        )
        self.assertEqual(
            DirectPaymentAuditEvent.objects.filter(intent=self.intent, event="callback.duplicate_paid").count(), 1
        )

    def test_late_failed_event_after_success_is_ignored_not_reverted(self):
        self._send("successful")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, MarketplaceOrderStatus.AWAITING_SATISFACTION)

        ok, result, intent = self._send("failed")
        self.assertTrue(ok)
        # Status stays "paid" from the webhook's point of view - a failed
        # event can't revert an already-settled payment.
        self.assertEqual(intent.status, DirectPaymentIntent.STATUS_PAID)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, MarketplaceOrderStatus.AWAITING_SATISFACTION)

    def test_out_of_order_pending_after_success_does_not_regress_status(self):
        self._send("successful")
        ok, result, intent = self._send("pending")
        self.assertTrue(ok)
        self.assertEqual(result, "ignored")
        self.assertEqual(intent.status, DirectPaymentIntent.STATUS_PAID)
