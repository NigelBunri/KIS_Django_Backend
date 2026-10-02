"""
Shipping/fulfillment business logic: zone matching, rate calculation, the
provider abstraction (spec §17/§18), and the only code paths allowed to
create or transition Fulfillment/Shipment rows - views must call through
here, never set .status directly, so the state machines in
shipping_models.py are actually enforced everywhere.

Concurrency: every write here that touches a Fulfillment's shipped
quantities runs inside `transaction.atomic()` with `select_for_update()` on
the Fulfillment row, so two concurrent "create shipment" or "transition"
calls against the same fulfillment serialize instead of racing - the same
pattern already used for Product stock locking in services.py.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.notifications.services import create_notification

from .models import MarketplaceOrderItem, Shop
from .shipping_models import (
    CustomerAddress,
    Fulfillment,
    FULFILLMENT_TRANSITIONS,
    FulfillmentStatus,
    Shipment,
    ShipmentEvent,
    ShipmentEventSource,
    ShipmentItem,
    SHIPMENT_TRANSITIONS,
    ShipmentStatus,
    ShippingMethod,
    ShippingRate,
    ShippingRateType,
    ShippingZone,
)


# ---------------------------------------------------------------------------
# Zone matching (spec §5)
# ---------------------------------------------------------------------------

def match_zone_for_address(shop: Shop, address: CustomerAddress) -> ShippingZone | None:
    """Deterministic zone match: among this shop's active zones whose
    country/state/city/postal-prefix all either match the address or are
    left blank ("any"), pick the most specific one. Ties break on the
    zone's explicit `priority` (higher wins), then on `created_at`
    (earlier wins) so the result never depends on dict/queryset ordering.

    Example: a shop with zones "Nigeria" (country-only) and "Nigeria ·
    Lagos" (country+state) both match a Lagos address; "Nigeria · Lagos"
    wins because it's more specific, regardless of which was created first
    or how they're stored.
    """
    candidates = ShippingZone.objects.filter(shop=shop, is_deleted=False, is_active=True, country=address.country)
    matched = []
    for zone in candidates:
        if zone.state and zone.state.strip().lower() != (address.state or "").strip().lower():
            continue
        if zone.city and zone.city.strip().lower() != (address.city or "").strip().lower():
            continue
        if zone.postal_code_prefix and not (address.postal_code or "").startswith(zone.postal_code_prefix):
            continue
        matched.append(zone)
    if not matched:
        return None
    matched.sort(key=lambda z: (-z.specificity, -z.priority, z.created_at))
    return matched[0]


# ---------------------------------------------------------------------------
# Rate calculation (spec §7)
# ---------------------------------------------------------------------------

@dataclass
class RateableItem:
    quantity: int
    weight_kg: Decimal | None = None


def calculate_rate_cents(rate: ShippingRate, *, subtotal_cents: int, items: Iterable[RateableItem]) -> int:
    """Pure calculation - no I/O, no side effects, safe to call for a
    checkout preview as well as from inside the order transaction. The
    server is always the one calling this; checkout never accepts a client-
    supplied shipping price (spec §8)."""
    items = list(items)
    if rate.rate_type == ShippingRateType.FREE:
        return 0
    if rate.rate_type == ShippingRateType.FLAT:
        return rate.base_cents
    if rate.rate_type == ShippingRateType.THRESHOLD:
        threshold = rate.free_above_cents if rate.free_above_cents is not None else 0
        return 0 if subtotal_cents >= threshold else rate.base_cents
    if rate.rate_type == ShippingRateType.QUANTITY:
        total_qty = sum(i.quantity for i in items)
        return rate.base_cents * total_qty
    if rate.rate_type == ShippingRateType.WEIGHT:
        # A product with no weight_kg set contributes 0 - documented in
        # Product.weight_kg's field comment, not a silent miscalculation.
        total_weight = sum((i.weight_kg or Decimal("0")) * i.quantity for i in items)
        # round up to the nearest whole kg-unit of pricing, minimum 1 unit
        # for any non-zero shipment so a 0.2kg item isn't free to ship.
        units = int(total_weight) + (1 if total_weight % 1 else 0)
        return rate.base_cents * max(units, 1 if total_weight > 0 else 0)
    raise ValidationError(f"Unknown shipping rate type: {rate.rate_type}")


def estimate_delivery_dates(method: ShippingMethod) -> tuple:
    today = timezone.now().date()
    return today + timezone.timedelta(days=method.min_days), today + timezone.timedelta(days=method.max_days)


def list_shipping_options(*, shop: Shop, address: CustomerAddress, subtotal_cents: int, items: Iterable[RateableItem]) -> list[dict]:
    """Every active ShippingMethod of this shop that has an active rate in
    the address's matched zone, with its computed cost and estimate. Used
    both for the checkout "pick a shipping method" screen and, internally,
    to validate a client-selected method+zone pair at order-creation time
    (never trusting a client-supplied price or zone - spec §8)."""
    zone = match_zone_for_address(shop, address)
    if zone is None:
        return []
    items = list(items)
    rates = (
        ShippingRate.objects.filter(zone=zone, is_active=True, method__shop=shop, method__is_active=True, is_deleted=False)
        .select_related("method")
    )
    options = []
    for rate in rates:
        cost_cents = calculate_rate_cents(rate, subtotal_cents=subtotal_cents, items=items)
        min_date, max_date = estimate_delivery_dates(rate.method)
        options.append({
            "shipping_method_id": str(rate.method_id),
            "shipping_method_name": rate.method.name,
            "method_type": rate.method.method_type,
            "shipping_zone_id": str(zone.id),
            "shipping_zone_name": zone.name,
            "cost_cents": cost_cents,
            "estimated_delivery_min": min_date.isoformat(),
            "estimated_delivery_max": max_date.isoformat(),
        })
    options.sort(key=lambda o: o["cost_cents"])
    return options


def resolve_shipping_selection(*, shop: Shop, address: CustomerAddress, shipping_method_id: str, subtotal_cents: int, items: Iterable[RateableItem]) -> dict:
    """Re-derives the authoritative cost/zone/estimate for a client-selected
    shipping_method_id, exactly like place_marketplace_order re-derives
    product price - the client only ever supplies a method id, never a
    price. Raises ValidationError if that method isn't actually available
    for this shop+address."""
    options = list_shipping_options(shop=shop, address=address, subtotal_cents=subtotal_cents, items=items)
    for option in options:
        if option["shipping_method_id"] == str(shipping_method_id):
            return option
    raise ValidationError({"shipping_method_id": "This shipping method is not available for the selected address."})


# ---------------------------------------------------------------------------
# Provider abstraction (spec §17/§18)
# ---------------------------------------------------------------------------

class ShippingProvider(abc.ABC):
    """Capability interface a future carrier integration implements. Core
    commerce code (checkout, fulfillment, shipment transitions) only ever
    talks to this interface, never to a specific carrier - swapping or
    adding a provider means adding a class here and a provider_code on the
    relevant ShippingMethod rows, nothing else."""

    @abc.abstractmethod
    def create_shipment(self, shipment: Shipment) -> dict:
        """Register the shipment with the provider. Returns a dict that may
        include carrier_name/tracking_number to store on the Shipment."""

    @abc.abstractmethod
    def cancel_shipment(self, shipment: Shipment) -> None:
        ...

    @abc.abstractmethod
    def get_tracking(self, shipment: Shipment) -> list[dict]:
        """Returns provider-reported tracking events, normalized to
        {status, description, location, occurred_at, external_event_id}."""


class ManualShippingProvider(ShippingProvider):
    """The only concrete provider today (spec §18) - KIS has no logistics
    API yet, so shipment creation/cancellation/tracking are driven entirely
    by seller/admin action through the normal Shipment/ShipmentEvent API,
    using the exact same models a real provider integration will write to
    later. This class intentionally does nothing beyond acknowledging the
    call - it exists so the call sites (shipping_services.create_shipment
    etc.) don't need to know whether a shipment is manual or provider-backed."""

    def create_shipment(self, shipment: Shipment) -> dict:
        return {}

    def cancel_shipment(self, shipment: Shipment) -> None:
        return None

    def get_tracking(self, shipment: Shipment) -> list[dict]:
        return []


