import uuid

from django.db import models
from django.db.models import JSONField

from apps.accounts.models import User
from apps.health_ops.models import HealthInstitution, ServiceWorkflowSession, TimeStampedUUIDModel


# ---------------------------------------------------------------------------
# Practitioner credentials (Section 2 of the KIS Health vision) — a
# practitioner is NOT verified merely by picking a profession. The actual
# verification state (draft/submitted/in_review/approved/rejected/...) is
# never stored redundantly here; it always lives on the generic
# apps.verification system (see apps.verification.services
# .current_practitioner_verification_status), exactly mirroring how
# HealthInstitution verification works. This model only holds the
# structured credential facts a verifier needs to review and that a
# patient-facing profile needs to display once approved.
# ---------------------------------------------------------------------------

class PractitionerProfessionType(models.TextChoices):
    DOCTOR = "doctor", "Doctor"
    NURSE = "nurse", "Nurse"
    DENTIST = "dentist", "Dentist"
    PHARMACIST = "pharmacist", "Pharmacist"
    PHYSIOTHERAPIST = "physiotherapist", "Physiotherapist"
    PSYCHOLOGIST = "psychologist", "Psychologist / Mental Health Professional"
    NUTRITIONIST = "nutritionist", "Nutritionist / Dietitian"
    LAB_PROFESSIONAL = "lab_professional", "Laboratory Professional"
    SPECIALIST = "specialist", "Specialist"
    OTHER = "other", "Other Licensed Healthcare Professional"


class PractitionerLicenseStatus(models.TextChoices):
    """The REAL-WORLD status of the license itself, as claimed/evidenced by
    the practitioner — independent of whether KIS has reviewed and approved
    it yet (that's apps.verification's case status). A license can be
    ACTIVE-per-the-issuing-board while KIS's own review is still PENDING."""
    ACTIVE = "active", "Active"
    EXPIRED = "expired", "Expired"
    SUSPENDED = "suspended", "Suspended"
    REVOKED = "revoked", "Revoked"


class HealthPractitioner(TimeStampedUUIDModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="health_practitioner_profile")
    # Optional — a practitioner may be independent (private practice) or
    # affiliated with one institution. Multi-institution affiliation is a
    # real-world case not modeled yet; this is additive-safe to extend to a
    # many-to-many later without breaking this field.
    institution = models.ForeignKey(
        HealthInstitution,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="practitioners",
    )
    legal_name = models.CharField(max_length=255)
    profession_type = models.CharField(max_length=32, choices=PractitionerProfessionType.choices, db_index=True)
    specialty = models.CharField(max_length=255, blank=True, default="")
    qualifications = models.JSONField(default=list, blank=True)  # ["MBBS", "FWACS", ...]
    registration_authority = models.CharField(max_length=255, blank=True, default="")
    license_number = models.CharField(max_length=120, blank=True, default="", db_index=True)
    jurisdiction = models.CharField(max_length=120, blank=True, default="")
    license_status = models.CharField(
        max_length=16, choices=PractitionerLicenseStatus.choices, default=PractitionerLicenseStatus.ACTIVE,
    )
    license_expires_at = models.DateField(null=True, blank=True, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "health_ops_practitioner"
        indexes = [
            models.Index(fields=["profession_type", "is_active"]),
            models.Index(fields=["institution", "is_active"]),
            models.Index(fields=["license_number"]),
        ]

    def __str__(self):
        return f"{self.legal_name} ({self.profession_type})"


# ---------------------------------------------------------------------------
# Patient clinical record — Condition / Allergy / Immunization
# (Section 7 of the KIS Health vision: longitudinal patient health record)
#
# Scope note: these are currently self-service only (the patient records
# their own data, same trust model as EMedication/HealthVitalReading
# already in this file). A practitioner-authored entry made during a real
# clinical encounter is a real, separate requirement (Section 5/6 of the
# vision — a proper "Encounter" concept with its own access-control rules),
# which does not exist yet in this codebase. Rather than bolt a
# clinician-write permission onto a patient model with no encounter to
# justify or audit it, `recorded_by`/`source` are modeled now so the schema
# is forward-compatible, but clinician-write is intentionally NOT wired up
# here — see KIS_HEALTH_CHECKLIST.md Section 4.
# ---------------------------------------------------------------------------

class ClinicalRecordSource(models.TextChoices):
    SELF_REPORTED = "self_reported", "Self-reported by patient"
    CLINICIAN_RECORDED = "clinician_recorded", "Recorded by a clinician"


class ConditionStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    RESOLVED = "resolved", "Resolved"
    REMISSION = "remission", "In remission"


class AllergySeverity(models.TextChoices):
    MILD = "mild", "Mild"
    MODERATE = "moderate", "Moderate"
    SEVERE = "severe", "Severe"
    LIFE_THREATENING = "life_threatening", "Life-threatening"


class Condition(TimeStampedUUIDModel):
    patient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_conditions")
    recorded_by = models.ForeignKey(
        HealthPractitioner, null=True, blank=True, on_delete=models.SET_NULL, related_name="recorded_conditions",
    )
    institution = models.ForeignKey(
        HealthInstitution, null=True, blank=True, on_delete=models.SET_NULL, related_name="patient_conditions",
    )
    name = models.CharField(max_length=255)
    icd_code = models.CharField(max_length=20, blank=True, default="")
    status = models.CharField(max_length=16, choices=ConditionStatus.choices, default=ConditionStatus.ACTIVE, db_index=True)
    source = models.CharField(max_length=24, choices=ClinicalRecordSource.choices, default=ClinicalRecordSource.SELF_REPORTED)
    onset_date = models.DateField(null=True, blank=True)
    resolved_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "health_ops_condition"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["patient", "is_active"])]

    def __str__(self):
        return f"{self.name} ({self.patient_id})"


