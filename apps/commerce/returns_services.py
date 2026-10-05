"""
Returns/RMA + Refunds service layer for apps.commerce.

Lifecycle (see returns_models.py for the full docstring/state machine):

    ReturnRequest: REQUESTED -> APPROVED -> RETURN_SHIPPED -> RECEIVED
                   -> COMPLETED (refund issued, stock restocked)
                                -> REJECTED (post-inspection failure)
    REQUESTED/APPROVED can also go to REJECTED (seller declines before
    shipment) or CANCELLED (buyer backs out before shipment).

Refund execution reuses the existing payment abstraction rather than
inventing a second one:
  - provider-paid orders (Flutterwave, the default/production rail) refund
    through apps.billing.direct_payments.refund_flutterwave_transaction,
    a real server-to-server call against Flutterwave's refund endpoint.
  - legacy wallet-escrow orders (feature-flagged off by default) can only
    be refunded here while still in escrow (TEMPORAL/AWAITING_SATISFACTION)
    via the existing refund_locked_booking_funds - once satisfied, those
    funds have already been released to the seller's wallet ledger and a
    true clawback/reversal mechanism does not exist in this codebase. This
    is a documented, honest limitation of the disabled legacy rail, not a
    silently-wrong refund.
  - Stripe-paid orders: no refund wrapper exists yet in apps.billing: this
    function returns a FAILED refund with a clear failure_reason rather
    than fabricating success.
"""
from __future__ import annotations

import uuid
from typing import Iterable

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.notifications.services import create_notification

from .models import MarketplaceOrder, MarketplaceOrderItem, MarketplaceOrderStatus, Product
from .returns_models import (
    Refund,
    RefundReason,
    RefundStatus,
    RETURN_TRANSITIONS,
    RETURN_WINDOW_DAYS,
    ReturnItem,
    ReturnReason,
    ReturnRequest,
    ReturnStatus,
)

ELIGIBLE_ORDER_STATUSES = {MarketplaceOrderStatus.SATISFIED, MarketplaceOrderStatus.COMPLETED}


def _provider_can_manage_shop_or_staff(user, shop) -> bool:
    from .services import _provider_can_manage_shop
    return bool(getattr(user, "is_staff", False)) or _provider_can_manage_shop(user, shop)


def _non_terminal_returned_quantity(order_item_id) -> int:
    """Sum of quantities already committed against this order_item by a
    ReturnRequest that hasn't been rejected/cancelled - the aggregate a
    bare Postgres CHECK constraint can't express (same pattern
    ShipmentItem/_shipped_quantity_for_item document)."""
    return (
        ReturnItem.objects.filter(order_item_id=order_item_id)
        .exclude(return_request__status__in=[ReturnStatus.REJECTED, ReturnStatus.CANCELLED])
        .aggregate(total=Sum("quantity"))
        .get("total") or 0
    )


def create_return_request(*, order_id, buyer, reason, explanation="", items: Iterable[dict]):
    """items: [{"order_item_id": ..., "quantity": ...}, ...]. Locks the
    referenced MarketplaceOrderItem rows for the duration of this
    transaction so two concurrent return requests against the same item
    can't both read a stale already-returned total and both overcommit."""
    items = list(items or [])
    if not items:
        raise ValidationError("At least one order item is required to request a return.")

    order = MarketplaceOrder.objects.filter(id=order_id, buyer=buyer).first()
    if not order:
        raise ValidationError("Marketplace order not found.")
    if order.status not in ELIGIBLE_ORDER_STATUSES:
        raise ValidationError("Only satisfied or completed orders are eligible for a return.")

    fulfillment = getattr(order, "fulfillment", None)
    delivered_at = getattr(fulfillment, "delivered_at", None)
    if delivered_at is not None:
        window_end = delivered_at + timezone.timedelta(days=RETURN_WINDOW_DAYS)
        if timezone.now() > window_end:
            raise ValidationError(
                f"The {RETURN_WINDOW_DAYS}-day return window for this order has closed."
            )

    with transaction.atomic():
        order_item_ids = [str(entry.get("order_item_id") or "") for entry in items]
        locked_items = {
            str(oi.id): oi
            for oi in MarketplaceOrderItem.objects.filter(
                id__in=order_item_ids, order=order
            ).select_for_update()
        }
        if len(locked_items) != len(set(order_item_ids)):
            raise ValidationError("One or more order items are invalid for this order.")

        return_request = ReturnRequest.objects.create(
            order=order, buyer=buyer, reason=reason, explanation=explanation
        )
        for entry in items:
            order_item = locked_items[str(entry.get("order_item_id"))]
            quantity = max(1, int(entry.get("quantity") or 1))
            already_returned = _non_terminal_returned_quantity(order_item.id)
            if already_returned + quantity > order_item.quantity:
                raise ValidationError(
                    f"Cannot return {quantity}x {order_item.product.name}: only "
                    f"{order_item.quantity - already_returned} unit(s) are still eligible."
                )
            ReturnItem.objects.create(return_request=return_request, order_item=order_item, quantity=quantity)

    _notify_return_status(return_request, audience="seller")
    return return_request


