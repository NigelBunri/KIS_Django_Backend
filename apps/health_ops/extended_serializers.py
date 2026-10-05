from rest_framework import serializers

from .extended_models import (
    AddictionRecoveryGroup,
    Allergy,
    BabyMilestone,
    BloodTypeRegistry,
    Condition,
    ConsultReview,
    EMedication,
    EmergencyAlert,
    Encounter,
    HealthGoal,
    HealthPractitioner,
    Immunization,
    LabOrder,
    LabResult,
    LabTestCatalogItem,
    MentalHealthJournal,
    MentalHealthSession,
    MoodEntry,
    PregnancyTracker,
    RecoveryMembership,
    RecoveryMilestone,
    ClinicalReferral,
    Specimen,
    TelemedicineConsult,
)


# ---------------------------------------------------------------------------
# Practitioner credentials
# ---------------------------------------------------------------------------

class HealthPractitionerSerializer(serializers.ModelSerializer):
    verification_status = serializers.SerializerMethodField()

    class Meta:
        model = HealthPractitioner
        fields = [
            "id",
            "user",
            "institution",
            "legal_name",
            "profession_type",
            "specialty",
            "qualifications",
            "registration_authority",
            "license_number",
            "jurisdiction",
            "license_status",
            "license_expires_at",
            "is_active",
            "verification_status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "user", "is_active", "verification_status", "created_at", "updated_at"]

    def get_verification_status(self, obj):
        from apps.verification.services import current_practitioner_verification_status

        return current_practitioner_verification_status(obj)


class PractitionerDirectoryEntrySerializer(serializers.ModelSerializer):
    """Patient-facing discovery card — deliberately a narrower field set
    than HealthPractitionerSerializer: never exposes verification evidence,
    license_number, or registration_authority to a browsing patient, only
    what helps them pick a practitioner plus a friendly verification badge.
    Section 2 of the vision: 'do not expose sensitive verification
    documents unnecessarily to patients.'"""

    verification_status = serializers.SerializerMethodField()
    institution_name = serializers.CharField(source="institution.name", default="", read_only=True)

    class Meta:
        model = HealthPractitioner
        fields = [
            "id",
            # The practitioner's own User id — not sensitive (it's just the
            # account identifier behind a public directory listing, the
            # same thing a profile view would expose), and required by the
            # RN app's booking flow: TelemedicineConsult.doctor is a User
            # FK (pre-existing schema, unrelated to this session's
            # HealthPractitioner work), so the client needs this exact
            # value — not the practitioner row's own id — to create a
            # consult against the chosen practitioner.
            "user",
            "legal_name",
            "profession_type",
            "specialty",
            "institution_name",
            "verification_status",
        ]

    def get_verification_status(self, obj):
        from apps.verification.services import current_practitioner_verification_status

        summary = current_practitioner_verification_status(obj)
        # Collapse to the one thing a patient actually needs: is this badge
        # live right now? Never the raw case/evidence detail.
        return {
            "is_verified": bool(summary.get("badges")),
            "badges": summary.get("badges", []),
        }


class StartPractitionerVerificationSerializer(serializers.Serializer):
    evidence_metadata = serializers.JSONField(required=False, default=dict)


class ReviewPractitionerCaseSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["approve", "reject", "needs_more_info"])
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    badge_codes = serializers.ListField(child=serializers.CharField(), required=False)


# ---------------------------------------------------------------------------
# Patient clinical record — Condition / Allergy / Immunization
# ---------------------------------------------------------------------------

class ConditionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Condition
        fields = [
            "id",
            "recorded_by",
            "institution",
            "name",
            "icd_code",
            "status",
            "source",
            "onset_date",
            "resolved_date",
            "notes",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "recorded_by", "source", "created_at", "updated_at"]


class AllergySerializer(serializers.ModelSerializer):
    class Meta:
        model = Allergy
        fields = [
            "id",
            "recorded_by",
            "institution",
            "allergen",
            "reaction",
            "severity",
            "source",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "recorded_by", "source", "created_at", "updated_at"]


class ImmunizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Immunization
        fields = [
            "id",
            "administered_by",
            "institution",
            "vaccine_name",
            "dose_number",
            "administered_date",
            "lot_number",
            "next_dose_due",
            "source",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "administered_by", "source", "created_at", "updated_at"]


class ClinicianConditionCreateSerializer(serializers.Serializer):
    name = serializers.CharField()
    icd_code = serializers.CharField(required=False, allow_blank=True, default="")
    status = serializers.ChoiceField(choices=Condition._meta.get_field("status").choices, required=False)
    onset_date = serializers.DateField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class ClinicianAllergyCreateSerializer(serializers.Serializer):
    allergen = serializers.CharField()
    reaction = serializers.CharField(required=False, allow_blank=True, default="")
    severity = serializers.ChoiceField(choices=Allergy._meta.get_field("severity").choices, required=False)


class ClinicianImmunizationCreateSerializer(serializers.Serializer):
    vaccine_name = serializers.CharField()
    dose_number = serializers.IntegerField(required=False, default=1)
    administered_date = serializers.DateField(required=False, allow_null=True)
    lot_number = serializers.CharField(required=False, allow_blank=True, default="")
    next_dose_due = serializers.DateField(required=False, allow_null=True)


# ---------------------------------------------------------------------------
# Clinical Encounter
# ---------------------------------------------------------------------------