_PROVIDER_REGISTRY = {
    "manual": ManualShippingProvider,
}


def get_shipping_provider(method: ShippingMethod) -> ShippingProvider:
    provider_cls = _PROVIDER_REGISTRY.get(method.provider_code, ManualShippingProvider)
    return provider_cls()


# ---------------------------------------------------------------------------
# Fulfillment lifecycle
# ---------------------------------------------------------------------------

def create_fulfillment_for_order(*, order, shop: Shop, address: CustomerAddress, shipping_method: ShippingMethod, shipping_zone: ShippingZone, shipping_cost_cents: int) -> Fulfillment:
    """Called from inside place_marketplace_order's existing transaction
    (services.py) - does not open its own transaction, so a failure here
    rolls back the order creation too, same as the stock-reservation and
    promotion steps it runs alongside."""
    min_date, max_date = estimate_delivery_dates(shipping_method)
    return Fulfillment.objects.create(
        order=order,
        shop=shop,
        status=FulfillmentStatus.PENDING,
        shipping_method=shipping_method,
        shipping_zone=shipping_zone,
        shipping_cost_cents=shipping_cost_cents,
        delivery_address_snapshot=address.to_snapshot(),
        estimated_delivery_min=min_date,
        estimated_delivery_max=max_date,
    )


