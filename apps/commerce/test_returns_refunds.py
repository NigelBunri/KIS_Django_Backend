"""Tests for the Returns/RMA + Refunds system (returns_models.py,
returns_services.py, returns_views.py) - closing the Returns/Refunds gap
identified in the mega-shop capability audit. See returns_models.py's
docstring for the state machine this exercises."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APITestCase

from .models import MarketplaceOrderItem, Product, Shop, ShopPayoutAccountStatus
from .services import place_marketplace_order
from .signals import on_product_save
from .shipping_models import Fulfillment, FulfillmentStatus
from .returns_models import Refund, RefundStatus, ReturnItem, ReturnRequest, ReturnStatus
from .returns_services import create_refund, create_return_request, transition_return


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


def _satisfy(order, *, delivered_days_ago=None):
    """Shortcut to a SATISFIED order for tests that aren't exercising the
    payment lifecycle itself - same shortcut test_reviews_search.py uses."""
    order.status = "satisfied"
    order.save(update_fields=["status"])
    if delivered_days_ago is not None:
        fulfillment = Fulfillment.objects.create(
            order=order, shop=order.shop, status=FulfillmentStatus.DELIVERED,
            delivered_at=timezone.now() - timezone.timedelta(days=delivered_days_ago),
        )
        return fulfillment
    return None


def _paid_intent(order, *, provider="flutterwave", amount_cents=None):
    from apps.billing.models import DirectPaymentIntent
    return DirectPaymentIntent.objects.create(
        user=order.buyer, provider=provider,
        target_type=DirectPaymentIntent.TARGET_MARKETPLACE_ORDER, target_id=order.id,
        amount_cents=amount_cents if amount_cents is not None else int(order.total_amount * 100),
        status=DirectPaymentIntent.STATUS_PAID, tx_ref=f"tx-{order.id}", provider_ref=f"flw-{order.id}",
    )


class ReturnEligibilityTests(TestCase):
    def setUp(self):
        _disable_recommendation_signal(self)
        User = get_user_model()
        self.owner = User.objects.create_user(phone="5559010001", username="ret_owner", password="secret", country="NG")
        self.buyer = User.objects.create_user(phone="5559010002", username="ret_buyer", password="secret", country="NG")
        self.shop = _make_shop(self.owner, "rettest")
        self.product = Product.objects.create(
            shop=self.shop, sku="RET-001", name="Returnable Widget", slug="returnable-widget",
            price=Decimal("20.00"), stock_qty=10, currency="USD",
        )

    def _place_order(self, qty=2):
        return place_marketplace_order(
            buyer=self.buyer, shop_id=self.shop.id,
            items=[{"product_id": str(self.product.id), "quantity": qty}],
        )

    def test_unpaid_order_not_eligible(self):
        order = self._place_order()
        with self.assertRaises(ValidationError):
            create_return_request(
                order_id=order.id, buyer=self.buyer, reason="damaged",
                items=[{"order_item_id": str(order.items.first().id), "quantity": 1}],
            )

    def test_satisfied_order_eligible(self):
        order = self._place_order()
        _satisfy(order)
        item = order.items.first()
        rr = create_return_request(
            order_id=order.id, buyer=self.buyer, reason="damaged",
            items=[{"order_item_id": str(item.id), "quantity": 1}],
        )
        self.assertEqual(rr.status, ReturnStatus.REQUESTED)
        self.assertEqual(rr.items.first().quantity, 1)

    def test_outside_return_window_rejected(self):
        order = self._place_order()
        _satisfy(order, delivered_days_ago=45)
        item = order.items.first()
        with self.assertRaises(ValidationError):
            create_return_request(
                order_id=order.id, buyer=self.buyer, reason="damaged",
                items=[{"order_item_id": str(item.id), "quantity": 1}],
            )

    def test_within_return_window_allowed(self):
        order = self._place_order()
        _satisfy(order, delivered_days_ago=10)
        item = order.items.first()
        rr = create_return_request(
            order_id=order.id, buyer=self.buyer, reason="damaged",
            items=[{"order_item_id": str(item.id), "quantity": 1}],
        )
        self.assertEqual(rr.status, ReturnStatus.REQUESTED)

    def test_cannot_return_more_than_ordered(self):
        order = self._place_order(qty=2)
        _satisfy(order)
        item = order.items.first()
        with self.assertRaises(ValidationError):
            create_return_request(
                order_id=order.id, buyer=self.buyer, reason="damaged",
                items=[{"order_item_id": str(item.id), "quantity": 3}],
            )

    def test_cannot_overcommit_across_two_requests(self):
        order = self._place_order(qty=2)
        _satisfy(order)
        item = order.items.first()
        create_return_request(
            order_id=order.id, buyer=self.buyer, reason="damaged",
            items=[{"order_item_id": str(item.id), "quantity": 2}],
        )
        with self.assertRaises(ValidationError):
            create_return_request(
                order_id=order.id, buyer=self.buyer, reason="damaged",
                items=[{"order_item_id": str(item.id), "quantity": 1}],
            )

    def test_rejected_request_frees_up_quantity_for_a_new_one(self):
        order = self._place_order(qty=2)
        _satisfy(order)
        item = order.items.first()
        first = create_return_request(
            order_id=order.id, buyer=self.buyer, reason="damaged",
            items=[{"order_item_id": str(item.id), "quantity": 2}],
        )
        transition_return(return_request_id=first.id, actor=self.owner, target_status=ReturnStatus.REJECTED, notes="not eligible")
        second = create_return_request(
            order_id=order.id, buyer=self.buyer, reason="damaged",
            items=[{"order_item_id": str(item.id), "quantity": 2}],
        )
        self.assertEqual(second.status, ReturnStatus.REQUESTED)

    def test_other_buyers_order_item_cannot_be_targeted(self):
        order = self._place_order()
        _satisfy(order)
        other_buyer = get_user_model().objects.create_user(phone="5559010099", username="ret_other_buyer", password="secret", country="NG")
        other_order = place_marketplace_order(
            buyer=other_buyer, shop_id=self.shop.id,
            items=[{"product_id": str(self.product.id), "quantity": 1}],
        )
        _satisfy(other_order)
        other_item = other_order.items.first()
        with self.assertRaises(ValidationError):
            # order_id belongs to self.buyer's order, but the order_item_id
            # belongs to someone else's order entirely.
            create_return_request(
                order_id=order.id, buyer=self.buyer, reason="damaged",
                items=[{"order_item_id": str(other_item.id), "quantity": 1}],
            )


class ReturnStateMachineTests(TestCase):
    def setUp(self):
        _disable_recommendation_signal(self)
        User = get_user_model()
        self.owner = User.objects.create_user(phone="5559020001", username="sm_owner", password="secret", country="NG")
        self.other_owner = User.objects.create_user(phone="5559020002", username="sm_other_owner", password="secret", country="NG")
        self.buyer = User.objects.create_user(phone="5559020003", username="sm_buyer", password="secret", country="NG")
        self.shop = _make_shop(self.owner, "smtest")
        self.other_shop = _make_shop(self.other_owner, "smtestother")
        self.product = Product.objects.create(
            shop=self.shop, sku="SM-001", name="State Machine Widget", slug="sm-widget",
            price=Decimal("30.00"), stock_qty=5, currency="USD",
        )
        self.order = place_marketplace_order(
            buyer=self.buyer, shop_id=self.shop.id,
            items=[{"product_id": str(self.product.id), "quantity": 1}],
        )
        _satisfy(self.order)
        self.item = self.order.items.first()
        self.rr = create_return_request(
            order_id=self.order.id, buyer=self.buyer, reason="damaged",
            items=[{"order_item_id": str(self.item.id), "quantity": 1}],
        )

    def test_buyer_cannot_approve_own_return(self):
        with self.assertRaises(PermissionDenied):
            transition_return(return_request_id=self.rr.id, actor=self.buyer, target_status=ReturnStatus.APPROVED)

    def test_seller_cannot_ship_buyers_return(self):
        transition_return(return_request_id=self.rr.id, actor=self.owner, target_status=ReturnStatus.APPROVED)
        with self.assertRaises(PermissionDenied):
            transition_return(return_request_id=self.rr.id, actor=self.owner, target_status=ReturnStatus.RETURN_SHIPPED)

    def test_unrelated_seller_cannot_act_on_this_return(self):
        with self.assertRaises(PermissionDenied):
            transition_return(return_request_id=self.rr.id, actor=self.other_owner, target_status=ReturnStatus.APPROVED)

    def test_cannot_skip_states(self):
        # REQUESTED -> RECEIVED directly is not a legal transition.
        with self.assertRaises(ValidationError):
            transition_return(return_request_id=self.rr.id, actor=self.owner, target_status=ReturnStatus.RECEIVED)

    def test_cannot_act_on_terminal_request(self):
        transition_return(return_request_id=self.rr.id, actor=self.owner, target_status=ReturnStatus.REJECTED, notes="no")
        with self.assertRaises(ValidationError):
            transition_return(return_request_id=self.rr.id, actor=self.owner, target_status=ReturnStatus.APPROVED)

    def test_full_happy_path_restocks_and_refunds(self):
        _paid_intent(self.order)
        starting_stock = Product.objects.get(id=self.product.id).stock_qty

        transition_return(return_request_id=self.rr.id, actor=self.owner, target_status=ReturnStatus.APPROVED)
        transition_return(
            return_request_id=self.rr.id, actor=self.buyer, target_status=ReturnStatus.RETURN_SHIPPED,
            carrier_name="DHL", tracking_number="TRACK123",
        )
        transition_return(return_request_id=self.rr.id, actor=self.owner, target_status=ReturnStatus.RECEIVED)

        with patch("apps.billing.direct_payments.refund_flutterwave_transaction") as mock_refund:
            mock_refund.return_value = {"id": "flw-refund-1"}
            completed = transition_return(return_request_id=self.rr.id, actor=self.owner, target_status=ReturnStatus.COMPLETED)

        self.assertEqual(completed.status, ReturnStatus.COMPLETED)
        self.assertIsNotNone(completed.completed_at)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_qty, starting_stock + 1)

        refund = completed.refunds.first()
        self.assertIsNotNone(refund)
        self.assertEqual(refund.status, RefundStatus.SUCCEEDED)
        self.assertEqual(refund.amount_cents, self.item.unit_price_cents * 1)
        mock_refund.assert_called_once()

    def test_post_inspection_rejection_does_not_restock_or_refund(self):
        _paid_intent(self.order)
        starting_stock = Product.objects.get(id=self.product.id).stock_qty
        transition_return(return_request_id=self.rr.id, actor=self.owner, target_status=ReturnStatus.APPROVED)
        transition_return(return_request_id=self.rr.id, actor=self.buyer, target_status=ReturnStatus.RETURN_SHIPPED)
        transition_return(return_request_id=self.rr.id, actor=self.owner, target_status=ReturnStatus.RECEIVED)
        rejected = transition_return(
            return_request_id=self.rr.id, actor=self.owner, target_status=ReturnStatus.REJECTED,
            notes="Item was not actually damaged on inspection.",
        )
        self.assertEqual(rejected.status, ReturnStatus.REJECTED)
        self.assertIsNotNone(rejected.inspected_at)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_qty, starting_stock)
        self.assertEqual(rejected.refunds.count(), 0)

    def test_buyer_can_cancel_before_shipment(self):
        cancelled = transition_return(return_request_id=self.rr.id, actor=self.buyer, target_status=ReturnStatus.CANCELLED)
        self.assertEqual(cancelled.status, ReturnStatus.CANCELLED)


class RefundServiceTests(TestCase):
    def setUp(self):
        _disable_recommendation_signal(self)
        User = get_user_model()
        self.owner = User.objects.create_user(phone="5559030001", username="rf_owner", password="secret", country="NG")
        self.buyer = User.objects.create_user(phone="5559030002", username="rf_buyer", password="secret", country="NG")
        self.shop = _make_shop(self.owner, "rftest")
        self.product = Product.objects.create(
            shop=self.shop, sku="RF-001", name="Refund Widget", slug="refund-widget",
            price=Decimal("50.00"), stock_qty=5, currency="USD",
        )
        self.order = place_marketplace_order(
            buyer=self.buyer, shop_id=self.shop.id,
            items=[{"product_id": str(self.product.id), "quantity": 1}],
        )
        _satisfy(self.order)
        _paid_intent(self.order, amount_cents=5000)

    def test_refund_is_idempotent_by_key(self):
        with patch("apps.billing.direct_payments.refund_flutterwave_transaction", return_value={"id": "x"}):
            first = create_refund(order=self.order, amount_cents=2000, reason="goodwill", idempotency_key="k-1")
            second = create_refund(order=self.order, amount_cents=2000, reason="goodwill", idempotency_key="k-1")
        self.assertEqual(first.id, second.id)
        self.assertEqual(Refund.objects.filter(idempotency_key="k-1").count(), 1)

    def test_refund_cannot_exceed_amount_paid(self):
        with patch("apps.billing.direct_payments.refund_flutterwave_transaction", return_value={"id": "x"}):
            create_refund(order=self.order, amount_cents=4000, reason="goodwill", idempotency_key="k-2")
            with self.assertRaises(ValidationError):
                create_refund(order=self.order, amount_cents=2000, reason="goodwill", idempotency_key="k-3")

    def test_failed_provider_refund_recorded_as_failed_not_succeeded(self):
        with patch("apps.billing.direct_payments.refund_flutterwave_transaction", side_effect=ValueError("provider down")):
            refund = create_refund(order=self.order, amount_cents=1000, reason="goodwill", idempotency_key="k-4")
        self.assertEqual(refund.status, RefundStatus.FAILED)
        self.assertIn("provider down", refund.failure_reason)
        # A failed refund must not count against the remaining refundable
        # amount - a second attempt for the same money must still be
        # possible.
        with patch("apps.billing.direct_payments.refund_flutterwave_transaction", return_value={"id": "ok"}):
            retried = create_refund(order=self.order, amount_cents=1000, reason="goodwill", idempotency_key="k-5")
        self.assertEqual(retried.status, RefundStatus.SUCCEEDED)


class ReturnRequestAPITests(APITestCase):
    def setUp(self):
        _disable_recommendation_signal(self)
        User = get_user_model()
        self.owner = User.objects.create_user(phone="5559040001", username="api_owner", password="secret", country="NG")
        self.other_owner = User.objects.create_user(phone="5559040002", username="api_other_owner", password="secret", country="NG")
        self.buyer = User.objects.create_user(phone="5559040003", username="api_buyer", password="secret", country="NG")
        self.shop = _make_shop(self.owner, "apitest")
        self.other_shop = _make_shop(self.other_owner, "apitestother")
        self.product = Product.objects.create(
            shop=self.shop, sku="API-001", name="API Widget", slug="api-widget",
            price=Decimal("25.00"), stock_qty=5, currency="USD",
        )
        self.order = place_marketplace_order(
            buyer=self.buyer, shop_id=self.shop.id,
            items=[{"product_id": str(self.product.id), "quantity": 1}],
        )
        _satisfy(self.order)
        self.item = self.order.items.first()

    def test_create_return_via_api(self):
        self.client.force_authenticate(self.buyer)
        response = self.client.post("/api/v1/commerce/returns/", {
            "order_id": str(self.order.id), "reason": "damaged",
            "items": [{"order_item_id": str(self.item.id), "quantity": 1}],
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_unrelated_user_gets_404_not_403(self):
        rr = create_return_request(
            order_id=self.order.id, buyer=self.buyer, reason="damaged",
            items=[{"order_item_id": str(self.item.id), "quantity": 1}],
        )
        unrelated = get_user_model().objects.create_user(phone="5559040099", username="api_unrelated", password="secret", country="NG")
        self.client.force_authenticate(unrelated)
        response = self.client.post(f"/api/v1/commerce/returns/{rr.id}/approve/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_seller_sees_and_approves_via_api(self):
        rr = create_return_request(
            order_id=self.order.id, buyer=self.buyer, reason="damaged",
            items=[{"order_item_id": str(self.item.id), "quantity": 1}],
        )
        self.client.force_authenticate(self.owner)
        response = self.client.post(f"/api/v1/commerce/returns/{rr.id}/approve/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["status"], "approved")
