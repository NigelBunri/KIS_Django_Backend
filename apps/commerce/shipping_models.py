"""
Shipping & fulfillment models for apps.commerce.

Architectural separation (see docs/commerce_shipping.md for the full writeup):

  MarketplaceOrder  — what the customer purchased (unchanged, pre-existing)
  Fulfillment       — how one shop is preparing/delivering its order
  Shipment          — one physical delivery movement (a fulfillment can have
                       several, to support partial shipment)
  ShipmentItem      — which order items/quantities travel in a given shipment

MarketplaceOrder is already scoped to exactly one Shop (the cart/checkout
architecture creates one MarketplaceOrder per shop, never a cross-shop
order), so Fulfillment is deliberately a 1:1 extension of MarketplaceOrder
rather than a separate "order has many fulfillments" model — there is
already exactly one seller responsible per order. The spec's suggested
FulfillmentItem layer is therefore redundant here (a fulfillment's items are
simply "all items of its order") and is intentionally omitted; partial
shipment is tracked at the ShipmentItem level against MarketplaceOrderItem
directly. This is a deliberate, documented simplification of the generic
schema sketch, not an oversight.

This module is imported from the bottom of models.py specifically so its
models register with Django's app registry on every normal startup path
(server, management commands, migrations) — see models.py's closing import
and the comment there for why that matters in this codebase.
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from .models import JSONField


def _generate_shipment_reference() -> str:
    return f"SHP-{uuid.uuid4().hex[:10].upper()}"


class AddressValidationStatus(models.TextChoices):
    UNVERIFIED = "unverified", "Unverified"
    VERIFIED = "verified", "Verified"


class CustomerAddress(models.Model):
    """A buyer's saved delivery address. Deliberately separate from any
    shop/service address fields already on Shop/ShopService - those describe
    where a seller operates from, this describes where a buyer receives
    goods. No reusable customer-facing address model existed anywhere in the
    codebase before this (confirmed by search), so this is new, not a
    duplicate of something else."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="delivery_addresses")
    label = models.CharField(max_length=64, blank=True, default="")
    recipient_name = models.CharField(max_length=255)
    recipient_phone = models.CharField(max_length=32)

    country = models.CharField(max_length=2, help_text="ISO 3166-1 alpha-2, e.g. NG, US, CM")
    state = models.CharField(max_length=128, blank=True, default="")
    city = models.CharField(max_length=128, blank=True, default="")
    district = models.CharField(max_length=128, blank=True, default="")
    street_address = models.CharField(max_length=255)
    apartment = models.CharField(max_length=128, blank=True, default="")
    postal_code = models.CharField(max_length=20, blank=True, default="")
    delivery_instructions = models.TextField(blank=True, default="")

    # Deliberately optional - the system must work without precise
    # coordinates (spec §4). Populated later by a geocoding step if KIS
    # adds one; zone matching never depends on these being set.
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    is_default = models.BooleanField(default=False)
    validation_status = models.CharField(
        max_length=16, choices=AddressValidationStatus.choices, default=AddressValidationStatus.UNVERIFIED,
    )

    class Meta:
        indexes = [
            models.Index(fields=["user", "is_default"]),
            models.Index(fields=["user", "is_deleted"]),
        ]

    def __str__(self):
        return f"{self.recipient_name} · {self.city or self.state or self.country}"

    def to_snapshot(self) -> dict:
        """Frozen copy stored on Fulfillment/Shipment at order time. Orders
        must never silently change delivery address when a customer later
        edits or deletes this saved address (spec §4) - every order-facing
        read goes through the snapshot, never a live FK dereference into
        CustomerAddress for already-placed orders."""
        return {
            "address_id": str(self.id),
            "label": self.label,
            "recipient_name": self.recipient_name,
            "recipient_phone": self.recipient_phone,
            "country": self.country,
            "state": self.state,
            "city": self.city,
            "district": self.district,
            "street_address": self.street_address,
            "apartment": self.apartment,
            "postal_code": self.postal_code,
            "delivery_instructions": self.delivery_instructions,
            "latitude": str(self.latitude) if self.latitude is not None else None,
            "longitude": str(self.longitude) if self.longitude is not None else None,
        }