class Allergy(TimeStampedUUIDModel):
    patient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_allergies")
    recorded_by = models.ForeignKey(
        HealthPractitioner, null=True, blank=True, on_delete=models.SET_NULL, related_name="recorded_allergies",
    )
    institution = models.ForeignKey(
        HealthInstitution, null=True, blank=True, on_delete=models.SET_NULL, related_name="patient_allergies",
    )
    allergen = models.CharField(max_length=255)
    reaction = models.TextField(blank=True, default="")
    severity = models.CharField(max_length=20, choices=AllergySeverity.choices, default=AllergySeverity.MILD)
    source = models.CharField(max_length=24, choices=ClinicalRecordSource.choices, default=ClinicalRecordSource.SELF_REPORTED)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "health_ops_allergy"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["patient", "is_active"])]

    def __str__(self):
        return f"{self.allergen} ({self.patient_id})"


class Immunization(TimeStampedUUIDModel):
    patient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_immunizations")
    administered_by = models.ForeignKey(
        HealthPractitioner, null=True, blank=True, on_delete=models.SET_NULL, related_name="administered_immunizations",
    )
    institution = models.ForeignKey(
        HealthInstitution, null=True, blank=True, on_delete=models.SET_NULL, related_name="patient_immunizations",
    )
    vaccine_name = models.CharField(max_length=255)
    dose_number = models.PositiveIntegerField(default=1)
    administered_date = models.DateField(null=True, blank=True)
    lot_number = models.CharField(max_length=100, blank=True, default="")
    next_dose_due = models.DateField(null=True, blank=True, db_index=True)
    source = models.CharField(max_length=24, choices=ClinicalRecordSource.choices, default=ClinicalRecordSource.SELF_REPORTED)

    class Meta:
        db_table = "health_ops_immunization"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["patient"]), models.Index(fields=["next_dose_due"])]

    def __str__(self):
        return f"{self.vaccine_name} dose {self.dose_number} ({self.patient_id})"


# ---------------------------------------------------------------------------
# Clinical Encounter (Section 5/6 of the KIS Health vision)
#
# This is the foundational clinical-relationship record that
# clinician-authored patient-record writes, referrals, and lab orders all
# anchor to. It is deliberately distinct from `ClinicalEngineSession`
# (apps/health_ops/models.py) — that is a generic step-tracker for a
# *booking workflow* (one step of a ServiceWorkflowSession, JSON payload,
# no clinical semantics of its own). An Encounter is the actual clinical
# event: a specific practitioner seeing a specific patient, with real
# clinical content (reason/notes/assessment/plan) and its own
# authorization boundary. `workflow_session` is an optional backlink to the
# booking context it grew out of, not a replacement for either model.
# ---------------------------------------------------------------------------

class EncounterType(models.TextChoices):
    IN_PERSON = "in_person", "In-person visit"
    VIDEO = "video", "Video consultation"
    MESSAGING = "messaging", "Secure messaging"
    EMERGENCY = "emergency", "Emergency"
    HOME_VISIT = "home_visit", "Home visit"
    OTHER = "other", "Other"


