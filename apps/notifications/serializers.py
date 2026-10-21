
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
    # Gated, enriched view of actor_user_id (see that field's docstring on
    # the model) - avatar/work_status/hiring_job_listing_id resolved and
    # visibility-checked the same way every other avatar context in the
    # app already is, so a notification never leaks a restricted actor's
    # status through this side channel. None whenever actor_user_id is
    # unset (most notification types) or the actor user can no longer be
    # resolved (deleted account).
    actor = serializers.SerializerMethodField()

    class Meta:
        model = models.Notification
        fields = "__all__"
        read_only_fields = ("delivered_at", "read_at", "is_read")

    def get_actor(self, obj):
        if not obj.actor_user_id:
            return None
        from apps.accounts.models import User
        from apps.accounts.visibility import get_visible_hiring_job_listing_id, get_visible_work_status

        try:
            actor = User.objects.select_related("profile").get(id=obj.actor_user_id)
        except User.DoesNotExist:
            return None
        viewer = getattr(self.context.get("request"), "user", None)
        profile = getattr(actor, "profile", None)
        return {
            "id": str(actor.id),
            "display_name": actor.display_name or actor.username,
            "avatar_url": getattr(profile, "avatar_url", None),
            "work_status": get_visible_work_status(actor, viewer),
            "hiring_job_listing_id": get_visible_hiring_job_listing_id(actor, viewer),
        }


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