class ShippingZone(models.Model):
    """A seller-defined delivery region. Zones are shop-scoped (sellers
    define their own delivery regions and rates - spec §5/§9), not a global
    platform-wide list, and nothing here hard-codes any specific country's
    administrative divisions.

    Matching is deterministic by specificity, most specific wins:
    postal_code_prefix > city > state > country-only ("ships anywhere in
    this country"). Ties within the same specificity break on the explicit
    `priority` field (higher wins), then on `created_at` (earlier wins) -
    see shipping_services.match_zone_for_address for the implementation and
    docs/commerce_shipping.md for a worked example.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    shop = models.ForeignKey("commerce.Shop", on_delete=models.CASCADE, related_name="shipping_zones")
    name = models.CharField(max_length=128)
    country = models.CharField(max_length=2, help_text="ISO 3166-1 alpha-2. Required - every zone matches within one country.")
    state = models.CharField(max_length=128, blank=True, default="", help_text="Blank = any state/province in this country.")
    city = models.CharField(max_length=128, blank=True, default="", help_text="Blank = any city in this state.")
    postal_code_prefix = models.CharField(max_length=20, blank=True, default="")
    is_active = models.BooleanField(default=True)
    priority = models.IntegerField(default=0, help_text="Tie-break only, used when two zones match with equal specificity.")

    class Meta:
        indexes = [
            models.Index(fields=["shop", "country", "is_active"]),
            models.Index(fields=["shop", "is_active"]),
        ]

    def __str__(self):
        parts = [p for p in [self.postal_code_prefix, self.city, self.state, self.country] if p]
        return f"{self.name} ({' / '.join(parts) or 'any'})"

    @property
    def specificity(self) -> int:
        score = 0
        if self.postal_code_prefix:
            score += 8
        if self.city:
            score += 4
        if self.state:
            score += 2
        return score


class ShippingMethodType(models.TextChoices):
    STANDARD = "standard", "Standard Delivery"
    EXPRESS = "express", "Express Delivery"
    SAME_DAY = "same_day", "Same-Day Delivery"
    PICKUP = "pickup", "Pickup"
    SELLER_DELIVERY = "seller_delivery", "Seller Delivery"
    CARRIER = "carrier", "Carrier Delivery"


class ShippingMethod(models.Model):
    """What kind of delivery a shop offers (Standard/Express/Pickup/...).
    Zone- and price-agnostic by design - a method's availability in a zone
    and its price in that zone live on ShippingRate, so the same method can
    carry different rates (or be unavailable) per zone without duplicating
    method definitions. `provider_code` keys into the provider registry in
    shipping_services.py (spec §17) - 'manual' today, a real carrier
    integration's code later, without any model change."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    shop = models.ForeignKey("commerce.Shop", on_delete=models.CASCADE, related_name="shipping_methods")
    name = models.CharField(max_length=128)
    description = models.TextField(blank=True, default="")
    method_type = models.CharField(max_length=20, choices=ShippingMethodType.choices, default=ShippingMethodType.STANDARD)
    provider_code = models.CharField(max_length=64, default="manual")
    is_active = models.BooleanField(default=True)
    min_days = models.PositiveIntegerField(default=1)
    max_days = models.PositiveIntegerField(default=3)

    class Meta:
        indexes = [models.Index(fields=["shop", "is_active"])]

    def __str__(self):
        return f"{self.name} ({self.shop.name})"

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.min_days > self.max_days:
            raise ValidationError({"min_days": "min_days cannot be greater than max_days."})


class ShippingRateType(models.TextChoices):
    FLAT = "flat", "Flat rate"
    FREE = "free", "Free shipping"
    THRESHOLD = "threshold", "Free above a subtotal threshold"
    QUANTITY = "quantity", "Per-item rate"
    WEIGHT = "weight", "Weight-based rate"