def _assert_transition_allowed(return_request, target_status):
    allowed = RETURN_TRANSITIONS.get(return_request.status, set())
    if target_status not in allowed:
        raise ValidationError(
            f"Cannot move a return from {return_request.status} to {target_status}."
        )


def _restock_returned_items(return_request):
    """Return restocking (spec Phase 3): PHYSICAL products get their
    stock_qty incremented back for every returned unit, locked the same
    way _reserve_stock_for_items/_release_stock_for_order lock Product
    rows in services.py."""
    return_items = list(return_request.items.select_related("order_item__product").all())
    product_ids = {ri.order_item.product_id for ri in return_items}
    if not product_ids:
        return
    locked_products = {p.id: p for p in Product.objects.filter(id__in=product_ids).select_for_update()}
    required_by_product: dict = {}
    for ri in return_items:
        product = locked_products.get(ri.order_item.product_id)
        if not product or product.inventory_type != "PHYSICAL":
            continue
        required_by_product[product.id] = required_by_product.get(product.id, 0) + ri.quantity
    for product_id, qty in required_by_product.items():
        product = locked_products[product_id]
        product.stock_qty = int(product.stock_qty or 0) + qty
        product.save(update_fields=["stock_qty"])


def _return_refund_amount_cents(return_request) -> int:
    total = 0
    for ri in return_request.items.select_related("order_item").all():
        total += ri.order_item.unit_price_cents * ri.quantity
    return total


