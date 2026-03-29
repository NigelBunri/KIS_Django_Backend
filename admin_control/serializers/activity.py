"""Serializers for admin control activity stream."""
from rest_framework import serializers

from admin_control.models import AdminUserActivity


class ActivityStreamSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdminUserActivity
        fields = [
            "id",
            "actor_id",
            "path",
            "method",
            "status_code",
            "ip_address",
            "device",
            "duration_ms",
            "response_size",
            "created_at",
        ]