def _assert_transition(transitions_table, current, target, *, label):
    allowed = transitions_table.get(current, set())
    if target not in allowed:
        raise ValidationError({"status": f"Cannot move {label} from {current} to {target}."})


def transition_fulfillment(fulfillment_id, *, target_status: str, actor) -> Fulfillment:
    with transaction.atomic():
        fulfillment = Fulfillment.objects.select_for_update().select_related("order", "shop", "order__buyer").get(id=fulfillment_id)
        if not _provider_can_manage_shop_or_staff(actor, fulfillment.shop):
            raise PermissionDenied("Only the seller, a shop manager, or staff can update this fulfillment.")
        _assert_transition(FULFILLMENT_TRANSITIONS, fulfillment.status, target_status, label="fulfillment")

        now = timezone.now()
        fulfillment.status = target_status
        if target_status == FulfillmentStatus.ACCEPTED:
            fulfillment.accepted_at = now
        elif target_status == FulfillmentStatus.READY_FOR_SHIPMENT:
            fulfillment.ready_at = now
        elif target_status == FulfillmentStatus.SHIPPED:
            fulfillment.shipped_at = now
        elif target_status == FulfillmentStatus.DELIVERED:
            fulfillment.delivered_at = now
        elif target_status == FulfillmentStatus.CANCELLED:
            fulfillment.cancelled_at = now
        fulfillment.save()

    _notify_fulfillment_status(fulfillment)
    return fulfillment


def _provider_can_manage_shop_or_staff(user, shop) -> bool:
    from .services import _provider_can_manage_shop
    return bool(getattr(user, "is_staff", False)) or _provider_can_manage_shop(user, shop)


_FULFILLMENT_NOTIFICATION_COPY = {
    FulfillmentStatus.ACCEPTED: ("Your order was accepted", "{shop} has accepted your order and will begin preparing it."),
    FulfillmentStatus.READY_FOR_SHIPMENT: ("Your order is ready to ship", "{shop} has finished preparing your order."),
    FulfillmentStatus.SHIPPED: ("Your order has shipped", "{shop} has shipped your order."),
    FulfillmentStatus.DELIVERED: ("Your order was delivered", "Your order from {shop} has been delivered."),
    FulfillmentStatus.CANCELLED: ("Your order was cancelled", "{shop} cancelled your order."),
    FulfillmentStatus.RETURNED: ("Your order was returned", "Your order from {shop} was returned."),
}