def transition_return(*, return_request_id, actor, target_status, notes="", carrier_name="", tracking_number=""):
    return_request = ReturnRequest.objects.select_related("order", "order__shop").filter(
        id=return_request_id
    ).first()
    if not return_request:
        raise ValidationError("Return request not found.")

    is_buyer = actor.id == return_request.buyer_id
    is_seller = _provider_can_manage_shop_or_staff(actor, return_request.order.shop)
    buyer_only_targets = {ReturnStatus.RETURN_SHIPPED, ReturnStatus.CANCELLED}
    seller_only_targets = {ReturnStatus.APPROVED, ReturnStatus.REJECTED, ReturnStatus.RECEIVED, ReturnStatus.COMPLETED}
    if target_status in buyer_only_targets and not is_buyer:
        raise PermissionDenied("Only the buyer can perform this action.")
    if target_status in seller_only_targets and not is_seller:
        raise PermissionDenied("Only the seller or staff can perform this action.")
    if not (is_buyer or is_seller):
        raise PermissionDenied("You do not have access to this return request.")

    _assert_transition_allowed(return_request, target_status)

    with transaction.atomic():
        return_request = ReturnRequest.objects.select_for_update().get(id=return_request.id)
        now = timezone.now()
        return_request.status = target_status
        update_fields = ["status", "updated_at"]

        if target_status in (ReturnStatus.APPROVED, ReturnStatus.REJECTED) and return_request.reviewed_at is None:
            return_request.reviewed_by = actor
            return_request.reviewed_at = now
            update_fields += ["reviewed_by", "reviewed_at"]
        if target_status == ReturnStatus.APPROVED and notes:
            return_request.seller_notes = notes
            update_fields.append("seller_notes")
        if target_status == ReturnStatus.REJECTED:
            return_request.rejection_reason = notes
            update_fields.append("rejection_reason")
        if target_status == ReturnStatus.RETURN_SHIPPED:
            return_request.return_carrier_name = carrier_name
            return_request.return_tracking_number = tracking_number
            return_request.return_shipped_at = now
            update_fields += ["return_carrier_name", "return_tracking_number", "return_shipped_at"]
        if target_status == ReturnStatus.RECEIVED:
            return_request.received_at = now
            update_fields.append("received_at")
        # A REJECTED/COMPLETED transition only follows a post-inspection
        # review when the request already has a received_at (i.e. it came
        # via RECEIVED, not directly from REQUESTED as a pre-shipment
        # decline) - received_at was either already set in a prior call or
        # just set above in this same call.
        if target_status in (ReturnStatus.COMPLETED, ReturnStatus.REJECTED) and return_request.received_at:
            return_request.inspected_at = now
            return_request.inspected_by = actor
            update_fields += ["inspected_at", "inspected_by"]
            if target_status == ReturnStatus.REJECTED:
                return_request.rejection_reason = notes
                if "rejection_reason" not in update_fields:
                    update_fields.append("rejection_reason")
        if target_status == ReturnStatus.COMPLETED:
            return_request.completed_at = now
            update_fields.append("completed_at")

        return_request.save(update_fields=update_fields)

        if target_status == ReturnStatus.COMPLETED:
            _restock_returned_items(return_request)
            amount_cents = _return_refund_amount_cents(return_request)
            create_refund(
                order=return_request.order,
                amount_cents=amount_cents,
                reason=RefundReason.RETURN,
                idempotency_key=f"return-{return_request.id}",
                actor=actor,
                return_request=return_request,
            )

    _notify_return_status(return_request, audience="buyer" if is_seller else "seller")
    return return_request


# ---------------------------------------------------------------------------
# Refunds
# ---------------------------------------------------------------------------

def _total_paid_cents(order) -> int:
    from apps.billing.models import DirectPaymentIntent
    intent = (
        DirectPaymentIntent.objects.filter(
            target_type=DirectPaymentIntent.TARGET_MARKETPLACE_ORDER,
            target_id=order.id,
            status=DirectPaymentIntent.STATUS_PAID,
        )
        .order_by("-created_at")
        .first()
    )
    if intent:
        return int(intent.amount_cents)
    buyer_tx = getattr(order, "buyer_debit_transaction", None)
    if buyer_tx and int(getattr(buyer_tx, "amount_cents", 0) or 0) > 0:
        return int(buyer_tx.amount_cents)
    return 0


def _execute_provider_refund(order, amount_cents: int) -> tuple[bool, str, str, str]:
    """Returns (success, provider, provider_ref, failure_reason)."""
    from apps.billing.models import DirectPaymentIntent

    intent = (
        DirectPaymentIntent.objects.filter(
            target_type=DirectPaymentIntent.TARGET_MARKETPLACE_ORDER,
            target_id=order.id,
            status=DirectPaymentIntent.STATUS_PAID,
        )
        .order_by("-created_at")
        .first()
    )
    if intent and intent.provider == "flutterwave" and intent.provider_ref:
        from apps.billing.direct_payments import refund_flutterwave_transaction
        try:
            result = refund_flutterwave_transaction(intent.provider_ref, amount_cents=amount_cents)
            return True, "flutterwave", str(result.get("id") or intent.provider_ref), ""
        except ValueError as exc:
            return False, "flutterwave", "", str(exc)
    if intent and intent.provider == "stripe":
        return False, "stripe", "", "Stripe refund integration is not yet implemented in apps.billing."
    if getattr(order, "buyer_debit_transaction_id", None) and order.status in {
        MarketplaceOrderStatus.TEMPORAL,
        MarketplaceOrderStatus.AWAITING_SATISFACTION,
    }:
        from apps.billing.services import refund_locked_booking_funds
        refund_locked_booking_funds(
            payer=order.buyer,
            amount_cents=amount_cents,
            reference=f"marketplace-return-refund-{order.id}-{uuid.uuid4().hex[:8]}",
            meta={"order_id": str(order.id), "source": "marketplace_return_refund"},
        )
        return True, "wallet", "", ""
    if getattr(order, "buyer_debit_transaction_id", None):
        return False, "wallet", "", (
            "Legacy wallet escrow for this order has already been released to the seller; "
            "a post-satisfaction wallet refund/clawback is not supported."
        )
    return False, "unknown", "", "No refundable payment record was found for this order."