class EncounterStatus(models.TextChoices):
    SCHEDULED = "scheduled", "Scheduled"
    CHECKED_IN = "checked_in", "Checked in"
    IN_PROGRESS = "in_progress", "In progress"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"
    NO_SHOW = "no_show", "No-show"
    CLOSED = "closed", "Closed"


class Encounter(TimeStampedUUIDModel):
    patient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_encounters")
    practitioner = models.ForeignKey(
        HealthPractitioner, on_delete=models.PROTECT, related_name="encounters",
    )
    institution = models.ForeignKey(
        HealthInstitution, on_delete=models.PROTECT, related_name="encounters",
    )
    workflow_session = models.ForeignKey(
        ServiceWorkflowSession, null=True, blank=True, on_delete=models.SET_NULL, related_name="encounters",
    )
    encounter_type = models.CharField(max_length=20, choices=EncounterType.choices, default=EncounterType.IN_PERSON)
    status = models.CharField(max_length=16, choices=EncounterStatus.choices, default=EncounterStatus.SCHEDULED, db_index=True)
    reason = models.TextField(blank=True, default="")
    notes = models.TextField(blank=True, default="")
    assessment = models.TextField(blank=True, default="")
    treatment_plan = models.TextField(blank=True, default="")
    scheduled_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "health_ops_encounter"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["patient", "status"]),
            models.Index(fields=["practitioner", "status"]),
            models.Index(fields=["institution", "status"]),
        ]

    def __str__(self):
        return f"Encounter {self.id} ({self.patient_id} / {self.practitioner_id})"


# ---------------------------------------------------------------------------
# Referral (Section 13 of the KIS Health vision)
# ---------------------------------------------------------------------------

class ReferralStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACCEPTED = "accepted", "Accepted"
    DECLINED = "declined", "Declined"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class ReferralPriority(models.TextChoices):
    ROUTINE = "routine", "Routine"
    URGENT = "urgent", "Urgent"
    EMERGENCY = "emergency", "Emergency"


class ClinicalReferral(TimeStampedUUIDModel):
    encounter = models.ForeignKey(Encounter, on_delete=models.PROTECT, related_name="clinical_referrals")
    patient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_clinical_referrals")
    referring_practitioner = models.ForeignKey(
        HealthPractitioner, on_delete=models.PROTECT, related_name="clinical_referrals_made",
    )
    referring_institution = models.ForeignKey(
        HealthInstitution, null=True, blank=True, on_delete=models.SET_NULL, related_name="clinical_referrals_made",
    )
    receiving_practitioner = models.ForeignKey(
        HealthPractitioner, null=True, blank=True, on_delete=models.SET_NULL, related_name="clinical_referrals_received",
    )
    receiving_institution = models.ForeignKey(
        HealthInstitution, null=True, blank=True, on_delete=models.SET_NULL, related_name="clinical_referrals_received",
    )
    reason = models.TextField()
    priority = models.CharField(max_length=16, choices=ReferralPriority.choices, default=ReferralPriority.ROUTINE)
    status = models.CharField(max_length=16, choices=ReferralStatus.choices, default=ReferralStatus.PENDING, db_index=True)
    notes = models.TextField(blank=True, default="")
    responded_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "health_ops_clinical_referral"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["patient", "status"]),
            models.Index(fields=["receiving_practitioner", "status"]),
            models.Index(fields=["receiving_institution", "status"]),
        ]

    def __str__(self):
        return f"ClinicalReferral {self.id} ({self.status})"


# ---------------------------------------------------------------------------
# Laboratory (Section 9 of the KIS Health vision) — replaces the generic
# ClinicalEngineCode.LAB_ORDER JSON-payload step with a real, queryable,
# access-controlled clinical record. Deliberately minimal: catalog + order
# + specimen + result covers the required lifecycle without inventing
# modules (billing integration for lab fees reuses the existing
# PaymentBillingSession machinery rather than a parallel one).
# ---------------------------------------------------------------------------