def _notify_fulfillment_status(fulfillment: Fulfillment) -> None:
    copy = _FULFILLMENT_NOTIFICATION_COPY.get(fulfillment.status)
    if not copy:
        return
    title, body_template = copy
    create_notification(
        user_id=str(fulfillment.order.buyer_id),
        type=f"marketplace.fulfillment.{fulfillment.status}",
        title=title,
        body=body_template.format(shop=fulfillment.shop.name),
        target_type="fulfillment",
        target_id=str(fulfillment.id),
        context={"order_id": str(fulfillment.order_id), "shop_id": str(fulfillment.shop_id)},
        dedup_key=f"fulfillment-{fulfillment.id}-{fulfillment.status}",
    )


# ---------------------------------------------------------------------------
# Shipment lifecycle
# ---------------------------------------------------------------------------

def _shipped_quantity_for_item(order_item_id) -> int:
    return (
        ShipmentItem.objects.filter(order_item_id=order_item_id, shipment__is_deleted=False)
        .exclude(shipment__status=ShipmentStatus.CANCELLED)
        .aggregate(total=Sum("quantity"))
        .get("total") or 0
    )


def create_shipment(*, fulfillment_id, actor, items: list[dict], carrier_name: str = "", tracking_number: str = "", idempotency_key: str | None = None) -> Shipment:
    """items: [{"order_item_id": "...", "quantity": N}, ...]

    Concurrency: locks the Fulfillment row for the duration of the
    transaction, so two simultaneous "create shipment" calls against the
    same fulfillment (two seller devices, or a retried request) serialize -
    the second sees the first's already-shipped quantities before deciding
    whether there's anything left to ship, the same select_for_update
    pattern services.py already uses for Product stock (spec §13/§23).

    Idempotency: if idempotency_key matches an existing Shipment's
    metadata.idempotency_key for this fulfillment, that shipment is
    returned as-is rather than creating a duplicate (spec §24).
    """
    with transaction.atomic():
        fulfillment = Fulfillment.objects.select_for_update().select_related("shop", "order").get(id=fulfillment_id)
        if not _provider_can_manage_shop_or_staff(actor, fulfillment.shop):
            raise PermissionDenied("Only the seller, a shop manager, or staff can create a shipment for this order.")
        if fulfillment.status not in {FulfillmentStatus.ACCEPTED, FulfillmentStatus.PROCESSING, FulfillmentStatus.READY_FOR_SHIPMENT, FulfillmentStatus.PARTIALLY_SHIPPED}:
            raise ValidationError({"status": f"Cannot create a shipment while fulfillment is {fulfillment.status}."})

        if idempotency_key:
            existing = Shipment.objects.filter(fulfillment=fulfillment, metadata__idempotency_key=idempotency_key).first()
            if existing:
                return existing

        if not items:
            raise ValidationError({"items": "At least one item is required to create a shipment."})

        order_item_ids = [str(i["order_item_id"]) for i in items]
        order_items = {
            str(oi.id): oi
            for oi in MarketplaceOrderItem.objects.select_for_update().filter(id__in=order_item_ids, order_id=fulfillment.order_id)
        }
        if len(order_items) != len(set(order_item_ids)):
            raise ValidationError({"items": "One or more items do not belong to this order."})

        shipment = Shipment.objects.create(
            fulfillment=fulfillment,
            carrier_name=carrier_name,
            tracking_number=tracking_number,
            shipping_method=fulfillment.shipping_method,
            status=ShipmentStatus.PENDING,
            origin_snapshot={"shop_id": str(fulfillment.shop_id), "shop_name": fulfillment.shop.name},
            destination_snapshot=fulfillment.delivery_address_snapshot,
            metadata={"idempotency_key": idempotency_key} if idempotency_key else {},
        )

        shipment_items = []
        for entry in items:
            order_item = order_items[str(entry["order_item_id"])]
            quantity = int(entry["quantity"])
            if quantity <= 0:
                raise ValidationError({"items": "Shipment item quantity must be positive."})
            already_shipped = _shipped_quantity_for_item(order_item.id)
            if already_shipped + quantity > order_item.quantity:
                raise ValidationError({
                    "items": (
                        f"Cannot ship {quantity} of '{order_item.product.name}' - "
                        f"only {order_item.quantity - already_shipped} of {order_item.quantity} remain unshipped."
                    ),
                })
            shipment_items.append(ShipmentItem(shipment=shipment, order_item=order_item, quantity=quantity))
        ShipmentItem.objects.bulk_create(shipment_items)

        record_shipment_event(shipment, status=ShipmentStatus.PENDING, description="Shipment created.", source=ShipmentEventSource.MANUAL, actor_check=False)

        _recompute_fulfillment_shipping_status(fulfillment)

    provider = get_shipping_provider(fulfillment.shipping_method) if fulfillment.shipping_method else ManualShippingProvider()
    provider_result = provider.create_shipment(shipment)
    if provider_result:
        for field in ("carrier_name", "tracking_number"):
            if provider_result.get(field):
                setattr(shipment, field, provider_result[field])
        shipment.save(update_fields=[f for f in ("carrier_name", "tracking_number") if provider_result.get(f)])

    return shipment


