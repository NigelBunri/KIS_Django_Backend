
from rest_framework import serializers
from . import models


class NotificationTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.NotificationTemplate
        fields = "__all__"


class NotificationDeliverySerializer(serializers.ModelSerializer):
    class Meta:
        model = models.NotificationDelivery
        fields = "__all__"


class NotificationSerializer(serializers.ModelSerializer):
    deliveries = NotificationDeliverySerializer(many=True, read_only=True)

    class Meta:
        model = models.Notification
        fields = "__all__"
        read_only_fields = ("delivered_at", "read_at", "is_read")


class NotificationRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.NotificationRule
        fields = "__all__"
        read_only_fields = ("id", "user_id", "created_at", "updated_at", "is_deleted")


class NotificationDeviceTokenSerializer(serializers.ModelSerializer):
    masked_push_token = serializers.SerializerMethodField()

    class Meta:
        model = models.NotificationDeviceToken
        fields = (
            "id",
            "user_id",
            "device_id",
            "platform",
            "token_type",
            "masked_push_token",
            "enabled",
            "last_seen_at",
            "metadata",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_masked_push_token(self, obj):
        token = str(getattr(obj, "push_token", "") or "")
        if len(token) <= 10:
            return "***"
        return f"{token[:6]}...{token[-4:]}"


class NotificationDigestSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.NotificationDigest
        fields = "__all__"