class ShippingRate(models.Model):
    """The price of one ShippingMethod in one ShippingZone. One active rate
    per (method, zone) pair - if a shop needs a different strategy for a
    zone, it replaces the rate rather than stacking ambiguous rules, which
    keeps the server-side calculation in shipping_services.py a single
    unambiguous lookup rather than a rule-priority engine (that complexity
    can be added later without touching checkout - spec §7/§33)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    method = models.ForeignKey(ShippingMethod, on_delete=models.CASCADE, related_name="rates")
    zone = models.ForeignKey(ShippingZone, on_delete=models.CASCADE, related_name="rates")
    rate_type = models.CharField(max_length=20, choices=ShippingRateType.choices, default=ShippingRateType.FLAT)
    base_cents = models.PositiveIntegerField(default=0, help_text="Flat price / per-item price / per-kg price, depending on rate_type.")
    free_above_cents = models.PositiveIntegerField(null=True, blank=True, help_text="THRESHOLD only: order subtotal (pre-shipping) at or above which shipping is free.")
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("method", "zone")
        indexes = [models.Index(fields=["method", "zone", "is_active"])]

    def __str__(self):
        return f"{self.method.name} in {self.zone.name}: {self.get_rate_type_display()}"


class FulfillmentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACCEPTED = "accepted", "Accepted"
    PROCESSING = "processing", "Processing"
    READY_FOR_SHIPMENT = "ready_for_shipment", "Ready for shipment"
    PARTIALLY_SHIPPED = "partially_shipped", "Partially shipped"
    SHIPPED = "shipped", "Shipped"
    DELIVERED = "delivered", "Delivered"
    CANCELLED = "cancelled", "Cancelled"
    RETURNED = "returned", "Returned"


# Documented state machine - see shipping_services.transition_fulfillment,
# the single place these are enforced. Nothing else may assign
# Fulfillment.status directly.
FULFILLMENT_TRANSITIONS: dict[str, set[str]] = {
    FulfillmentStatus.PENDING: {FulfillmentStatus.ACCEPTED, FulfillmentStatus.CANCELLED},
    FulfillmentStatus.ACCEPTED: {FulfillmentStatus.PROCESSING, FulfillmentStatus.CANCELLED},
    FulfillmentStatus.PROCESSING: {FulfillmentStatus.READY_FOR_SHIPMENT, FulfillmentStatus.CANCELLED},
    FulfillmentStatus.READY_FOR_SHIPMENT: {FulfillmentStatus.PARTIALLY_SHIPPED, FulfillmentStatus.SHIPPED, FulfillmentStatus.CANCELLED},
    FulfillmentStatus.PARTIALLY_SHIPPED: {FulfillmentStatus.SHIPPED, FulfillmentStatus.RETURNED},
    FulfillmentStatus.SHIPPED: {FulfillmentStatus.DELIVERED, FulfillmentStatus.RETURNED},
    FulfillmentStatus.DELIVERED: {FulfillmentStatus.RETURNED},
    FulfillmentStatus.CANCELLED: set(),
    FulfillmentStatus.RETURNED: set(),
}


class Fulfillment(models.Model):
    """One shop's responsibility for preparing and delivering the items in
    one MarketplaceOrder. 1:1 with MarketplaceOrder - see this module's
    docstring for why that's correct here rather than a limitation."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    order = models.OneToOneField("commerce.MarketplaceOrder", on_delete=models.CASCADE, related_name="fulfillment")
    shop = models.ForeignKey("commerce.Shop", on_delete=models.CASCADE, related_name="fulfillments")
    status = models.CharField(max_length=24, choices=FulfillmentStatus.choices, default=FulfillmentStatus.PENDING, db_index=True)

    shipping_method = models.ForeignKey(ShippingMethod, on_delete=models.SET_NULL, null=True, blank=True, related_name="fulfillments")
    shipping_zone = models.ForeignKey(ShippingZone, on_delete=models.SET_NULL, null=True, blank=True, related_name="fulfillments")
    shipping_cost_cents = models.PositiveIntegerField(default=0)

    delivery_address_snapshot = JSONField(default=dict, blank=True)
    estimated_delivery_min = models.DateField(null=True, blank=True)
    estimated_delivery_max = models.DateField(null=True, blank=True)

    accepted_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    metadata = JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["shop", "status"])]

    def __str__(self):
        return f"Fulfillment {self.id} for order {self.order_id} ({self.status})"


class ShipmentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    LABEL_CREATED = "label_created", "Label created"
    READY_FOR_PICKUP = "ready_for_pickup", "Ready for pickup"
    PICKED_UP = "picked_up", "Picked up"
    IN_TRANSIT = "in_transit", "In transit"
    OUT_FOR_DELIVERY = "out_for_delivery", "Out for delivery"
    DELIVERED = "delivered", "Delivered"
    DELIVERY_FAILED = "delivery_failed", "Delivery failed"
    RETURNED = "returned", "Returned"
    CANCELLED = "cancelled", "Cancelled"