class LabTestCatalogItem(TimeStampedUUIDModel):
    institution = models.ForeignKey(
        HealthInstitution, null=True, blank=True, on_delete=models.SET_NULL, related_name="lab_catalog_items",
    )
    code = models.CharField(max_length=40, db_index=True)
    name = models.CharField(max_length=255)
    specimen_type = models.CharField(max_length=100, blank=True, default="")
    reference_range = models.CharField(max_length=255, blank=True, default="")
    unit = models.CharField(max_length=40, blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "health_ops_lab_catalog_item"
        constraints = [
            models.UniqueConstraint(fields=["institution", "code"], name="health_ops_lab_catalog_code_unique"),
        ]

    def __str__(self):
        return f"{self.code} — {self.name}"


class LabOrderStatus(models.TextChoices):
    ORDERED = "ordered", "Ordered"
    SPECIMEN_COLLECTED = "specimen_collected", "Specimen collected"
    PROCESSING = "processing", "Processing"
    RESULT_PENDING_VERIFICATION = "result_pending_verification", "Result pending verification"
    RESULTED = "resulted", "Resulted"
    CANCELLED = "cancelled", "Cancelled"


class LabOrder(TimeStampedUUIDModel):
    encounter = models.ForeignKey(Encounter, on_delete=models.PROTECT, related_name="lab_orders")
    patient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_lab_orders")
    ordering_practitioner = models.ForeignKey(
        HealthPractitioner, on_delete=models.PROTECT, related_name="lab_orders_placed",
    )
    institution = models.ForeignKey(
        HealthInstitution, null=True, blank=True, on_delete=models.SET_NULL, related_name="lab_orders",
    )
    test = models.ForeignKey(LabTestCatalogItem, on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(max_length=32, choices=LabOrderStatus.choices, default=LabOrderStatus.ORDERED, db_index=True)
    clinical_notes = models.TextField(blank=True, default="")
    ordered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "health_ops_lab_order"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["patient", "status"]),
            models.Index(fields=["ordering_practitioner", "status"]),
        ]

    def __str__(self):
        return f"LabOrder {self.id} ({self.status})"


class Specimen(TimeStampedUUIDModel):
    lab_order = models.OneToOneField(LabOrder, on_delete=models.CASCADE, related_name="specimen")
    specimen_type = models.CharField(max_length=100, blank=True, default="")
    collected_at = models.DateTimeField(null=True, blank=True)
    collected_by = models.ForeignKey(
        HealthPractitioner, null=True, blank=True, on_delete=models.SET_NULL, related_name="specimens_collected",
    )
    barcode = models.CharField(max_length=100, blank=True, default="")
    rejected = models.BooleanField(default=False)
    rejection_reason = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "health_ops_specimen"

    def __str__(self):
        return f"Specimen for {self.lab_order_id}"


class LabResultFlag(models.TextChoices):
    NORMAL = "normal", "Normal"
    ABNORMAL = "abnormal", "Abnormal"
    CRITICAL = "critical", "Critical"


class LabResult(TimeStampedUUIDModel):
    lab_order = models.OneToOneField(LabOrder, on_delete=models.CASCADE, related_name="result")
    value = models.CharField(max_length=255, blank=True, default="")
    unit = models.CharField(max_length=40, blank=True, default="")
    reference_range = models.CharField(max_length=255, blank=True, default="")
    flag = models.CharField(max_length=16, choices=LabResultFlag.choices, default=LabResultFlag.NORMAL)
    entered_by = models.ForeignKey(
        HealthPractitioner, null=True, blank=True, on_delete=models.SET_NULL, related_name="lab_results_entered",
    )
    verified_by = models.ForeignKey(
        HealthPractitioner, null=True, blank=True, on_delete=models.SET_NULL, related_name="lab_results_verified",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "health_ops_lab_result"

    def __str__(self):
        return f"Result for {self.lab_order_id}"


# ---------------------------------------------------------------------------
# Telemedicine
# ---------------------------------------------------------------------------

class TelemedicineConsult(TimeStampedUUIDModel):
    class ConsultType(models.TextChoices):
        INSTANT = "instant", "Instant"
        SCHEDULED = "scheduled", "Scheduled"

    class ConsultStatus(models.TextChoices):
        REQUESTED = "requested", "Requested"
        CONFIRMED = "confirmed", "Confirmed"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    patient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="tele_patient")
    doctor = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="tele_doctor"
    )
    specialty = models.CharField(max_length=255, blank=True, default="")
    type = models.CharField(
        max_length=16, choices=ConsultType.choices, default=ConsultType.SCHEDULED, db_index=True
    )
    status = models.CharField(
        max_length=16, choices=ConsultStatus.choices, default=ConsultStatus.REQUESTED, db_index=True
    )
    scheduled_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    prescription_notes = models.TextField(blank=True, default="")
    recording_url = models.URLField(blank=True, default="")
    call_url = models.URLField(blank=True, default="")
    metadata = JSONField(default=dict)

    class Meta:
        db_table = "health_ext_telemedicine_consult"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["patient", "status"]),
            models.Index(fields=["doctor", "status"]),
        ]

    def __str__(self):
        return f"{self.patient_id} -> {self.doctor_id} [{self.status}]"


