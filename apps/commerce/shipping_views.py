from django.db import transaction
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Shop
from .services import _normalize_marketplace_items, _provider_can_manage_shop
from .shipping_models import (
    CustomerAddress,
    Fulfillment,
    FulfillmentStatus,
    Shipment,
    ShipmentEventSource,
    ShippingMethod,
    ShippingRate,
    ShippingZone,
)
from .shipping_serializers import (
    CreateShipmentSerializer,
    CustomerAddressSerializer,
    FulfillmentSerializer,
    FulfillmentTransitionSerializer,
    ShipmentEventCreateSerializer,
    ShipmentSerializer,
    ShipmentTransitionSerializer,
    ShippingMethodSerializer,
    ShippingOptionsRequestSerializer,
    ShippingRateSerializer,
    ShippingZoneSerializer,
)
from .shipping_services import (
    RateableItem,
    create_shipment,
    list_shipping_options,
    record_shipment_event,
    transition_fulfillment,
    transition_shipment,
)


def _is_staff_or_manages_shop(user, shop) -> bool:
    return bool(getattr(user, "is_staff", False)) or _provider_can_manage_shop(user, shop)


# ---------------------------------------------------------------------------
# Customer: addresses
# ---------------------------------------------------------------------------

class CustomerAddressViewSet(viewsets.ModelViewSet):
    """A buyer's own saved delivery addresses - strictly self-scoped, no
    staff override, since there is no legitimate reason for anyone but the
    owner to list or edit another person's saved address book."""

    serializer_class = CustomerAddressSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return CustomerAddress.objects.filter(user=self.request.user, is_deleted=False).order_by("-is_default", "-created_at")

    def perform_create(self, serializer):
        is_default = serializer.validated_data.get("is_default", False)
        if is_default:
            CustomerAddress.objects.filter(user=self.request.user).update(is_default=False)
        serializer.save(user=self.request.user)

    def perform_update(self, serializer):
        if serializer.validated_data.get("is_default"):
            CustomerAddress.objects.filter(user=self.request.user).exclude(id=serializer.instance.id).update(is_default=False)
        serializer.save()

    def perform_destroy(self, instance):
        instance.is_deleted = True
        instance.save(update_fields=["is_deleted", "updated_at"])

    @action(detail=True, methods=["post"])
    def set_default(self, request, pk=None):
        address = self.get_object()
        CustomerAddress.objects.filter(user=request.user).exclude(id=address.id).update(is_default=False)
        address.is_default = True
        address.save(update_fields=["is_default", "updated_at"])
        return Response(self.get_serializer(address).data)


# ---------------------------------------------------------------------------
# Seller: zones / methods / rates configuration
# ---------------------------------------------------------------------------

class _ShopScopedShippingConfigViewSet(viewsets.ModelViewSet):
    """Shared shape for ShippingZone/ShippingMethod/ShippingRate - all three
    are configured per-shop by that shop's own owner/manager/partner-manager
    (or staff), never cross-shop. Subclasses only need `shop_field_path`
    (how to reach the owning Shop from the model instance) and the model's
    own `shop` FK for the create-time check."""

    permission_classes = [permissions.IsAuthenticated]
    shop_lookup_field = "shop"  # overridden by ShippingRate (reaches shop via method)

    def _shop_of(self, instance):
        return instance.shop if self.shop_lookup_field == "shop" else getattr(instance, self.shop_lookup_field).shop

    def get_queryset(self):
        qs = self.queryset_base()
        prefix = "shop__" if self.shop_lookup_field == "shop" else f"{self.shop_lookup_field}__shop__"

        shop_id = self.request.query_params.get("shop")
        if shop_id:
            qs = qs.filter(**{f"{prefix}id": shop_id})

        user = self.request.user
        if getattr(user, "is_staff", False):
            return qs
        managed_shop_ids = [s.id for s in Shop.objects.filter(owner=user)] + [
            s.id for s in Shop.objects.filter(team_members__user=user, team_members__is_active=True)
        ]
        return qs.filter(**{f"{prefix}in": managed_shop_ids})

    def queryset_base(self):
        raise NotImplementedError

    def perform_create(self, serializer):
        shop = serializer.validated_data.get("shop") or getattr(serializer.validated_data.get(self.shop_lookup_field), "shop", None)
        if not shop or not _is_staff_or_manages_shop(self.request.user, shop):
            raise PermissionDenied("You can only configure shipping for a shop you manage.")
        serializer.save()

    def perform_update(self, serializer):
        shop = self._shop_of(serializer.instance)
        if not _is_staff_or_manages_shop(self.request.user, shop):
            raise PermissionDenied("You can only configure shipping for a shop you manage.")
        serializer.save()

    def perform_destroy(self, instance):
        shop = self._shop_of(instance)
        if not _is_staff_or_manages_shop(self.request.user, shop):
            raise PermissionDenied("You can only configure shipping for a shop you manage.")
        instance.is_deleted = True
        instance.save(update_fields=["is_deleted", "updated_at"])


