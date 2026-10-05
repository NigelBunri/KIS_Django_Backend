from rest_framework import serializers

from .returns_models import Refund, ReturnItem, ReturnReason, ReturnRequest


class ReturnItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="order_item.product.name", read_only=True)

    class Meta:
        model = ReturnItem
        fields = ("id", "order_item", "product_name", "quantity")
        read_only_fields = fields


class RefundSerializer(serializers.ModelSerializer):
    class Meta:
        model = Refund
        fields = (
            "id", "order", "return_request", "amount_cents", "reason", "status",
            "provider", "provider_ref", "failure_reason", "processed_at", "created_at",
        )
        read_only_fields = fields


class ReturnRequestSerializer(serializers.ModelSerializer):
    items = ReturnItemSerializer(many=True, read_only=True)
    refunds = RefundSerializer(many=True, read_only=True)
    buyer_username = serializers.CharField(source="buyer.username", read_only=True)
    shop_name = serializers.CharField(source="order.shop.name", read_only=True)

    class Meta:
        model = ReturnRequest
        fields = (
            "id", "order", "buyer", "buyer_username", "shop_name", "status", "reason", "explanation",
            "reviewed_by", "reviewed_at", "seller_notes", "rejection_reason",
            "return_carrier_name", "return_tracking_number", "return_shipped_at",
            "received_at", "inspected_at", "completed_at", "items", "refunds", "created_at",
        )
        read_only_fields = (
            "id", "buyer", "buyer_username", "shop_name", "status", "reviewed_by", "reviewed_at",
            "rejection_reason", "return_carrier_name", "return_tracking_number", "return_shipped_at",
            "received_at", "inspected_at", "completed_at", "items", "refunds", "created_at",
        )


class CreateReturnRequestSerializer(serializers.Serializer):
    order_id = serializers.UUIDField()
    reason = serializers.ChoiceField(choices=ReturnReason.choices)
    explanation = serializers.CharField(required=False, allow_blank=True, default="")
    items = serializers.ListField(child=serializers.DictField(), allow_empty=False)


class ReturnReviewSerializer(serializers.Serializer):
    seller_notes = serializers.CharField(required=False, allow_blank=True, default="")


class ReturnRejectSerializer(serializers.Serializer):
    rejection_reason = serializers.CharField()


class ReturnShipSerializer(serializers.Serializer):
    carrier_name = serializers.CharField(required=False, allow_blank=True, default="")
    tracking_number = serializers.CharField(required=False, allow_blank=True, default="")
