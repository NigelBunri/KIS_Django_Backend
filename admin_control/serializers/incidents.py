"""Serializers for security incident tracking."""
from rest_framework import serializers

from admin_control.models import SecurityIncident


class SecurityIncidentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SecurityIncident
        fields = [
            "id",
            "title",
            "description",
            "severity",
            "status",
            "reported_by",
            "discovered_at",
            "affected_user_count",
            "data_categories_affected",
            "regulatory_notification_required",
            "regulatory_notification_sent_at",
            "resolved_at",
            "resolution_summary",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "reported_by", "created_at", "updated_at"]


class SecurityIncidentCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    severity = serializers.ChoiceField(choices=SecurityIncident.Severity.choices, default=SecurityIncident.Severity.MEDIUM)
    discovered_at = serializers.DateTimeField()
    affected_user_count = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    data_categories_affected = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    regulatory_notification_required = serializers.BooleanField(required=False, allow_null=True)


class SecurityIncidentUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=SecurityIncident.Status.choices, required=False)
    severity = serializers.ChoiceField(choices=SecurityIncident.Severity.choices, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    affected_user_count = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    data_categories_affected = serializers.ListField(child=serializers.CharField(), required=False)
    regulatory_notification_required = serializers.BooleanField(required=False, allow_null=True)
    regulatory_notification_sent_at = serializers.DateTimeField(required=False, allow_null=True)
    resolution_summary = serializers.CharField(required=False, allow_blank=True)
