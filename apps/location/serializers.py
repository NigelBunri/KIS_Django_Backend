from __future__ import annotations

from rest_framework import serializers

from .models import (
    PartnerLocationEvent,
    PartnerLocationZone,
    PartnerLocationAttendance,
    PartnerLocationConsent,
    PartnerLocationAuditLog,
)


class PartnerLocationZoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartnerLocationZone
        fields = ["id", "name", "center_lat", "center_lng", "radius_meters", "is_active", "created_at"]
        read_only_fields = ["id", "created_at"]


class PartnerLocationEventSerializer(serializers.ModelSerializer):
    extra_zones = PartnerLocationZoneSerializer(many=True, read_only=True)
    attendance_count = serializers.SerializerMethodField()
    is_checkin_open = serializers.SerializerMethodField()

    class Meta:
        model = PartnerLocationEvent
        fields = [
            "id", "title", "description", "status",
            "start_dt", "end_dt",
            "checkin_opens_before_minutes", "late_after_minutes",
            "recurrence", "recurrence_days", "recurrence_until",
            "target_type", "target_roles", "target_user_ids", "target_ref_id",
            "center_lat", "center_lng", "radius_meters",
            "show_arrival_order_to_members", "show_checkin_count_to_members",
            "is_active", "extra_zones", "attendance_count", "is_checkin_open",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "extra_zones", "attendance_count", "is_checkin_open", "created_at", "updated_at"]

    def get_attendance_count(self, obj):
        return obj.attendances.count()

    def get_is_checkin_open(self, obj):
        return obj.is_checkin_open


class PartnerLocationAttendanceSerializer(serializers.ModelSerializer):
    user_display = serializers.SerializerMethodField()

    class Meta:
        model = PartnerLocationAttendance
        fields = [
            "id", "user_id", "user_display",
            "checked_in_at", "is_late", "arrival_number",
            "distance_from_center_m", "location_verified",
            "source", "is_manual",
            "manually_adjusted_at",
        ]
        read_only_fields = ["id", "user_id", "user_display", "checked_in_at", "arrival_number", "manually_adjusted_at"]

    def get_user_display(self, obj):
        user = obj.user
        return {
            "id": str(user.id),
            "display_name": getattr(user, "display_name", None) or getattr(user, "username", None) or str(user.id),
            "avatar_url": getattr(user, "avatar_url", None),
        }


class MemberAttendanceStatusSerializer(serializers.ModelSerializer):
    """Minimal status view for a member looking at their own check-in."""

    class Meta:
        model = PartnerLocationAttendance
        fields = ["id", "checked_in_at", "is_late", "arrival_number", "distance_from_center_m"]


class PartnerLocationConsentSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartnerLocationConsent
        fields = ["id", "granted", "granted_at", "revoked_at", "is_minor", "notes"]
        read_only_fields = ["id", "granted_at", "revoked_at"]


class PartnerLocationAuditLogSerializer(serializers.ModelSerializer):
    actor_display = serializers.SerializerMethodField()

    class Meta:
        model = PartnerLocationAuditLog
        fields = ["id", "action", "actor_display", "metadata", "created_at"]

    def get_actor_display(self, obj):
        if not obj.actor:
            return None
        return getattr(obj.actor, "display_name", None) or str(obj.actor_id)
