# moderation/serializers.py
from rest_framework import serializers
from . import models


class FlagSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Flag
        fields = "__all__"


class ModerationActionSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.ModerationAction
        fields = "__all__"


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.AuditLog
        fields = "__all__"


class UserReputationSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.UserReputation
        fields = "__all__"


class ModerationRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.ModerationRule
        fields = "__all__"


class SafetyAlertSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.SafetyAlert
        fields = "__all__"


class UserBlockSerializer(serializers.ModelSerializer):
    blocked = serializers.PrimaryKeyRelatedField(
        queryset=models.UserBlock._meta.get_field("blocked").remote_field.model.objects.all(),
        required=False,
    )
    blocked_id = serializers.PrimaryKeyRelatedField(
        queryset=models.UserBlock._meta.get_field("blocked").remote_field.model.objects.all(),
        source="blocked",
        write_only=True,
        required=False,
    )

    class Meta:
        model = models.UserBlock
        fields = (
            "id",
            "blocker",
            "blocked",
            "blocked_id",
            "reason",
            "created_at",
            "updated_at",
            "is_deleted",
        )
        read_only_fields = ("id", "blocker", "created_at", "updated_at", "is_deleted")
        validators = []

    def validate(self, attrs):
        request = self.context.get("request")
        blocked = attrs.get("blocked")
        if not blocked:
            raise serializers.ValidationError({"blocked": "A user to mute is required."})
        if request and getattr(request, "user", None) and blocked == request.user:
            raise serializers.ValidationError({"blocked": "You cannot mute yourself."})
        return attrs
