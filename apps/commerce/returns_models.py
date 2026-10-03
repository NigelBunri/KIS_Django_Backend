"""
Returns/RMA models for apps.commerce.

Architectural separation (mirrors shipping_models.py's split - see
docs/commerce_shipping.md for that precedent):

  MarketplaceOrder     — what the customer purchased (unchanged, pre-existing)
  ReturnRequest        — one buyer-initiated request to return part/all of
                          an order, carried through a seller-review and
                          (for physical goods) inspection lifecycle
  ReturnItem           — which order items/quantities are covered by a
                          given ReturnRequest (partial-return support)

A ReturnRequest's return-leg tracking reuses the existing Shipment model
(FulfillmentStatus/ShipmentStatus already have a RETURNED terminal state
that anticipated this) rather than inventing a parallel shipment concept -
return_shipment is a nullable FK to the same Shipment table used for
outbound delivery.

This module is imported from the bottom of models.py, same as
shipping_models.py and for the same reason (Django app-registry
registration on every startup path) - see models.py's closing import block.
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from .models import JSONField


class ReturnStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    RETURN_SHIPPED = "return_shipped", "Return shipped"
    RECEIVED = "received", "Received"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class ReturnReason(models.TextChoices):
    DAMAGED = "damaged", "Damaged"
    WRONG_ITEM = "wrong_item", "Wrong item"
    NOT_AS_DESCRIBED = "not_as_described", "Not as described"
    DEFECTIVE = "defective", "Defective"
    NO_LONGER_NEEDED = "no_longer_needed", "No longer needed"
    OTHER = "other", "Other"


# Documented state machine - see returns_services.transition_return, the
# single place these are enforced. Nothing else may assign
# ReturnRequest.status directly. REJECTED is deliberately reused for both
# a pre-shipment seller decline and a post-inspection failure (inspected_at/
# inspected_by on the row disambiguate which happened) rather than adding a
# second terminal-rejection state.
RETURN_TRANSITIONS: dict[str, set[str]] = {
    ReturnStatus.REQUESTED: {ReturnStatus.APPROVED, ReturnStatus.REJECTED, ReturnStatus.CANCELLED},
    ReturnStatus.APPROVED: {ReturnStatus.RETURN_SHIPPED, ReturnStatus.CANCELLED},
    ReturnStatus.RETURN_SHIPPED: {ReturnStatus.RECEIVED},
    ReturnStatus.RECEIVED: {ReturnStatus.COMPLETED, ReturnStatus.REJECTED},
    ReturnStatus.REJECTED: set(),
    ReturnStatus.CANCELLED: set(),
    ReturnStatus.COMPLETED: set(),
}

# How many days after delivery a buyer may open a return. Only enforced
# when delivery-date data actually exists (see returns_services.
# create_return_request) - orders with no Fulfillment/Shipment (digital/
# service orders that never configured shipping) are not subject to this
# window, which is an explicit, documented choice rather than an oversight.
RETURN_WINDOW_DAYS = 30


class ReturnRequest(models.Model):
    """One buyer-initiated request to return part or all of a
    MarketplaceOrder. order.status must be SATISFIED or COMPLETED for a
    request to be opened (see returns_services.create_return_request) -
    an unpaid or already-cancelled order can't be returned."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    order = models.ForeignKey("commerce.MarketplaceOrder", on_delete=models.CASCADE, related_name="return_requests")
    buyer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="return_requests")
    status = models.CharField(max_length=24, choices=ReturnStatus.choices, default=ReturnStatus.REQUESTED, db_index=True)
    reason = models.CharField(max_length=24, choices=ReturnReason.choices)
    explanation = models.TextField(blank=True, default="")

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    seller_notes = models.TextField(blank=True, default="")
    rejection_reason = models.TextField(blank=True, default="")

    # The return leg's own tracking info. Deliberately plain fields rather
    # than a reuse of the outbound Shipment model: Shipment.fulfillment is
    # a required (non-null) FK, so representing a reverse-logistics leg
    # through it would require manufacturing a Fulfillment context that
    # doesn't otherwise exist for a return - not worth the coupling for
    # what is, from the platform's side, just "a carrier + tracking number
    # the buyer self-reported".
    return_carrier_name = models.CharField(max_length=128, blank=True, default="")
    return_tracking_number = models.CharField(max_length=128, blank=True, default="")
    return_shipped_at = models.DateTimeField(null=True, blank=True)

    received_at = models.DateTimeField(null=True, blank=True)
    inspected_at = models.DateTimeField(null=True, blank=True)
    inspected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    metadata = JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["order", "status"]), models.Index(fields=["buyer", "status"])]

    def __str__(self):
        return f"ReturnRequest {self.id} for order {self.order_id} ({self.status})"


class ReturnItem(models.Model):
    """Which order items/quantities a ReturnRequest covers. Multiple
    ReturnItems across different (non-rejected/cancelled) ReturnRequests may
    reference the same order_item (a buyer could return 1 of 3 units, then
    later request to return another); the invariant that the cumulative
    returned quantity never exceeds order_item.quantity is enforced
    transactionally in returns_services.create_return_request, the same
    pattern ShipmentItem documents for its own aggregate-across-siblings
    constraint that a bare Postgres CHECK can't express."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    return_request = models.ForeignKey(ReturnRequest, on_delete=models.CASCADE, related_name="items")
    order_item = models.ForeignKey("commerce.MarketplaceOrderItem", on_delete=models.PROTECT, related_name="return_items")
    quantity = models.PositiveIntegerField()

    class Meta:
        unique_together = ("return_request", "order_item")
        indexes = [models.Index(fields=["order_item"])]

    def __str__(self):
        return f"{self.quantity}x {self.order_item_id} in return {self.return_request_id}"


class RefundStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"


class RefundReason(models.TextChoices):
    RETURN = "return", "Return"
    CANCELLATION = "cancellation", "Cancellation"
    GOODWILL = "goodwill", "Goodwill"
    DISPUTE = "dispute", "Dispute"


class Refund(models.Model):
    """One money-movement record against a MarketplaceOrder. Reuses the
    existing payment abstraction to actually move money (apps.billing's
    DirectPaymentIntent/Flutterwave refund API for provider-paid orders,
    refund_locked_booking_funds for legacy wallet-escrow orders - see
    returns_services.create_refund) rather than introducing a second
    payment system. total_refunded <= total_paid is enforced in that
    service function, not here, since it requires summing sibling rows."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    order = models.ForeignKey("commerce.MarketplaceOrder", on_delete=models.PROTECT, related_name="refunds")
    return_request = models.ForeignKey(
        ReturnRequest, on_delete=models.SET_NULL, null=True, blank=True, related_name="refunds"
    )
    amount_cents = models.PositiveIntegerField()
    reason = models.CharField(max_length=20, choices=RefundReason.choices)
    status = models.CharField(max_length=20, choices=RefundStatus.choices, default=RefundStatus.PENDING, db_index=True)
    # Caller-supplied (e.g. f"return-{return_request.id}" or
    # f"cancel-{order.id}") so a retried request for the same logical refund
    # can't double-charge the provider or double-create a row.
    idempotency_key = models.CharField(max_length=191, unique=True)
    provider = models.CharField(max_length=32, blank=True, default="")
    provider_ref = models.CharField(max_length=191, blank=True, default="")
    failure_reason = models.TextField(blank=True, default="")
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    processed_at = models.DateTimeField(null=True, blank=True)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["order", "status"])]

    def __str__(self):
        return f"Refund {self.id} for order {self.order_id} ({self.status}, {self.amount_cents}c)"