class ConsultReview(TimeStampedUUIDModel):
    consult = models.OneToOneField(
        TelemedicineConsult, on_delete=models.CASCADE, related_name="review"
    )
    reviewer = models.ForeignKey(User, on_delete=models.CASCADE, related_name="consult_reviews")
    rating = models.IntegerField()  # 1-5
    comment = models.TextField(blank=True, default="")

    class Meta:
        db_table = "health_ext_consult_review"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Review for {self.consult_id} — {self.rating}/5"


# ---------------------------------------------------------------------------
# Mental Health
# ---------------------------------------------------------------------------

class MentalHealthSession(TimeStampedUUIDModel):
    class SessionType(models.TextChoices):
        INDIVIDUAL = "individual", "Individual"
        GROUP = "group", "Group"
        CRISIS = "crisis", "Crisis"

    class Modality(models.TextChoices):
        VIDEO = "video", "Video"
        AUDIO = "audio", "Audio"
        TEXT = "text", "Text"

    class SessionStatus(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    patient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="mh_patient")
    therapist = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="mh_therapist"
    )
    type = models.CharField(
        max_length=16, choices=SessionType.choices, default=SessionType.INDIVIDUAL, db_index=True
    )
    modality = models.CharField(
        max_length=8, choices=Modality.choices, default=Modality.VIDEO
    )
    status = models.CharField(
        max_length=16, choices=SessionStatus.choices, default=SessionStatus.SCHEDULED, db_index=True
    )
    scheduled_at = models.DateTimeField(null=True, blank=True)
    session_notes = models.TextField(blank=True, default="")
    is_faith_based = models.BooleanField(default=False)

    class Meta:
        db_table = "health_ext_mental_health_session"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["patient", "status"]),
            models.Index(fields=["therapist", "status"]),
        ]

    def __str__(self):
        return f"MH {self.type} — {self.status}"


class MoodEntry(TimeStampedUUIDModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="mood_entries")
    mood_score = models.IntegerField()  # 1-10
    emotion_tags = JSONField(default=list)
    journal_text = models.TextField(blank=True, default="")
    entry_date = models.DateField(db_index=True)

    class Meta:
        db_table = "health_ext_mood_entry"
        ordering = ["-entry_date"]
        unique_together = [["user", "entry_date"]]
        indexes = [
            models.Index(fields=["user", "entry_date"]),
        ]

    def __str__(self):
        return f"{self.user_id} mood {self.mood_score} on {self.entry_date}"


class MentalHealthJournal(TimeStampedUUIDModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="mh_journals")
    title = models.CharField(max_length=255, blank=True, default="")
    content = models.TextField()
    mood_score = models.IntegerField(null=True, blank=True)
    is_private = models.BooleanField(default=True)
    entry_date = models.DateField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "health_ext_mh_journal"
        ordering = ["-entry_date", "-created_at"]
        indexes = [
            models.Index(fields=["user", "entry_date"]),
        ]

    def __str__(self):
        return f"{self.user_id} journal — {self.entry_date}"


# ---------------------------------------------------------------------------
# Addiction Recovery
# ---------------------------------------------------------------------------

class AddictionRecoveryGroup(TimeStampedUUIDModel):
    class AddictionType(models.TextChoices):
        ALCOHOL = "alcohol", "Alcohol"
        DRUGS = "drugs", "Drugs"
        GAMBLING = "gambling", "Gambling"
        PORNOGRAPHY = "pornography", "Pornography"
        OTHER = "other", "Other"

    name = models.CharField(max_length=255)
    addiction_type = models.CharField(
        max_length=16, choices=AddictionType.choices, default=AddictionType.OTHER, db_index=True
    )
    facilitator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="facilitated_recovery_groups"
    )
    description = models.TextField(blank=True, default="")
    is_anonymous = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True, db_index=True)
    meeting_schedule = JSONField(default=dict)

    class Meta:
        db_table = "health_ext_recovery_group"
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class RecoveryMembership(TimeStampedUUIDModel):
    group = models.ForeignKey(
        AddictionRecoveryGroup, on_delete=models.CASCADE, related_name="memberships"
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="recovery_memberships")
    is_anonymous = models.BooleanField(default=False)
    sobriety_start_date = models.DateField(null=True, blank=True)
    sponsor_id = models.UUIDField(null=True, blank=True)
    join_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "health_ext_recovery_membership"
        unique_together = [["group", "user"]]
        indexes = [
            models.Index(fields=["group", "user"]),
        ]

    def __str__(self):
        return f"{self.user_id} in {self.group_id}"