# Same contract as FULFILLMENT_TRANSITIONS - see
# shipping_services.transition_shipment.
SHIPMENT_TRANSITIONS: dict[str, set[str]] = {
    ShipmentStatus.PENDING: {ShipmentStatus.LABEL_CREATED, ShipmentStatus.CANCELLED},
    ShipmentStatus.LABEL_CREATED: {ShipmentStatus.READY_FOR_PICKUP, ShipmentStatus.CANCELLED},
    ShipmentStatus.READY_FOR_PICKUP: {ShipmentStatus.PICKED_UP, ShipmentStatus.CANCELLED},
    ShipmentStatus.PICKED_UP: {ShipmentStatus.IN_TRANSIT, ShipmentStatus.CANCELLED},
    ShipmentStatus.IN_TRANSIT: {ShipmentStatus.OUT_FOR_DELIVERY, ShipmentStatus.DELIVERY_FAILED, ShipmentStatus.CANCELLED},
    ShipmentStatus.OUT_FOR_DELIVERY: {ShipmentStatus.DELIVERED, ShipmentStatus.DELIVERY_FAILED},
    ShipmentStatus.DELIVERY_FAILED: {ShipmentStatus.OUT_FOR_DELIVERY, ShipmentStatus.RETURNED, ShipmentStatus.CANCELLED},
    ShipmentStatus.DELIVERED: {ShipmentStatus.RETURNED},
    ShipmentStatus.RETURNED: set(),
    ShipmentStatus.CANCELLED: set(),
}


class Shipment(models.Model):
    """One physical delivery movement. A Fulfillment can have several (to
    support partial shipment - spec §12)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    fulfillment = models.ForeignKey(Fulfillment, on_delete=models.CASCADE, related_name="shipments")
    reference = models.CharField(max_length=32, unique=True, default=_generate_shipment_reference)
    carrier_name = models.CharField(max_length=128, blank=True, default="")
    tracking_number = models.CharField(max_length=128, blank=True, default="")
    shipping_method = models.ForeignKey(ShippingMethod, on_delete=models.SET_NULL, null=True, blank=True, related_name="shipments")
    status = models.CharField(max_length=24, choices=ShipmentStatus.choices, default=ShipmentStatus.PENDING, db_index=True)

    origin_snapshot = JSONField(default=dict, blank=True)
    destination_snapshot = JSONField(default=dict, blank=True)
    shipping_cost_cents = models.PositiveIntegerField(default=0)
    estimated_delivery_min = models.DateField(null=True, blank=True)
    estimated_delivery_max = models.DateField(null=True, blank=True)

    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    metadata = JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["fulfillment", "status"])]

    def __str__(self):
        return f"Shipment {self.reference} ({self.status})"


class ShipmentItem(models.Model):
    """Which order items/quantities travel in this shipment. Multiple
    ShipmentItems across different shipments may reference the same
    order_item (that's how partial shipment is represented); the invariant
    that their quantities never exceed order_item.quantity is enforced
    transactionally in shipping_services.create_shipment, not by a bare DB
    CHECK constraint (the check is an aggregate across sibling rows, which
    Postgres CHECK constraints can't express directly)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="items")
    # PROTECT, not CASCADE: mirrors MarketplaceOrderItem.product's own
    # PROTECT-against-Product in models.py - no normal code path deletes a
    # MarketplaceOrderItem, but if one ever did, it must not silently wipe
    # the shipment audit trail of what was actually shipped for it.
    order_item = models.ForeignKey("commerce.MarketplaceOrderItem", on_delete=models.PROTECT, related_name="shipment_items")
    quantity = models.PositiveIntegerField()

    class Meta:
        unique_together = ("shipment", "order_item")
        indexes = [models.Index(fields=["order_item"])]

    def __str__(self):
        return f"{self.quantity}x {self.order_item_id} in {self.shipment.reference}"


class ShipmentEventSource(models.TextChoices):
    MANUAL = "manual", "Manual"
    SYSTEM = "system", "System"
    CARRIER_WEBHOOK = "carrier_webhook", "Carrier webhook"


class ShipmentEvent(models.Model):
    """Append-only tracking history - shipment status is never silently
    overwritten without a corresponding event (spec §16). A future carrier
    webhook integration writes here through the same
    shipping_services.record_shipment_event helper manual/admin updates use,
    keyed by (shipment, external_event_id) for webhook-replay idempotency
    (spec §24) - see that function's docstring."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="events")
    status = models.CharField(max_length=24, choices=ShipmentStatus.choices)
    description = models.CharField(max_length=255, blank=True, default="")
    source = models.CharField(max_length=20, choices=ShipmentEventSource.choices, default=ShipmentEventSource.MANUAL)
    location = models.CharField(max_length=255, blank=True, default="")
    occurred_at = models.DateTimeField(default=timezone.now)
    external_event_id = models.CharField(max_length=128, blank=True, default="", help_text="Carrier-provided event id, for webhook replay idempotency.")
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [models.Index(fields=["shipment", "occurred_at"])]

    def __str__(self):
        return f"{self.shipment.reference}: {self.status} @ {self.occurred_at}"