class ShippingZoneViewSet(_ShopScopedShippingConfigViewSet):
    serializer_class = ShippingZoneSerializer
    shop_lookup_field = "shop"

    def queryset_base(self):
        return ShippingZone.objects.filter(is_deleted=False).order_by("-priority", "name")


class ShippingMethodViewSet(_ShopScopedShippingConfigViewSet):
    serializer_class = ShippingMethodSerializer
    shop_lookup_field = "shop"

    def queryset_base(self):
        return ShippingMethod.objects.filter(is_deleted=False).prefetch_related("rates").order_by("name")


class ShippingRateViewSet(_ShopScopedShippingConfigViewSet):
    serializer_class = ShippingRateSerializer
    shop_lookup_field = "method"

    def queryset_base(self):
        return ShippingRate.objects.filter(is_deleted=False).select_related("method", "zone").order_by("-created_at")


# ---------------------------------------------------------------------------
# Customer: shipping options at checkout
# ---------------------------------------------------------------------------

class ShippingOptionsView(APIView):
    """POST the same {shop_id, address_id, items} a checkout is about to
    submit; get back every shipping method actually available, each with
    its server-computed cost and delivery estimate. Pure read - no order,
    fulfillment, or shipment is created here. place_marketplace_order
    re-derives this exact same calculation again at order-creation time
    rather than trusting whatever the client echoes back from this
    response (spec §8)."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ShippingOptionsRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        shop = Shop.objects.filter(id=data["shop_id"]).first()
        if not shop:
            raise NotFound("Shop not found.")
        address = CustomerAddress.objects.filter(id=data["address_id"], user=request.user, is_deleted=False).first()
        if not address:
            raise NotFound("Delivery address not found.")

        # _normalize_marketplace_items locks each Product row
        # (select_for_update), which Postgres only allows inside an open
        # transaction - place_marketplace_order always has one (it's
        # writing an order), but this is a read-only preview with no
        # transaction.atomic() of its own otherwise. The lock is released
        # the instant this block exits; nothing here writes.
        with transaction.atomic():
            normalized_items = _normalize_marketplace_items(
                [dict(item) for item in data["items"]], shop,
            )
        subtotal_cents = sum(i["quantity"] * i["unit_price_cents"] for i in normalized_items)
        rateable_items = [RateableItem(quantity=i["quantity"], weight_kg=i["product"].weight_kg) for i in normalized_items]

        options = list_shipping_options(shop=shop, address=address, subtotal_cents=subtotal_cents, items=rateable_items)
        return Response({"subtotal_cents": subtotal_cents, "options": options})


# ---------------------------------------------------------------------------
# Seller + admin: fulfillment queue
# ---------------------------------------------------------------------------

_FULFILLMENT_ACTION_TARGETS = {
    "accept": FulfillmentStatus.ACCEPTED,
    "start_processing": FulfillmentStatus.PROCESSING,
    "mark_ready": FulfillmentStatus.READY_FOR_SHIPMENT,
    "cancel": FulfillmentStatus.CANCELLED,
}


class FulfillmentViewSet(viewsets.GenericViewSet, mixins.ListModelMixin, mixins.RetrieveModelMixin):
    """The seller's 'orders to prepare' queue. Staff see every shop's
    fulfillments (for exception handling - spec §20); a seller sees only
    fulfillments for shops they own/manage, enforced in get_queryset so a
    seller can never even discover another shop's fulfillment id, let alone
    act on it (confirmed pattern: unrelated ids 404 here exactly like
    MarketplaceOrderViewSet already does for orders)."""

    serializer_class = FulfillmentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = Fulfillment.objects.filter(is_deleted=False).select_related("shop", "order", "shipping_method", "shipping_zone").prefetch_related("shipments__items", "shipments__events").order_by("-created_at")
        user = self.request.user
        if getattr(user, "is_staff", False):
            return qs
        managed_shop_ids = [s.id for s in Shop.objects.filter(owner=user)] + [
            s.id for s in Shop.objects.filter(team_members__user=user, team_members__is_active=True)
        ]
        return qs.filter(shop_id__in=managed_shop_ids)

    def _dispatch_transition(self, request, pk, target_status):
        # get_object() applies get_queryset()'s shop-scoping first, so an
        # unrelated seller's fulfillment id 404s here - the same "don't even
        # confirm it exists" behavior every other cross-shop lookup in this
        # app has (MarketplaceOrderViewSet, ShopTeamMemberViewSet, ...).
        # Calling transition_fulfillment(pk, ...) directly would skip that
        # scoping and leak existence via a 403 instead (caught by
        # test_seller_a_cannot_accept_seller_bs_fulfillment).
        fulfillment = self.get_object()
        fulfillment = transition_fulfillment(fulfillment.id, target_status=target_status, actor=request.user)
        return Response(self.get_serializer(fulfillment).data)

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        return self._dispatch_transition(request, pk, _FULFILLMENT_ACTION_TARGETS["accept"])

    @action(detail=True, methods=["post"], url_path="start-processing")
    def start_processing(self, request, pk=None):
        return self._dispatch_transition(request, pk, _FULFILLMENT_ACTION_TARGETS["start_processing"])

    @action(detail=True, methods=["post"], url_path="mark-ready")
    def mark_ready(self, request, pk=None):
        return self._dispatch_transition(request, pk, _FULFILLMENT_ACTION_TARGETS["mark_ready"])

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return self._dispatch_transition(request, pk, _FULFILLMENT_ACTION_TARGETS["cancel"])


# ---------------------------------------------------------------------------
# Seller + admin: shipments
# ---------------------------------------------------------------------------

class ShipmentViewSet(viewsets.GenericViewSet, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin):
    serializer_class = ShipmentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = Shipment.objects.filter(is_deleted=False).select_related("fulfillment", "fulfillment__shop").prefetch_related("items", "events").order_by("-created_at")
        user = self.request.user
        if getattr(user, "is_staff", False):
            return qs
        managed_shop_ids = [s.id for s in Shop.objects.filter(owner=user)] + [
            s.id for s in Shop.objects.filter(team_members__user=user, team_members__is_active=True)
        ]
        return qs.filter(fulfillment__shop_id__in=managed_shop_ids)

    def create(self, request, *args, **kwargs):
        fulfillment_id = request.data.get("fulfillment_id") or request.data.get("fulfillment")
        if not fulfillment_id:
            raise ValidationError({"fulfillment_id": "This field is required."})
        body = CreateShipmentSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        shipment = create_shipment(
            fulfillment_id=fulfillment_id,
            actor=request.user,
            items=body.validated_data["items"],
            carrier_name=body.validated_data["carrier_name"],
            tracking_number=body.validated_data["tracking_number"],
            idempotency_key=body.validated_data.get("idempotency_key") or None,
        )
        return Response(self.get_serializer(shipment).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        # See FulfillmentViewSet._dispatch_transition's comment - get_object()
        # must run first so an unrelated seller's shipment id 404s instead of
        # leaking existence via a 403 from the service-layer check alone.
        existing = self.get_object()
        body = ShipmentTransitionSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        shipment = transition_shipment(
            shipment_id=existing.id, actor=request.user,
            target_status=body.validated_data["status"],
            description=body.validated_data["description"],
            location=body.validated_data["location"],
        )
        return Response(self.get_serializer(shipment).data)

    @action(detail=True, methods=["post"])
    def events(self, request, pk=None):
        """A pure informational tracking update (e.g. 'arrived at Lagos hub')
        that doesn't change the shipment's overall status - status
        transitions go through POST .../transition/ instead, which is the
        only path that validates against SHIPMENT_TRANSITIONS."""
        shipment = self.get_object()
        body = ShipmentEventCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        record_shipment_event(
            shipment, status=shipment.status,
            description=body.validated_data["description"],
            location=body.validated_data["location"],
            source=ShipmentEventSource.MANUAL,
            actor=request.user,
        )
        shipment.refresh_from_db()
        return Response(self.get_serializer(shipment).data)