def create_refund(*, order, amount_cents, reason, idempotency_key, actor=None, return_request=None):
    existing = Refund.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        return existing
    if amount_cents <= 0:
        raise ValidationError("Refund amount must be greater than zero.")

    with transaction.atomic():
        order_locked = MarketplaceOrder.objects.select_for_update().get(id=order.id)
        total_paid = _total_paid_cents(order_locked)
        already_refunded = (
            Refund.objects.select_for_update()
            .filter(order=order_locked, status=RefundStatus.SUCCEEDED)
            .aggregate(total=Sum("amount_cents"))
            .get("total") or 0
        )
        remaining = total_paid - already_refunded
        if amount_cents > remaining:
            raise ValidationError(
                f"Refund of {amount_cents} cents would exceed the {remaining} cents still refundable on this order."
            )

        refund = Refund.objects.create(
            order=order_locked,
            return_request=return_request,
            amount_cents=amount_cents,
            reason=reason,
            idempotency_key=idempotency_key,
            initiated_by=actor,
        )
        success, provider, provider_ref, failure_reason = _execute_provider_refund(order_locked, amount_cents)
        refund.provider = provider
        refund.provider_ref = provider_ref
        refund.processed_at = timezone.now()
        refund.status = RefundStatus.SUCCEEDED if success else RefundStatus.FAILED
        refund.failure_reason = failure_reason
        refund.save(update_fields=["provider", "provider_ref", "processed_at", "status", "failure_reason", "updated_at"])

    _notify_refund(refund)
    return refund


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

_RETURN_NOTIFICATION_COPY = {
    ReturnStatus.REQUESTED: ("New return request", "{buyer} requested a return on an order from {shop}."),
    ReturnStatus.APPROVED: ("Your return was approved", "{shop} approved your return request. Ship the item(s) back to continue."),
    ReturnStatus.REJECTED: ("Your return request was declined", "{shop} declined your return request."),
    ReturnStatus.RETURN_SHIPPED: ("Return on its way back", "{buyer} marked their return as shipped."),
    ReturnStatus.RECEIVED: ("Return received", "{shop} received your returned item(s) and will inspect them shortly."),
    ReturnStatus.COMPLETED: ("Return completed and refunded", "Your return from {shop} is complete and a refund has been issued."),
    ReturnStatus.CANCELLED: ("Return cancelled", "A return request was cancelled."),
}


def _notify_return_status(return_request, *, audience: str) -> None:
    copy = _RETURN_NOTIFICATION_COPY.get(return_request.status)
    if not copy:
        return
    title, body_template = copy
    body = body_template.format(buyer=return_request.buyer.username, shop=return_request.order.shop.name)
    if audience == "seller":
        user_id = str(return_request.order.shop.owner_id)
    else:
        user_id = str(return_request.buyer_id)
    create_notification(
        user_id=user_id,
        type=f"marketplace.return.{return_request.status}",
        title=title,
        body=body,
        target_type="return_request",
        target_id=str(return_request.id),
        context={"order_id": str(return_request.order_id), "shop_id": str(return_request.order.shop_id)},
        dedup_key=f"return-{return_request.id}-{return_request.status}",
    )


def _notify_refund(refund) -> None:
    if refund.status != RefundStatus.SUCCEEDED:
        return
    create_notification(
        user_id=str(refund.order.buyer_id),
        type="marketplace.refund.succeeded",
        title="Refund issued",
        body=f"A refund of {refund.amount_cents / 100:.2f} {refund.order.currency} has been issued to you.",
        target_type="refund",
        target_id=str(refund.id),
        context={"order_id": str(refund.order_id)},
        dedup_key=f"refund-{refund.id}-succeeded",
    )