def _recompute_fulfillment_shipping_status(fulfillment: Fulfillment) -> None:
    """After a shipment is created, reflect whether the order is now fully
    or partially covered by shipments. Only advances fulfillment.status
    forward along READY_FOR_SHIPMENT -> PARTIALLY_SHIPPED/SHIPPED - never
    called when fulfillment is still PENDING/ACCEPTED/PROCESSING, so an
    out-of-process shipment can't skip the seller's own acceptance step."""
    if fulfillment.status not in {FulfillmentStatus.READY_FOR_SHIPMENT, FulfillmentStatus.PARTIALLY_SHIPPED}:
        return
    order_items = MarketplaceOrderItem.objects.filter(order_id=fulfillment.order_id)
    fully_shipped = all(_shipped_quantity_for_item(oi.id) >= oi.quantity for oi in order_items)
    any_shipped = any(_shipped_quantity_for_item(oi.id) > 0 for oi in order_items)
    new_status = FulfillmentStatus.SHIPPED if fully_shipped else (FulfillmentStatus.PARTIALLY_SHIPPED if any_shipped else fulfillment.status)
    if new_status != fulfillment.status:
        fulfillment.status = new_status
        if new_status == FulfillmentStatus.SHIPPED:
            fulfillment.shipped_at = timezone.now()
        fulfillment.save(update_fields=["status", "shipped_at", "updated_at"] if new_status == FulfillmentStatus.SHIPPED else ["status", "updated_at"])