class EncounterSerializer(serializers.ModelSerializer):
    class Meta:
        model = Encounter
        fields = [
            "id",
            "patient",
            "practitioner",
            "institution",
            "workflow_session",
            "encounter_type",
            "status",
            "reason",
            "notes",
            "assessment",
            "treatment_plan",
            "scheduled_at",
            "started_at",
            "ended_at",
            "closed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id", "patient", "practitioner", "status", "started_at", "ended_at",
            "closed_at", "created_at", "updated_at",
        ]


class EncounterCreateSerializer(serializers.Serializer):
    patient = serializers.UUIDField()
    institution = serializers.UUIDField()
    encounter_type = serializers.ChoiceField(choices=Encounter._meta.get_field("encounter_type").choices)
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    scheduled_at = serializers.DateTimeField(required=False, allow_null=True)


class EncounterClinicalContentSerializer(serializers.Serializer):
    notes = serializers.CharField(required=False, allow_blank=True)
    assessment = serializers.CharField(required=False, allow_blank=True)
    treatment_plan = serializers.CharField(required=False, allow_blank=True)


# ---------------------------------------------------------------------------
# Referral
# ---------------------------------------------------------------------------

class ClinicalReferralSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClinicalReferral
        fields = [
            "id",
            "encounter",
            "patient",
            "referring_practitioner",
            "referring_institution",
            "receiving_practitioner",
            "receiving_institution",
            "reason",
            "priority",
            "status",
            "notes",
            "responded_at",
            "completed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id", "patient", "referring_practitioner", "referring_institution", "status",
            "responded_at", "completed_at", "created_at", "updated_at",
        ]


class ClinicalReferralCreateSerializer(serializers.Serializer):
    encounter = serializers.UUIDField()
    receiving_practitioner = serializers.UUIDField(required=False, allow_null=True)
    receiving_institution = serializers.UUIDField(required=False, allow_null=True)
    reason = serializers.CharField()
    priority = serializers.ChoiceField(choices=ClinicalReferral._meta.get_field("priority").choices, required=False)
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class ClinicalReferralActionSerializer(serializers.Serializer):
    notes = serializers.CharField(required=False, allow_blank=True, default="")


# ---------------------------------------------------------------------------
# Laboratory
# ---------------------------------------------------------------------------

class LabTestCatalogItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = LabTestCatalogItem
        fields = [
            "id", "institution", "code", "name", "specimen_type",
            "reference_range", "unit", "is_active", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class SpecimenSerializer(serializers.ModelSerializer):
    class Meta:
        model = Specimen
        fields = [
            "id", "lab_order", "specimen_type", "collected_at", "collected_by",
            "barcode", "rejected", "rejection_reason", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "lab_order", "collected_at", "collected_by", "created_at", "updated_at"]


class LabResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = LabResult
        fields = [
            "id", "lab_order", "value", "unit", "reference_range", "flag",
            "entered_by", "verified_by", "verified_at", "delivered_at",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "lab_order", "entered_by", "verified_by", "verified_at",
            "delivered_at", "created_at", "updated_at",
        ]


class LabOrderSerializer(serializers.ModelSerializer):
    specimen = SpecimenSerializer(read_only=True)
    result = LabResultSerializer(read_only=True)
    test_name = serializers.CharField(source="test.name", read_only=True)

    class Meta:
        model = LabOrder
        fields = [
            "id", "encounter", "patient", "ordering_practitioner", "institution",
            "test", "test_name", "status", "clinical_notes", "ordered_at",
            "specimen", "result", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "patient", "ordering_practitioner", "institution", "status",
            "ordered_at", "created_at", "updated_at",
        ]


class LabOrderCreateSerializer(serializers.Serializer):
    encounter = serializers.UUIDField()
    test = serializers.UUIDField()
    clinical_notes = serializers.CharField(required=False, allow_blank=True, default="")


class SpecimenCollectSerializer(serializers.Serializer):
    specimen_type = serializers.CharField(required=False, allow_blank=True, default="")
    barcode = serializers.CharField(required=False, allow_blank=True, default="")


class LabResultEntrySerializer(serializers.Serializer):
    value = serializers.CharField()
    unit = serializers.CharField(required=False, allow_blank=True, default="")
    reference_range = serializers.CharField(required=False, allow_blank=True, default="")
    flag = serializers.ChoiceField(choices=LabResult._meta.get_field("flag").choices, required=False)


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
    is_verified_prescription = serializers.BooleanField(read_only=True)

    class Meta:
        model = EMedication
        fields = [
            "id",
            "patient",
            "name",
            "dosage",
            "frequency",
            "prescribed_by",
            "prescribing_practitioner",
            "encounter",
            "is_verified_prescription",
            "start_date",
            "refill_due",
            "is_active",
            "is_revoked",
            "revoked_at",
            "notes",
            "interaction_warnings",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id", "prescribing_practitioner", "encounter", "is_verified_prescription",
            "is_revoked", "revoked_at", "created_at", "updated_at",
        ]


class ClinicianPrescriptionCreateSerializer(serializers.Serializer):
    name = serializers.CharField()
    dosage = serializers.CharField()
    frequency = serializers.CharField()
    start_date = serializers.DateField(required=False, allow_null=True)
    refill_due = serializers.DateField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")


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
