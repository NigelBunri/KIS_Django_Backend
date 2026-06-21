# apps/family/serializers.py
from rest_framework import serializers

from .models import (
    FamilyAccount,
    FamilyMember,
    ParentalControl,
    FamilyEvent,
    FamilyPhotoAlbum,
    FamilyPhoto,
    MemorialPage,
    MilestoneRecord,
    TimeCapsule,
    FamilyPrayerItem,
    FamilyNoticeBoard,
    GriefSupportGroup,
    GriefGroupMembership,
)


class FamilyAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = FamilyAccount
        fields = [
            "id",
            "admin_user",
            "name",
            "description",
            "invite_code",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class FamilyMemberSerializer(serializers.ModelSerializer):
    class Meta:
        model = FamilyMember
        fields = [
            "id",
            "family",
            "user",
            "role",
            "nickname",
            "birth_date",
            "is_admin",
            "is_minor",
            "added_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class ParentalControlSerializer(serializers.ModelSerializer):
    class Meta:
        model = ParentalControl
        fields = [
            "id",
            "family_member",
            "content_filter_level",
            "allowed_contact_ids",
            "screen_time_minutes_per_day",
            "time_restrictions",
            "restricted_sections",
            "location_sharing",
            "sos_enabled",
            "activity_report_frequency",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class FamilyEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = FamilyEvent
        fields = [
            "id",
            "family",
            "title",
            "type",
            "event_date",
            "reminder_minutes_before",
            "notes",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class FamilyPhotoAlbumSerializer(serializers.ModelSerializer):
    class Meta:
        model = FamilyPhotoAlbum
        fields = [
            "id",
            "family",
            "name",
            "description",
            "cover_url",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class FamilyPhotoSerializer(serializers.ModelSerializer):
    class Meta:
        model = FamilyPhoto
        fields = [
            "id",
            "album",
            "uploader",
            "file_url",
            "caption",
            "taken_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class MemorialPageSerializer(serializers.ModelSerializer):
    class Meta:
        model = MemorialPage
        fields = [
            "id",
            "family",
            "name",
            "birth_date",
            "death_date",
            "tribute",
            "photo_url",
            "is_public",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class MilestoneRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = MilestoneRecord
        fields = [
            "id",
            "family_member",
            "title",
            "type",
            "milestone_date",
            "description",
            "photo_url",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class TimeCapsuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = TimeCapsule
        fields = [
            "id",
            "family",
            "title",
            "message",
            "unlock_date",
            "is_locked",
            "creator",
            "media_urls",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class TimeCapsuleLockedSerializer(serializers.ModelSerializer):
    """Serializer that hides message when capsule is still locked."""

    message = serializers.SerializerMethodField()

    class Meta:
        model = TimeCapsule
        fields = [
            "id",
            "family",
            "title",
            "message",
            "unlock_date",
            "is_locked",
            "creator",
            "media_urls",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_message(self, obj):
        from django.utils import timezone
        today = timezone.now().date()
        if obj.unlock_date <= today:
            return obj.message
        return None


class FamilyPrayerItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = FamilyPrayerItem
        fields = [
            "id",
            "family",
            "user",
            "text",
            "is_answered",
            "answered_note",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class FamilyNoticeBoardSerializer(serializers.ModelSerializer):
    class Meta:
        model = FamilyNoticeBoard
        fields = [
            "id",
            "family",
            "posted_by",
            "title",
            "content",
            "is_pinned",
            "expires_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class GriefSupportGroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = GriefSupportGroup
        fields = [
            "id",
            "name",
            "type",
            "facilitator",
            "description",
            "is_anonymous_allowed",
            "member_count",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "member_count", "created_at", "updated_at"]


class GriefGroupMembershipSerializer(serializers.ModelSerializer):
    class Meta:
        model = GriefGroupMembership
        fields = [
            "id",
            "group",
            "user",
            "is_anonymous",
            "join_date",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "join_date", "created_at", "updated_at"]


class JoinFamilySerializer(serializers.Serializer):
    invite_code = serializers.CharField(max_length=12)


class SOSAlertSerializer(serializers.Serializer):
    family_id = serializers.UUIDField()
    message = serializers.CharField(required=False, default="SOS Alert triggered")
    location = serializers.CharField(required=False, allow_blank=True)