class RecoveryMilestone(TimeStampedUUIDModel):
    class MilestoneType(models.TextChoices):
        DAY_1 = "day_1", "Day 1"
        DAY_7 = "day_7", "Day 7"
        DAY_30 = "day_30", "Day 30"
        DAY_90 = "day_90", "Day 90"
        DAY_180 = "day_180", "Day 180"
        YEAR_1 = "year_1", "Year 1"
        YEAR_2 = "year_2", "Year 2"
        YEAR_5 = "year_5", "Year 5"
        CUSTOM = "custom", "Custom"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="recovery_milestones")
    days_sober = models.IntegerField()
    milestone_type = models.CharField(
        max_length=16, choices=MilestoneType.choices, db_index=True
    )
    addiction_type = models.CharField(max_length=64, blank=True, default="")
    celebrated_at = models.DateTimeField(auto_now_add=True)
    note = models.TextField(blank=True, default="")

    class Meta:
        db_table = "health_ext_recovery_milestone"
        ordering = ["-celebrated_at"]
        indexes = [
            models.Index(fields=["user", "milestone_type"]),
        ]

    def __str__(self):
        return f"{self.user_id} — {self.milestone_type} ({self.days_sober}d)"


# ---------------------------------------------------------------------------
# Pregnancy & Baby
# ---------------------------------------------------------------------------

class PregnancyTracker(TimeStampedUUIDModel):
    patient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="pregnancy_trackers")
    due_date = models.DateField()
    current_week = models.IntegerField(null=True, blank=True)
    last_appointment = models.DateField(null=True, blank=True)
    next_appointment = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    symptoms = JSONField(default=list)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "health_ext_pregnancy_tracker"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["patient", "is_active"]),
        ]

    def __str__(self):
        return f"{self.patient_id} due {self.due_date}"


class BabyMilestone(TimeStampedUUIDModel):
    class MilestoneType(models.TextChoices):
        BIRTH_WEIGHT = "birth_weight", "Birth Weight"
        FIRST_SMILE = "first_smile", "First Smile"
        FIRST_STEPS = "first_steps", "First Steps"
        FIRST_WORDS = "first_words", "First Words"
        OTHER = "other", "Other"

    patient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="baby_milestones")
    baby_name = models.CharField(max_length=255, blank=True, default="")
    birth_date = models.DateField(null=True, blank=True)
    milestone_type = models.CharField(
        max_length=16, choices=MilestoneType.choices, db_index=True
    )
    milestone_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    photo_url = models.URLField(blank=True, default="")

    class Meta:
        db_table = "health_ext_baby_milestone"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["patient", "milestone_type"]),
        ]

    def __str__(self):
        return f"{self.baby_name or self.patient_id} — {self.milestone_type}"


# ---------------------------------------------------------------------------
# Blood Type Registry
# ---------------------------------------------------------------------------

class BloodTypeRegistry(TimeStampedUUIDModel):
    class BloodType(models.TextChoices):
        A_POS = "A_POS", "A+"
        A_NEG = "A_NEG", "A-"
        B_POS = "B_POS", "B+"
        B_NEG = "B_NEG", "B-"
        AB_POS = "AB_POS", "AB+"
        AB_NEG = "AB_NEG", "AB-"
        O_POS = "O_POS", "O+"
        O_NEG = "O_NEG", "O-"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="blood_type_registry")
    blood_type = models.CharField(max_length=8, choices=BloodType.choices, db_index=True)
    is_available_to_donate = models.BooleanField(default=False, db_index=True)
    last_donation_date = models.DateField(null=True, blank=True)
    location_city = models.CharField(max_length=120, blank=True, default="")
    location_country = models.CharField(max_length=120, blank=True, default="", db_index=True)
    is_public = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "health_ext_blood_type_registry"
        indexes = [
            models.Index(fields=["blood_type", "is_available_to_donate"]),
            models.Index(fields=["location_country", "blood_type"]),
        ]

    def __str__(self):
        return f"{self.user_id} — {self.blood_type}"


