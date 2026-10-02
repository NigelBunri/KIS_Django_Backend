from rest_framework import serializers

from .models import Product
from .serializers import MarketplaceOrderItemCreateSerializer
from .shipping_models import (
    CustomerAddress,
    Fulfillment,
    Shipment,
    ShipmentEvent,
    ShipmentItem,
    ShippingMethod,
    ShippingRate,
    ShippingZone,
)


class CustomerAddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerAddress
        fields = (
            'id', 'label', 'recipient_name', 'recipient_phone',
            'country', 'state', 'city', 'district', 'street_address', 'apartment',
            'postal_code', 'delivery_instructions', 'latitude', 'longitude',
            'is_default', 'validation_status', 'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'validation_status', 'created_at', 'updated_at')

    def validate_country(self, value):
        normalized = str(value or '').strip().upper()
        if len(normalized) != 2:
            raise serializers.ValidationError('Use a 2-letter country code (e.g. NG, US).')
        return normalized


class ShippingZoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShippingZone
        fields = ('id', 'shop', 'name', 'country', 'state', 'city', 'postal_code_prefix', 'is_active', 'priority', 'created_at')
        read_only_fields = ('id', 'created_at')

    def validate_country(self, value):
        normalized = str(value or '').strip().upper()
        if len(normalized) != 2:
            raise serializers.ValidationError('Use a 2-letter country code (e.g. NG, US).')
        return normalized


class ShippingRateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShippingRate
        fields = ('id', 'method', 'zone', 'rate_type', 'base_cents', 'free_above_cents', 'is_active', 'created_at')
        read_only_fields = ('id', 'created_at')

    def validate(self, attrs):
        method = attrs.get('method') or getattr(self.instance, 'method', None)
        zone = attrs.get('zone') or getattr(self.instance, 'zone', None)
        if method and zone and method.shop_id != zone.shop_id:
            raise serializers.ValidationError('The shipping method and zone must belong to the same shop.')
        return attrs


class ShippingMethodSerializer(serializers.ModelSerializer):
    rates = ShippingRateSerializer(many=True, read_only=True)

    class Meta:
        model = ShippingMethod
        fields = (
            'id', 'shop', 'name', 'description', 'method_type', 'provider_code',
            'is_active', 'min_days', 'max_days', 'rates', 'created_at',
        )
        read_only_fields = ('id', 'created_at')

    def validate(self, attrs):
        min_days = attrs.get('min_days', getattr(self.instance, 'min_days', 1))
        max_days = attrs.get('max_days', getattr(self.instance, 'max_days', 3))
        if min_days > max_days:
            raise serializers.ValidationError({'min_days': 'min_days cannot be greater than max_days.'})
        return attrs


class ShippingOptionsRequestSerializer(serializers.Serializer):
    """Request shape for the checkout 'what can I pick' step - mirrors
    MarketplaceOrderCreateSerializer's items exactly, since this must price
    the same cart the way checkout itself will, server-side (spec §8)."""
    shop_id = serializers.UUIDField()
    address_id = serializers.UUIDField()
    items = MarketplaceOrderItemCreateSerializer(many=True)


class ShipmentEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShipmentEvent
        fields = ('id', 'status', 'description', 'source', 'location', 'occurred_at', 'metadata', 'created_at')
        read_only_fields = fields


class ShipmentItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='order_item.product.name', read_only=True)

    class Meta:
        model = ShipmentItem
        fields = ('id', 'order_item', 'product_name', 'quantity')
        read_only_fields = ('id', 'product_name')


class ShipmentSerializer(serializers.ModelSerializer):
    items = ShipmentItemSerializer(many=True, read_only=True)
    events = ShipmentEventSerializer(many=True, read_only=True)

    class Meta:
        model = Shipment
        fields = (
            'id', 'fulfillment', 'reference', 'carrier_name', 'tracking_number',
            'shipping_method', 'status', 'origin_snapshot', 'destination_snapshot',
            'shipping_cost_cents', 'estimated_delivery_min', 'estimated_delivery_max',
            'shipped_at', 'delivered_at', 'cancelled_at', 'items', 'events', 'created_at',
        )
        read_only_fields = (
            'id', 'reference', 'status', 'origin_snapshot', 'destination_snapshot',
            'shipping_cost_cents', 'shipped_at', 'delivered_at', 'cancelled_at', 'created_at',
        )


class CreateShipmentSerializer(serializers.Serializer):
    """Write-only input for POST .../shipments/ - deliberately separate from
    ShipmentSerializer so the nested items/events read shape never doubles
    as a write contract."""
    items = serializers.ListField(child=serializers.DictField(), allow_empty=False)
    carrier_name = serializers.CharField(required=False, allow_blank=True, default='')
    tracking_number = serializers.CharField(required=False, allow_blank=True, default='')
    idempotency_key = serializers.CharField(required=False, allow_blank=True, default='')


class ShipmentTransitionSerializer(serializers.Serializer):
    status = serializers.CharField()
    description = serializers.CharField(required=False, allow_blank=True, default='')
    location = serializers.CharField(required=False, allow_blank=True, default='')


class ShipmentEventCreateSerializer(serializers.Serializer):
    description = serializers.CharField(required=False, allow_blank=True, default='')
    location = serializers.CharField(required=False, allow_blank=True, default='')


class FulfillmentOrderItemSerializer(serializers.Serializer):
    """Per-order-item shipping state, for the seller's 'create shipment'
    screen - how much of this line was ordered vs. already shipped, so the
    UI can default to shipping what remains without a second round trip."""
    order_item_id = serializers.CharField(source='id')
    product_name = serializers.CharField(source='product.name')
    quantity = serializers.IntegerField()
    shipped_quantity = serializers.SerializerMethodField()

    def get_shipped_quantity(self, order_item):
        from .shipping_services import _shipped_quantity_for_item
        return _shipped_quantity_for_item(order_item.id)


class FulfillmentSerializer(serializers.ModelSerializer):
    shipments = ShipmentSerializer(many=True, read_only=True)
    shop_name = serializers.CharField(source='shop.name', read_only=True)
    order_buyer_id = serializers.UUIDField(source='order.buyer_id', read_only=True)
    order_line_items = serializers.SerializerMethodField()

    class Meta:
        model = Fulfillment
        fields = (
            'id', 'order', 'order_buyer_id', 'shop', 'shop_name', 'status',
            'shipping_method', 'shipping_zone', 'shipping_cost_cents',
            'delivery_address_snapshot', 'estimated_delivery_min', 'estimated_delivery_max',
            'accepted_at', 'ready_at', 'shipped_at', 'delivered_at', 'cancelled_at',
            'shipments', 'order_line_items', 'created_at',
        )
        read_only_fields = fields

    def get_order_line_items(self, fulfillment):
        items = fulfillment.order.items.select_related('product').all()
        return FulfillmentOrderItemSerializer(items, many=True).data


class FulfillmentTransitionSerializer(serializers.Serializer):
    status = serializers.CharField()
