"""Serializers for audit entries and suspicious flags."""
from rest_framework import serializers

from admin_control.models import AdminAuditEntry, SuspiciousActivityFlag


class AuditEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = AdminAuditEntry
        fields = [
            "id",
            "actor",
            "action_type",
            "target_app",
            "target_model",
            "target_pk",
            "severity",
            "metadata",
            "created_at",
        ]


class SuspiciousActivityFlagSerializer(serializers.ModelSerializer):
    class Meta:
        model = SuspiciousActivityFlag
        fields = [
            "id",
            "actor",
            "reason",
            "path",
            "severity",
            "metadata",
            "resolved",
            "acknowledged_at",
            "created_at",
        ]


class AuditActionSerializer(serializers.Serializer):
    action_type = serializers.CharField()
    target_app = serializers.CharField(required=False, allow_blank=True)
    target_model = serializers.CharField(required=False, allow_blank=True)
    target_pk = serializers.CharField(required=False, allow_blank=True)
    severity = serializers.ChoiceField(choices=AdminAuditEntry.Severity.choices, default=AdminAuditEntry.Severity.INFO)
    metadata = serializers.JSONField(required=False, default=dict)