def transition_shipment(*, shipment_id, actor, target_status: str, description: str = "", location: str = "", source: str = ShipmentEventSource.MANUAL) -> Shipment:
    with transaction.atomic():
        shipment = Shipment.objects.select_for_update().select_related("fulfillment", "fulfillment__shop").get(id=shipment_id)
        fulfillment = shipment.fulfillment
        if not _provider_can_manage_shop_or_staff(actor, fulfillment.shop):
            raise PermissionDenied("Only the seller, a shop manager, or staff can update this shipment.")
        _assert_transition(SHIPMENT_TRANSITIONS, shipment.status, target_status, label="shipment")

        now = timezone.now()
        shipment.status = target_status
        if target_status == ShipmentStatus.DELIVERED:
            shipment.delivered_at = now
        elif target_status == ShipmentStatus.CANCELLED:
            shipment.cancelled_at = now
        elif target_status in {ShipmentStatus.PICKED_UP, ShipmentStatus.IN_TRANSIT} and not shipment.shipped_at:
            shipment.shipped_at = now
        shipment.save()

        record_shipment_event(shipment, status=target_status, description=description, location=location, source=source, actor_check=False)

        if target_status == ShipmentStatus.DELIVERED:
            _maybe_mark_fulfillment_delivered(fulfillment)
        elif target_status == ShipmentStatus.RETURNED and fulfillment.status != FulfillmentStatus.RETURNED:
            fulfillment.status = FulfillmentStatus.RETURNED
            fulfillment.save(update_fields=["status", "updated_at"])

    _notify_shipment_status(shipment)
    return shipment


def _maybe_mark_fulfillment_delivered(fulfillment: Fulfillment) -> None:
    active_shipments = fulfillment.shipments.filter(is_deleted=False).exclude(status=ShipmentStatus.CANCELLED)
    if active_shipments.exists() and all(s.status == ShipmentStatus.DELIVERED for s in active_shipments):
        fulfillment.status = FulfillmentStatus.DELIVERED
        fulfillment.delivered_at = timezone.now()
        fulfillment.save(update_fields=["status", "delivered_at", "updated_at"])
        _notify_fulfillment_status(fulfillment)


_SHIPMENT_NOTIFICATION_COPY = {
    ShipmentStatus.PICKED_UP: ("Your package was picked up", "A courier has picked up your package."),
    ShipmentStatus.IN_TRANSIT: ("Your package is in transit", "Your package is on its way."),
    ShipmentStatus.OUT_FOR_DELIVERY: ("Your package is out for delivery", "Your package is out for delivery today."),
    ShipmentStatus.DELIVERED: ("Your package was delivered", "Your package has been delivered."),
    ShipmentStatus.DELIVERY_FAILED: ("Delivery attempt failed", "A delivery attempt for your package failed."),
    ShipmentStatus.RETURNED: ("Your package was returned", "Your package has been returned to the seller."),
}


def _notify_shipment_status(shipment: Shipment) -> None:
    copy = _SHIPMENT_NOTIFICATION_COPY.get(shipment.status)
    if not copy:
        return
    title, body = copy
    fulfillment = shipment.fulfillment
    create_notification(
        user_id=str(fulfillment.order.buyer_id),
        type=f"marketplace.shipment.{shipment.status}",
        title=title,
        body=body,
        target_type="shipment",
        target_id=str(shipment.id),
        context={"order_id": str(fulfillment.order_id), "shipment_reference": shipment.reference},
        dedup_key=f"shipment-{shipment.id}-{shipment.status}",
    )


def record_shipment_event(shipment: Shipment, *, status: str, description: str = "", location: str = "", source: str = ShipmentEventSource.MANUAL, external_event_id: str = "", metadata: dict | None = None, actor_check: bool = True, actor=None) -> ShipmentEvent:
    """Append a tracking event. When external_event_id is provided (a
    carrier webhook), this is idempotent: a second delivery of the same
    event id for the same shipment returns the existing row instead of
    creating a duplicate (spec §16/§24) - carrier webhooks are exactly the
    kind of at-least-once delivery this guards against."""
    if actor_check and actor is not None and not _provider_can_manage_shop_or_staff(actor, shipment.fulfillment.shop):
        raise PermissionDenied("Only the seller, a shop manager, or staff can add tracking events to this shipment.")
    if external_event_id:
        existing = ShipmentEvent.objects.filter(shipment=shipment, external_event_id=external_event_id).first()
        if existing:
            return existing
    return ShipmentEvent.objects.create(
        shipment=shipment,
        status=status,
        description=description,
        location=location,
        source=source,
        external_event_id=external_event_id,
        metadata=metadata or {},
    )