# ---------------------------------------------------------------------------
# E-Medication
# ---------------------------------------------------------------------------

class EMedication(TimeStampedUUIDModel):
    patient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="emedications")
    name = models.CharField(max_length=255)
    dosage = models.CharField(max_length=100)
    frequency = models.CharField(max_length=100)
    # Legacy free-text field — kept for the patient's own self-tracked
    # "medications I'm taking" list (e.g. an over-the-counter supplement, or
    # something prescribed outside KIS entirely). NEVER trusted as proof of
    # a real prescription — see prescribing_practitioner below, which is the
    # only field the pharmacy workflow (verify_prescription step) accepts
    # as evidence a prescription is real.
    prescribed_by = models.CharField(max_length=255, blank=True, default="")
    prescribing_practitioner = models.ForeignKey(
        "health_ops.HealthPractitioner", null=True, blank=True, on_delete=models.SET_NULL, related_name="prescriptions",
    )
    encounter = models.ForeignKey(
        "health_ops.Encounter", null=True, blank=True, on_delete=models.SET_NULL, related_name="prescriptions",
    )
    start_date = models.DateField(null=True, blank=True)
    refill_due = models.DateField(null=True, blank=True, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    is_revoked = models.BooleanField(default=False, db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    interaction_warnings = JSONField(default=list)

    class Meta:
        db_table = "health_ext_emedication"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["patient", "is_active"]),
            models.Index(fields=["refill_due"]),
        ]

    def __str__(self):
        return f"{self.name} for {self.patient_id}"

    @property
    def is_verified_prescription(self) -> bool:
        """True only for a prescription actually issued by a practitioner
        through the Encounter-anchored clinician path — never true for a
        patient's own free-text self-tracked entry, regardless of what they
        type into prescribed_by."""
        return bool(self.prescribing_practitioner_id) and not self.is_revoked


# ---------------------------------------------------------------------------
# Health Goals
# ---------------------------------------------------------------------------

class HealthGoal(TimeStampedUUIDModel):
    class Category(models.TextChoices):
        FITNESS = "fitness", "Fitness"
        NUTRITION = "nutrition", "Nutrition"
        SLEEP = "sleep", "Sleep"
        WEIGHT = "weight", "Weight"
        MENTAL = "mental", "Mental"
        CHRONIC_DISEASE = "chronic_disease", "Chronic Disease"
        OTHER = "other", "Other"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_goals")
    title = models.CharField(max_length=255)
    category = models.CharField(
        max_length=16, choices=Category.choices, default=Category.FITNESS, db_index=True
    )
    target_value = models.FloatField(null=True, blank=True)
    current_value = models.FloatField(null=True, blank=True)
    unit = models.CharField(max_length=64, blank=True, default="")
    deadline = models.DateField(null=True, blank=True)
    is_achieved = models.BooleanField(default=False, db_index=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "health_ext_health_goal"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "category"]),
            models.Index(fields=["user", "is_achieved"]),
        ]

    def __str__(self):
        return f"{self.title} ({self.user_id})"


# ---------------------------------------------------------------------------
# Emergency Alert
# ---------------------------------------------------------------------------

class EmergencyAlert(TimeStampedUUIDModel):
    class AlertType(models.TextChoices):
        SOS = "sos", "SOS"
        MEDICAL = "medical", "Medical"
        FIRE = "fire", "Fire"
        SECURITY = "security", "Security"

    class AlertStatus(models.TextChoices):
        ACTIVE = "active", "Active"
        RESOLVED = "resolved", "Resolved"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="emergency_alerts")
    alert_type = models.CharField(
        max_length=12, choices=AlertType.choices, default=AlertType.SOS, db_index=True
    )
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    address = models.TextField(blank=True, default="")
    message = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=12, choices=AlertStatus.choices, default=AlertStatus.ACTIVE, db_index=True
    )
    notified_contacts = JSONField(default=list)

    class Meta:
        db_table = "health_ext_emergency_alert"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["alert_type", "status"]),
        ]

    def __str__(self):
        return f"{self.alert_type} alert by {self.user_id} [{self.status}]"
