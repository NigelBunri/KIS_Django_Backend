from rest_framework import serializers

from .extended_models import (
    AddictionRecoveryGroup,
    BabyMilestone,
    BloodTypeRegistry,
    ConsultReview,
    EMedication,
    EmergencyAlert,
    HealthGoal,
    MentalHealthJournal,
    MentalHealthSession,
    MoodEntry,
    PregnancyTracker,
    RecoveryMembership,
    RecoveryMilestone,
    TelemedicineConsult,
)


# ---------------------------------------------------------------------------
# Telemedicine
# ---------------------------------------------------------------------------

class TelemedicineConsultSerializer(serializers.ModelSerializer):
    class Meta:
        model = TelemedicineConsult
        fields = [
            "id",
            "patient",
            "doctor",
            "specialty",
            "type",
            "status",
            "scheduled_at",
            "started_at",
            "ended_at",
            "notes",
            "prescription_notes",
            "recording_url",
            "call_url",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class ConsultReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsultReview
        fields = [
            "id",
            "consult",
            "reviewer",
            "rating",
            "comment",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


# ---------------------------------------------------------------------------
# Mental Health
# ---------------------------------------------------------------------------

class MentalHealthSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = MentalHealthSession
        fields = [
            "id",
            "patient",
            "therapist",
            "type",
            "modality",
            "status",
            "scheduled_at",
            "session_notes",
            "is_faith_based",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class MoodEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = MoodEntry
        fields = [
            "id",
            "user",
            "mood_score",
            "emotion_tags",
            "journal_text",
            "entry_date",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class MentalHealthJournalSerializer(serializers.ModelSerializer):
    class Meta:
        model = MentalHealthJournal
        fields = [
            "id",
            "user",
            "title",
            "content",
            "mood_score",
            "is_private",
            "entry_date",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "entry_date", "created_at", "updated_at"]


# ---------------------------------------------------------------------------
# Addiction Recovery
# ---------------------------------------------------------------------------

class AddictionRecoveryGroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = AddictionRecoveryGroup
        fields = [
            "id",
            "name",
            "addiction_type",
            "facilitator",
            "description",
            "is_anonymous",
            "is_active",
            "meeting_schedule",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class RecoveryMembershipSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecoveryMembership
        fields = [
            "id",
            "group",
            "user",
            "is_anonymous",
            "sobriety_start_date",
            "sponsor_id",
            "join_date",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "join_date", "created_at", "updated_at"]


class RecoveryMilestoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecoveryMilestone
        fields = [
            "id",
            "user",
            "days_sober",
            "milestone_type",
            "addiction_type",
            "celebrated_at",
            "note",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "celebrated_at", "created_at", "updated_at"]


# ---------------------------------------------------------------------------
# Pregnancy & Baby
# ---------------------------------------------------------------------------

class PregnancyTrackerSerializer(serializers.ModelSerializer):
    class Meta:
        model = PregnancyTracker
        fields = [
            "id",
            "patient",
            "due_date",
            "current_week",
            "last_appointment",
            "next_appointment",
            "notes",
            "symptoms",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class BabyMilestoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = BabyMilestone
        fields = [
            "id",
            "patient",
            "baby_name",
            "birth_date",
            "milestone_type",
            "milestone_date",
            "notes",
            "photo_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


# ---------------------------------------------------------------------------
# Blood Type Registry
# ---------------------------------------------------------------------------

class BloodTypeRegistrySerializer(serializers.ModelSerializer):
    class Meta:
        model = BloodTypeRegistry
        fields = [
            "id",
            "user",
            "blood_type",
            "is_available_to_donate",
            "last_donation_date",
            "location_city",
            "location_country",
            "is_public",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


# ---------------------------------------------------------------------------
# E-Medication
# ---------------------------------------------------------------------------

class EMedicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = EMedication
        fields = [
            "id",
            "patient",
            "name",
            "dosage",
            "frequency",
            "prescribed_by",
            "start_date",
            "refill_due",
            "is_active",
            "notes",
            "interaction_warnings",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


# ---------------------------------------------------------------------------
# Health Goals
# ---------------------------------------------------------------------------

class HealthGoalSerializer(serializers.ModelSerializer):
    class Meta:
        model = HealthGoal
        fields = [
            "id",
            "user",
            "title",
            "category",
            "target_value",
            "current_value",
            "unit",
            "deadline",
            "is_achieved",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class HealthGoalProgressSerializer(serializers.Serializer):
    current_value = serializers.FloatField()
    notes = serializers.CharField(required=False, allow_blank=True)


# ---------------------------------------------------------------------------
# Emergency Alert
# ---------------------------------------------------------------------------

class EmergencyAlertSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmergencyAlert
        fields = [
            "id",
            "user",
            "alert_type",
            "latitude",
            "longitude",
            "address",
            "message",
            "status",
            "notified_contacts",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class SOSAlertSerializer(serializers.Serializer):
    latitude = serializers.FloatField(required=False, allow_null=True)
    longitude = serializers.FloatField(required=False, allow_null=True)
    address = serializers.CharField(required=False, allow_blank=True)
    message = serializers.CharField(required=False, allow_blank=True)


# ---------------------------------------------------------------------------
# AI Symptom Checker
# ---------------------------------------------------------------------------

class SymptomsCheckSerializer(serializers.Serializer):
    symptoms = serializers.ListField(child=serializers.CharField(), min_length=1)
    age = serializers.IntegerField(required=False, allow_null=True)
    gender = serializers.ChoiceField(
        choices=["male", "female", "other"], required=False, allow_null=True
    )
