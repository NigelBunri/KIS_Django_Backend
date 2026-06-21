import uuid

from django.db import models
from django.db.models import JSONField

from apps.accounts.models import User
from apps.health_ops.models import TimeStampedUUIDModel


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
    prescribed_by = models.CharField(max_length=255, blank=True, default="")
    start_date = models.DateField(null=True, blank=True)
    refill_due = models.DateField(null=True, blank=True, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
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
