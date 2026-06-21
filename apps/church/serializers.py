from rest_framework import serializers
from .models import (
    ChurchGiving,
    TithePledge,
    OfferingCampaign,
    ChurchMembership,
    AttendanceRecord,
    ChurchLifeEvent,
    MemberBirthdayAnniversary,
    SmallGroup,
    SmallGroupMembership,
    SmallGroupAttendance,
    DiscipleshipJourney,
    SpiritualGiftsAssessment,
    AccountabilityPartner,
    PrayerRequest,
    PrayerWallEntry,
    FastingRecord,
    PrayerChainSlot,
    Song,
    SetList,
    MinistryDepartment,
    VolunteerSignup,
    OutreachCampaign,
    EvangelismRecord,
)


class OfferingCampaignSerializer(serializers.ModelSerializer):
    class Meta:
        model = OfferingCampaign
        fields = [
            "id",
            "name",
            "description",
            "target_amount",
            "currency",
            "raised_amount",
            "type",
            "start_date",
            "end_date",
            "is_active",
            "created_by",
            "banner_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "raised_amount"]


class ChurchGivingSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChurchGiving
        fields = [
            "id",
            "user",
            "amount",
            "currency",
            "type",
            "campaign",
            "is_anonymous",
            "payment_ref",
            "notes",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class TithePledgeSerializer(serializers.ModelSerializer):
    class Meta:
        model = TithePledge
        fields = [
            "id",
            "user",
            "amount",
            "currency",
            "period",
            "fulfilled_amount",
            "start_date",
            "end_date",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "fulfilled_amount"]


class ChurchMembershipSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChurchMembership
        fields = [
            "id",
            "user",
            "church_id",
            "tier",
            "join_date",
            "status",
            "baptism_date",
            "confirmation_date",
            "transfer_from",
            "transfer_to",
            "pastoral_notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class ChurchMembershipPublicSerializer(serializers.ModelSerializer):
    """Excludes pastoral_notes for non-leader access."""

    class Meta:
        model = ChurchMembership
        fields = [
            "id",
            "user",
            "church_id",
            "tier",
            "join_date",
            "status",
            "baptism_date",
            "confirmation_date",
            "transfer_from",
            "transfer_to",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class AttendanceRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceRecord
        fields = [
            "id",
            "user",
            "church_id",
            "date",
            "type",
            "check_in_method",
            "service_type",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class ChurchLifeEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChurchLifeEvent
        fields = [
            "id",
            "user",
            "church_id",
            "type",
            "event_date",
            "officiant",
            "notes",
            "witnesses",
            "certificate_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class MemberBirthdayAnniversarySerializer(serializers.ModelSerializer):
    class Meta:
        model = MemberBirthdayAnniversary
        fields = [
            "id",
            "user",
            "type",
            "date",
            "notify_church",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class SmallGroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = SmallGroup
        fields = [
            "id",
            "name",
            "church_id",
            "leader",
            "type",
            "district",
            "zone",
            "region",
            "meeting_schedule",
            "description",
            "max_members",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class SmallGroupMembershipSerializer(serializers.ModelSerializer):
    class Meta:
        model = SmallGroupMembership
        fields = [
            "id",
            "group",
            "user",
            "role",
            "join_date",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "join_date", "created_at", "updated_at"]


class SmallGroupAttendanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = SmallGroupAttendance
        fields = [
            "id",
            "group",
            "date",
            "present_members",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class DiscipleshipJourneySerializer(serializers.ModelSerializer):
    class Meta:
        model = DiscipleshipJourney
        fields = [
            "id",
            "user",
            "stage",
            "salvation_date",
            "baptism_date",
            "milestones",
            "mentor",
            "notes",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class SpiritualGiftsAssessmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SpiritualGiftsAssessment
        fields = [
            "id",
            "user",
            "completed_at",
            "results",
            "dominant_gifts",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "completed_at", "created_at", "updated_at"]


class AccountabilityPartnerSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccountabilityPartner
        fields = [
            "id",
            "user1",
            "user2",
            "focus_area",
            "started_at",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "started_at", "created_at", "updated_at"]


class PrayerRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = PrayerRequest
        fields = [
            "id",
            "user",
            "text",
            "privacy",
            "is_answered",
            "testimony",
            "category",
            "tags",
            "prayer_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "prayer_count", "created_at", "updated_at"]


class PrayerWallEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = PrayerWallEntry
        fields = [
            "id",
            "user",
            "text",
            "is_public",
            "prayer_count",
            "church_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "prayer_count", "created_at", "updated_at"]


class FastingRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = FastingRecord
        fields = [
            "id",
            "user",
            "type",
            "start_date",
            "end_date",
            "goal",
            "is_community",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class PrayerChainSlotSerializer(serializers.ModelSerializer):
    class Meta:
        model = PrayerChainSlot
        fields = [
            "id",
            "church_id",
            "user",
            "start_time",
            "end_time",
            "focus",
            "is_filled",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class SongSerializer(serializers.ModelSerializer):
    class Meta:
        model = Song
        fields = [
            "id",
            "title",
            "artist",
            "key",
            "tempo_bpm",
            "tags",
            "ccli_number",
            "lyrics",
            "chord_chart_url",
            "created_by",
            "church_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class SetListSerializer(serializers.ModelSerializer):
    class Meta:
        model = SetList
        fields = [
            "id",
            "title",
            "church_id",
            "created_by",
            "service_date",
            "songs",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class MinistryDepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = MinistryDepartment
        fields = [
            "id",
            "name",
            "church_id",
            "type",
            "leader",
            "description",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class VolunteerSignupSerializer(serializers.ModelSerializer):
    class Meta:
        model = VolunteerSignup
        fields = [
            "id",
            "user",
            "department",
            "church_id",
            "role",
            "event_date",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class OutreachCampaignSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutreachCampaign
        fields = [
            "id",
            "title",
            "church_id",
            "created_by",
            "description",
            "start_date",
            "end_date",
            "target_count",
            "current_count",
            "type",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class EvangelismRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = EvangelismRecord
        fields = [
            "id",
            "user",
            "date",
            "shares",
            "follow_ups",
            "salvations",
            "notes",
            "church_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
