import uuid
from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator

from apps.accounts.models import User


class TimeStampedUUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class InstitutionType(models.TextChoices):
    CLINIC = "clinic", "Clinic"
    HOSPITAL = "hospital", "Hospital"
    LAB = "lab", "Laboratory"
    DIAGNOSTICS = "diagnostics", "Diagnostics Center"
    PHARMACY = "pharmacy", "Pharmacy"
    WELLNESS_CENTER = "wellness_center", "Wellness Center"


class MembershipRole(models.TextChoices):
    OWNER = "owner", "Owner"
    ADMIN = "admin", "Admin"
    MANAGER = "manager", "Manager"
    STAFF = "staff", "Staff"
    MEMBER = "member", "Member"


class WorkflowStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    IN_PROGRESS = "in_progress", "In Progress"
    PAUSED = "paused", "Paused"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class VideoConsultationStatus(models.TextChoices):
    SCHEDULED = "scheduled", "Scheduled"
    WAITING_ROOM = "waiting_room", "Waiting Room"
    IN_SESSION = "in_session", "In Session"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class SecureMessagingStatus(models.TextChoices):
    WAITING = "waiting", "Waiting"
    ACTIVE = "active", "Active"
    COMPLETED = "completed", "Completed"
    CLOSED = "closed", "Closed"


class SecureMessageType(models.TextChoices):
    TEXT = "text", "Text"
    FILE = "file", "File"
    VOICE = "voice", "Voice"
    SYSTEM = "system", "System"


class ClinicalEngineCode(models.TextChoices):
    EHR_RECORDS = "ehr_records", "EHR / Health Records"
    LAB_ORDER = "lab_order", "Lab Order"
    IMAGING_ORDER = "imaging_order", "Imaging Order"


class ClinicalEngineSessionStatus(models.TextChoices):
    WAITING = "waiting", "Waiting"
    IN_PROGRESS = "in_progress", "In Progress"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"
    PAUSED = "paused", "Paused"


class AdmissionBedStatus(models.TextChoices):
    WAITING = "waiting", "Waiting"
    INTAKE = "intake", "Intake"
    BED_ASSIGNED = "bed_assigned", "Bed Assigned"
    ADMITTED = "admitted", "Admitted"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class EmergencyDispatchStatus(models.TextChoices):
    WAITING = "waiting", "Waiting"
    TRIAGING = "triaging", "Triaging"
    DISPATCHED = "dispatched", "Dispatched"
    IN_TRANSIT = "in_transit", "In Transit"
    ARRIVED = "arrived", "Arrived"
    RESOLVED = "resolved", "Resolved"
    CANCELLED = "cancelled", "Cancelled"


class PharmacyFulfillmentStatus(models.TextChoices):
    WAITING = "waiting", "Waiting"
    VERIFYING = "verifying", "Verifying"
    INVENTORY_CONFIRMED = "inventory_confirmed", "Inventory Confirmed"
    FULFILLMENT_IN_PROGRESS = "fulfillment_in_progress", "Fulfillment In Progress"
    READY_FOR_COLLECTION = "ready_for_collection", "Ready For Collection"
    DELIVERED = "delivered", "Delivered"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class HomeLogisticsStatus(models.TextChoices):
    WAITING = "waiting", "Waiting"
    SCHEDULING = "scheduling", "Scheduling"
    ROUTE_ASSIGNED = "route_assigned", "Route Assigned"
    IN_TRANSIT = "in_transit", "In Transit"
    ARRIVED = "arrived", "Arrived"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class WellnessProgramStatus(models.TextChoices):
    WAITING = "waiting", "Waiting"
    ENROLLED = "enrolled", "Enrolled"
    IN_PROGRESS = "in_progress", "In Progress"
    PAUSED = "paused", "Paused"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class NotificationReminderStatus(models.TextChoices):
    WAITING = "waiting", "Waiting"
    CONFIGURING = "configuring", "Configuring"
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    COMPLETED = "completed", "Completed"
    DISABLED = "disabled", "Disabled"
    CANCELLED = "cancelled", "Cancelled"


class PaymentBillingStatus(models.TextChoices):
    WAITING = "waiting", "Waiting"
    QUOTE_READY = "quote_ready", "Quote Ready"
    PAYMENT_PENDING = "payment_pending", "Payment Pending"
    PAID = "paid", "Paid"
    INVOICED = "invoiced", "Invoiced"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class CarePlanStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class VitalReadingType(models.TextChoices):
    BLOOD_PRESSURE = "blood_pressure", "Blood Pressure"
    HEART_RATE = "heart_rate", "Heart Rate"
    TEMPERATURE = "temperature", "Temperature"
    OXYGEN_SATURATION = "oxygen_saturation", "Oxygen Saturation"
    BLOOD_GLUCOSE = "blood_glucose", "Blood Glucose"
    WEIGHT = "weight", "Weight"
    CUSTOM = "custom", "Custom"


class EngineCompletionMode(models.TextChoices):
    STEP_PROGRESS = "step_progress", "Step Progress"
    VIDEO_ITEMS = "video_items", "Video Items"


class ContentBlockType(models.TextChoices):
    VIDEO = "video", "Video"
    AUDIO = "audio", "Audio"
    TEXT = "text", "Text"
    FILE = "file", "File"
    INTERACTIVE = "interactive", "Interactive"


class HealthInstitution(TimeStampedUUIDModel):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_ops_owned_institutions")
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=280, unique=True)
    institution_type = models.CharField(max_length=40, choices=InstitutionType.choices, default=InstitutionType.CLINIC, db_index=True)
    timezone = models.CharField(max_length=64, default="UTC")
    settings = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "health_ops_institution"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner", "institution_type"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return self.name


class HealthInstitutionMembership(TimeStampedUUIDModel):
    institution = models.ForeignKey(HealthInstitution, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_ops_memberships")
    role = models.CharField(max_length=32, choices=MembershipRole.choices, default=MembershipRole.MEMBER, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    invited_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="health_ops_invitations")

    class Meta:
        db_table = "health_ops_membership"
        constraints = [
            models.UniqueConstraint(fields=["institution", "user"], name="health_ops_membership_unique"),
        ]
        indexes = [
            models.Index(fields=["role", "is_active"]),
        ]


class HealthService(TimeStampedUUIDModel):
    institution = models.ForeignKey(HealthInstitution, on_delete=models.CASCADE, related_name="services")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)
    requires_assessment = models.BooleanField(default=False)
    assessment_schema = models.JSONField(default=dict, blank=True)
    base_cost_micro = models.BigIntegerField(default=0, validators=[MinValueValidator(0)])

    class Meta:
        db_table = "health_ops_service"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["institution", "name"], name="health_ops_service_name_per_institution"),
        ]

    def __str__(self):
        return self.name


class EngineRegistry(TimeStampedUUIDModel):
    code = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=180)
    description = models.TextField(blank=True, default="")
    category = models.CharField(max_length=80, blank=True, default="")
    is_fixed = models.BooleanField(default=True, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    schema_version = models.PositiveIntegerField(default=1)
    default_step_count = models.PositiveIntegerField(default=1)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "health_ops_engine_registry"
        ordering = ["name"]

    def __str__(self):
        return self.name


class ServiceEngineMap(TimeStampedUUIDModel):
    service = models.ForeignKey(HealthService, on_delete=models.CASCADE, related_name="engine_mappings")
    engine = models.ForeignKey(EngineRegistry, on_delete=models.CASCADE, related_name="service_mappings")
    execution_order = models.PositiveIntegerField(default=1)
    config = models.JSONField(default=dict, blank=True)
    cost_micro = models.BigIntegerField(default=0, validators=[MinValueValidator(0)])
    is_required = models.BooleanField(default=True)
    access_window_days = models.PositiveIntegerField(default=0)
    completion_mode = models.CharField(
        max_length=32,
        choices=EngineCompletionMode.choices,
        default=EngineCompletionMode.STEP_PROGRESS,
        db_index=True,
    )

    class Meta:
        db_table = "health_ops_service_engine_map"
        ordering = ["execution_order"]
        constraints = [
            models.UniqueConstraint(fields=["service", "engine"], name="health_ops_service_engine_unique"),
            models.UniqueConstraint(fields=["service", "execution_order"], name="health_ops_service_engine_order_unique"),
        ]


class EngineStepDefinition(TimeStampedUUIDModel):
    engine = models.ForeignKey(EngineRegistry, on_delete=models.CASCADE, related_name="step_definitions")
    step_key = models.SlugField(max_length=120)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    step_order = models.PositiveIntegerField(default=1)
    validation_schema = models.JSONField(default=dict, blank=True)
    completion_rule = models.JSONField(default=dict, blank=True)
    is_required = models.BooleanField(default=True)

    class Meta:
        db_table = "health_ops_engine_step_definition"
        ordering = ["step_order"]
        constraints = [
            models.UniqueConstraint(fields=["engine", "step_key"], name="health_ops_engine_step_key_unique"),
            models.UniqueConstraint(fields=["engine", "step_order"], name="health_ops_engine_step_order_unique"),
        ]


class ServiceWorkflowSession(TimeStampedUUIDModel):
    institution = models.ForeignKey(HealthInstitution, on_delete=models.CASCADE, related_name="workflow_sessions")
    service = models.ForeignKey(HealthService, on_delete=models.CASCADE, related_name="workflow_sessions")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_ops_workflow_sessions")
    status = models.CharField(max_length=32, choices=WorkflowStatus.choices, default=WorkflowStatus.IN_PROGRESS, db_index=True)
    current_engine_map = models.ForeignKey(
        ServiceEngineMap,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="current_workflow_sessions",
    )
    current_step_index = models.PositiveIntegerField(default=0)
    total_steps = models.PositiveIntegerField(default=0)
    completed_steps = models.PositiveIntegerField(default=0)
    progress_percent = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    is_locked_by_payment = models.BooleanField(default=False)
    requires_assessment = models.BooleanField(default=False)
    assessment_completed = models.BooleanField(default=False)
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "health_ops_workflow_session"
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["service", "created_at"]),
        ]


class HealthCarePlan(TimeStampedUUIDModel):
    institution = models.ForeignKey(HealthInstitution, on_delete=models.CASCADE, related_name="care_plans")
    service = models.ForeignKey(HealthService, on_delete=models.SET_NULL, related_name="care_plans", null=True, blank=True)
    workflow_session = models.ForeignKey(
        ServiceWorkflowSession,
        on_delete=models.SET_NULL,
        related_name="care_plans",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_ops_care_plans")
    title = models.CharField(max_length=180)
    summary = models.TextField(blank=True, default="")
    status = models.CharField(max_length=24, choices=CarePlanStatus.choices, default=CarePlanStatus.ACTIVE, db_index=True)
    goals = models.JSONField(default=list, blank=True)
    tasks = models.JSONField(default=list, blank=True)
    medication_summary = models.JSONField(default=dict, blank=True)
    reminder_summary = models.JSONField(default=dict, blank=True)
    next_review_at = models.DateTimeField(null=True, blank=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "health_ops_care_plan"
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["user", "status"]),
            models.Index(fields=["workflow_session", "status"]),
            models.Index(fields=["next_review_at"]),
        ]

    def __str__(self):
        return self.title


class HealthVitalReading(TimeStampedUUIDModel):
    institution = models.ForeignKey(HealthInstitution, on_delete=models.CASCADE, related_name="vital_readings")
    workflow_session = models.ForeignKey(
        ServiceWorkflowSession,
        on_delete=models.SET_NULL,
        related_name="vital_readings",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_ops_vital_readings")
    reading_type = models.CharField(max_length=40, choices=VitalReadingType.choices, default=VitalReadingType.CUSTOM, db_index=True)
    label = models.CharField(max_length=120, blank=True, default="")
    value = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    unit = models.CharField(max_length=32, blank=True, default="")
    systolic = models.PositiveIntegerField(null=True, blank=True)
    diastolic = models.PositiveIntegerField(null=True, blank=True)
    measured_at = models.DateTimeField(default=timezone.now, db_index=True)
    source = models.CharField(max_length=64, blank=True, default="manual")
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "health_ops_vital_reading"
        ordering = ["-measured_at", "-created_at"]
        indexes = [
            models.Index(fields=["institution", "reading_type"]),
            models.Index(fields=["user", "reading_type", "measured_at"]),
            models.Index(fields=["workflow_session", "reading_type"]),
        ]

    def __str__(self):
        return f"{self.user_id}:{self.reading_type}:{self.measured_at}"


class EngineSession(TimeStampedUUIDModel):
    workflow_session = models.ForeignKey(ServiceWorkflowSession, on_delete=models.CASCADE, related_name="engine_sessions")
    engine_map = models.ForeignKey(ServiceEngineMap, on_delete=models.CASCADE, related_name="engine_sessions")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_ops_engine_sessions")
    progress_step = models.PositiveIntegerField(default=0)
    progress_percent = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    is_completed = models.BooleanField(default=False, db_index=True)
    is_paused = models.BooleanField(default=False, db_index=True)
    is_unlocked = models.BooleanField(default=False, db_index=True)
    is_expired = models.BooleanField(default=False, db_index=True)
    state_blob = models.JSONField(default=dict, blank=True)
    unlocked_at = models.DateTimeField(null=True, blank=True, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    expired_at = models.DateTimeField(null=True, blank=True, db_index=True)
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "health_ops_engine_session"
        constraints = [
            models.UniqueConstraint(fields=["workflow_session", "engine_map"], name="health_ops_workflow_engine_session_unique"),
        ]
        indexes = [
            models.Index(fields=["user", "is_completed"]),
        ]


class EngineStepProgress(TimeStampedUUIDModel):
    engine_session = models.ForeignKey(EngineSession, on_delete=models.CASCADE, related_name="step_progress")
    step_definition = models.ForeignKey(
        EngineStepDefinition,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="session_progress",
    )
    step_key = models.CharField(max_length=120, db_index=True)
    content_position = models.FloatField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    is_completed = models.BooleanField(default=False, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "health_ops_engine_step_progress"
        constraints = [
            models.UniqueConstraint(fields=["engine_session", "step_key"], name="health_ops_engine_step_progress_unique"),
        ]



class VideoConsultationSession(TimeStampedUUIDModel):
    workflow_session = models.OneToOneField(
        ServiceWorkflowSession,
        on_delete=models.CASCADE,
        related_name="video_consultation",
    )
    engine_session = models.OneToOneField(
        EngineSession,
        on_delete=models.CASCADE,
        related_name="video_consultation",
    )
    institution = models.ForeignKey(HealthInstitution, on_delete=models.CASCADE, related_name="video_sessions")
    service = models.ForeignKey(HealthService, on_delete=models.CASCADE, related_name="video_sessions")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_ops_video_sessions")
    room_code = models.CharField(max_length=48, db_index=True)
    host_join_token = models.CharField(max_length=120)
    participant_join_token = models.CharField(max_length=120)
    token_expires_at = models.DateTimeField(db_index=True)
    status = models.CharField(
        max_length=24,
        choices=VideoConsultationStatus.choices,
        default=VideoConsultationStatus.SCHEDULED,
        db_index=True,
    )
    recording_enabled = models.BooleanField(default=False)
    waiting_room_enabled = models.BooleanField(default=True)
    step_state = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "health_ops_video_consultation_session"
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["service", "created_at"]),
            models.Index(fields=["token_expires_at"]),
        ]

    def __str__(self):
        return f"{self.room_code}:{self.status}"


class VideoEngineItem(TimeStampedUUIDModel):
    engine_map = models.ForeignKey(
        ServiceEngineMap,
        on_delete=models.CASCADE,
        related_name="video_items",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    source_url = models.URLField()
    thumbnail_url = models.URLField(blank=True, default="")
    duration_seconds = models.PositiveIntegerField(default=0)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="health_ops_video_items_created",
    )
    updated_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="health_ops_video_items_updated",
    )

    class Meta:
        db_table = "health_ops_video_engine_item"
        ordering = ["sort_order", "created_at"]
        indexes = [
            models.Index(fields=["engine_map", "sort_order"]),
            models.Index(fields=["engine_map", "is_active"]),
        ]

    def __str__(self):
        return self.title


class VideoEngineItemProgress(TimeStampedUUIDModel):
    item = models.ForeignKey(VideoEngineItem, on_delete=models.CASCADE, related_name="progress_rows")
    engine_session = models.ForeignKey(EngineSession, on_delete=models.CASCADE, related_name="video_item_progress_rows")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_ops_video_item_progress_rows")
    watched_seconds = models.PositiveIntegerField(default=0)
    is_completed = models.BooleanField(default=False, db_index=True)
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_watched_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "health_ops_video_engine_item_progress"
        constraints = [
            models.UniqueConstraint(
                fields=["item", "engine_session", "user"],
                name="health_ops_video_item_progress_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["engine_session", "user"]),
            models.Index(fields=["item", "is_completed"]),
        ]


class VideoEngineItemLike(TimeStampedUUIDModel):
    item = models.ForeignKey(VideoEngineItem, on_delete=models.CASCADE, related_name="likes")
    engine_session = models.ForeignKey(EngineSession, on_delete=models.CASCADE, related_name="video_item_likes")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_ops_video_item_likes")

    class Meta:
        db_table = "health_ops_video_engine_item_like"
        constraints = [
            models.UniqueConstraint(
                fields=["item", "engine_session", "user"],
                name="health_ops_video_item_like_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["item", "created_at"]),
            models.Index(fields=["engine_session", "created_at"]),
        ]


class VideoEngineItemComment(TimeStampedUUIDModel):
    item = models.ForeignKey(VideoEngineItem, on_delete=models.CASCADE, related_name="comments")
    engine_session = models.ForeignKey(EngineSession, on_delete=models.CASCADE, related_name="video_item_comments")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_ops_video_item_comments")
    body = models.TextField()
    is_deleted = models.BooleanField(default=False, db_index=True)

    class Meta:
        db_table = "health_ops_video_engine_item_comment"
        indexes = [
            models.Index(fields=["item", "created_at"]),
            models.Index(fields=["engine_session", "created_at"]),
            models.Index(fields=["user", "created_at"]),
        ]


class SecureMessagingSession(TimeStampedUUIDModel):
    workflow_session = models.OneToOneField(
        ServiceWorkflowSession,
        on_delete=models.CASCADE,
        related_name="secure_messaging",
    )
    engine_session = models.OneToOneField(
        EngineSession,
        on_delete=models.CASCADE,
        related_name="secure_messaging",
    )
    institution = models.ForeignKey(HealthInstitution, on_delete=models.CASCADE, related_name="secure_messaging_sessions")
    service = models.ForeignKey(HealthService, on_delete=models.CASCADE, related_name="secure_messaging_sessions")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_ops_secure_messaging_sessions")
    thread_code = models.CharField(max_length=48, db_index=True)
    status = models.CharField(
        max_length=24,
        choices=SecureMessagingStatus.choices,
        default=SecureMessagingStatus.WAITING,
        db_index=True,
    )
    step_state = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    last_message_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "health_ops_secure_messaging_session"
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["service", "created_at"]),
            models.Index(fields=["thread_code"]),
        ]

    def __str__(self):
        return f"{self.thread_code}:{self.status}"


class SecureMessage(TimeStampedUUIDModel):
    session = models.ForeignKey(SecureMessagingSession, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_ops_secure_messages")
    message_type = models.CharField(max_length=16, choices=SecureMessageType.choices, default=SecureMessageType.TEXT)
    body = models.TextField(blank=True, default="")
    attachment_url = models.URLField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    delivered_at = models.DateTimeField(default=timezone.now, db_index=True)
    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "health_ops_secure_message"
        indexes = [
            models.Index(fields=["session", "created_at"]),
            models.Index(fields=["sender", "created_at"]),
            models.Index(fields=["is_read", "created_at"]),
        ]

    def __str__(self):
        return f"{self.session_id}:{self.message_type}"


class ClinicalEngineSession(TimeStampedUUIDModel):
    workflow_session = models.ForeignKey(
        ServiceWorkflowSession,
        on_delete=models.CASCADE,
        related_name="clinical_engine_sessions",
    )
    engine_session = models.OneToOneField(
        EngineSession,
        on_delete=models.CASCADE,
        related_name="clinical_session",
    )
    institution = models.ForeignKey(HealthInstitution, on_delete=models.CASCADE, related_name="clinical_engine_sessions")
    service = models.ForeignKey(HealthService, on_delete=models.CASCADE, related_name="clinical_engine_sessions")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_ops_clinical_engine_sessions")
    engine_code = models.CharField(max_length=64, choices=ClinicalEngineCode.choices, db_index=True)
    status = models.CharField(
        max_length=24,
        choices=ClinicalEngineSessionStatus.choices,
        default=ClinicalEngineSessionStatus.WAITING,
        db_index=True,
    )
    step_state = models.JSONField(default=dict, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "health_ops_clinical_engine_session"
        constraints = [
            models.UniqueConstraint(fields=["workflow_session", "engine_code"], name="health_ops_clinical_engine_unique"),
        ]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["service", "engine_code"]),
            models.Index(fields=["workflow_session", "engine_code"]),
        ]

    def __str__(self):
        return f"{self.workflow_session_id}:{self.engine_code}:{self.status}"


class AdmissionBedSession(TimeStampedUUIDModel):
    workflow_session = models.OneToOneField(
        ServiceWorkflowSession,
        on_delete=models.CASCADE,
        related_name="admission_bed",
    )
    engine_session = models.OneToOneField(
        EngineSession,
        on_delete=models.CASCADE,
        related_name="admission_bed",
    )
    institution = models.ForeignKey(HealthInstitution, on_delete=models.CASCADE, related_name="admission_bed_sessions")
    service = models.ForeignKey(HealthService, on_delete=models.CASCADE, related_name="admission_bed_sessions")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_ops_admission_bed_sessions")
    status = models.CharField(
        max_length=24,
        choices=AdmissionBedStatus.choices,
        default=AdmissionBedStatus.WAITING,
        db_index=True,
    )
    ward_name = models.CharField(max_length=120, blank=True, default="")
    bed_code = models.CharField(max_length=80, blank=True, default="")
    triage_level = models.CharField(max_length=40, blank=True, default="")
    requires_isolation = models.BooleanField(default=False)
    requires_icu = models.BooleanField(default=False)
    step_state = models.JSONField(default=dict, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    assigned_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "health_ops_admission_bed_session"
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["service", "created_at"]),
            models.Index(fields=["ward_name", "bed_code"]),
        ]

    def __str__(self):
        return f"{self.workflow_session_id}:{self.status}"


class EmergencyDispatchSession(TimeStampedUUIDModel):
    workflow_session = models.OneToOneField(
        ServiceWorkflowSession,
        on_delete=models.CASCADE,
        related_name="emergency_dispatch",
    )
    engine_session = models.OneToOneField(
        EngineSession,
        on_delete=models.CASCADE,
        related_name="emergency_dispatch",
    )
    institution = models.ForeignKey(HealthInstitution, on_delete=models.CASCADE, related_name="emergency_dispatch_sessions")
    service = models.ForeignKey(HealthService, on_delete=models.CASCADE, related_name="emergency_dispatch_sessions")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_ops_emergency_dispatch_sessions")
    dispatch_code = models.CharField(max_length=64, db_index=True)
    status = models.CharField(
        max_length=24,
        choices=EmergencyDispatchStatus.choices,
        default=EmergencyDispatchStatus.WAITING,
        db_index=True,
    )
    triage_level = models.CharField(max_length=40, blank=True, default="")
    location_latitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    location_longitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    ambulance_reference = models.CharField(max_length=120, blank=True, default="")
    current_eta_minutes = models.PositiveIntegerField(null=True, blank=True)
    step_state = models.JSONField(default=dict, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    tracking_events = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    arrived_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    last_tracking_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "health_ops_emergency_dispatch_session"
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["service", "created_at"]),
            models.Index(fields=["dispatch_code"]),
            models.Index(fields=["last_tracking_at"]),
        ]

    def __str__(self):
        return f"{self.dispatch_code}:{self.status}"


class PharmacyFulfillmentSession(TimeStampedUUIDModel):
    workflow_session = models.OneToOneField(
        ServiceWorkflowSession,
        on_delete=models.CASCADE,
        related_name="pharmacy_fulfillment",
    )
    engine_session = models.OneToOneField(
        EngineSession,
        on_delete=models.CASCADE,
        related_name="pharmacy_fulfillment",
    )
    institution = models.ForeignKey(HealthInstitution, on_delete=models.CASCADE, related_name="pharmacy_fulfillment_sessions")
    service = models.ForeignKey(HealthService, on_delete=models.CASCADE, related_name="pharmacy_fulfillment_sessions")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_ops_pharmacy_fulfillment_sessions")
    status = models.CharField(
        max_length=32,
        choices=PharmacyFulfillmentStatus.choices,
        default=PharmacyFulfillmentStatus.WAITING,
        db_index=True,
    )
    cart_items = models.JSONField(default=list, blank=True)
    delivery_mode = models.CharField(max_length=32, blank=True, default="")
    payment_reference = models.CharField(max_length=120, blank=True, default="", db_index=True)
    fulfillment_reference = models.CharField(max_length=120, blank=True, default="")
    current_eta_minutes = models.PositiveIntegerField(null=True, blank=True)
    step_state = models.JSONField(default=dict, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    tracking_events = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    last_tracking_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "health_ops_pharmacy_fulfillment_session"
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["service", "created_at"]),
            models.Index(fields=["payment_reference"]),
            models.Index(fields=["last_tracking_at"]),
        ]

    def __str__(self):
        return f"{self.workflow_session_id}:{self.status}"


class PaymentBillingSession(TimeStampedUUIDModel):
    workflow_session = models.OneToOneField(
        ServiceWorkflowSession,
        on_delete=models.CASCADE,
        related_name="payment_billing",
    )
    engine_session = models.OneToOneField(
        EngineSession,
        on_delete=models.CASCADE,
        related_name="payment_billing",
    )
    institution = models.ForeignKey(HealthInstitution, on_delete=models.CASCADE, related_name="payment_billing_sessions")
    service = models.ForeignKey(HealthService, on_delete=models.CASCADE, related_name="payment_billing_sessions")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_ops_payment_billing_sessions")
    status = models.CharField(
        max_length=24,
        choices=PaymentBillingStatus.choices,
        default=PaymentBillingStatus.WAITING,
        db_index=True,
    )
    total_amount_micro = models.BigIntegerField(default=0, validators=[MinValueValidator(0)])
    insurance_coverage_micro = models.BigIntegerField(default=0, validators=[MinValueValidator(0)])
    payable_amount_micro = models.BigIntegerField(default=0, validators=[MinValueValidator(0)])
    amount_paid_micro = models.BigIntegerField(default=0, validators=[MinValueValidator(0)])
    payment_provider = models.CharField(max_length=40, blank=True, default="")
    payment_reference = models.CharField(max_length=120, blank=True, default="", db_index=True)
    invoice_number = models.CharField(max_length=80, blank=True, default="", db_index=True)
    step_state = models.JSONField(default=dict, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "health_ops_payment_billing_session"
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["service", "created_at"]),
            models.Index(fields=["payment_reference"]),
            models.Index(fields=["invoice_number"]),
        ]

    def __str__(self):
        return f"{self.workflow_session_id}:{self.status}"


class HomeLogisticsSession(TimeStampedUUIDModel):
    workflow_session = models.OneToOneField(
        ServiceWorkflowSession,
        on_delete=models.CASCADE,
        related_name="home_logistics",
    )
    engine_session = models.OneToOneField(
        EngineSession,
        on_delete=models.CASCADE,
        related_name="home_logistics",
    )
    institution = models.ForeignKey(HealthInstitution, on_delete=models.CASCADE, related_name="home_logistics_sessions")
    service = models.ForeignKey(HealthService, on_delete=models.CASCADE, related_name="home_logistics_sessions")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_ops_home_logistics_sessions")
    logistics_code = models.CharField(max_length=64, db_index=True)
    status = models.CharField(
        max_length=24,
        choices=HomeLogisticsStatus.choices,
        default=HomeLogisticsStatus.WAITING,
        db_index=True,
    )
    task_type = models.CharField(max_length=40, blank=True, default="")
    route_reference = models.CharField(max_length=120, blank=True, default="")
    assignee_name = models.CharField(max_length=120, blank=True, default="")
    current_eta_minutes = models.PositiveIntegerField(null=True, blank=True)
    location_latitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    location_longitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    scheduled_window_start = models.DateTimeField(null=True, blank=True)
    scheduled_window_end = models.DateTimeField(null=True, blank=True)
    step_state = models.JSONField(default=dict, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    tracking_events = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    arrived_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    last_tracking_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "health_ops_home_logistics_session"
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["service", "created_at"]),
            models.Index(fields=["logistics_code"]),
            models.Index(fields=["last_tracking_at"]),
        ]

    def __str__(self):
        return f"{self.logistics_code}:{self.status}"


class WellnessProgramSession(TimeStampedUUIDModel):
    workflow_session = models.OneToOneField(
        ServiceWorkflowSession,
        on_delete=models.CASCADE,
        related_name="wellness_program",
    )
    engine_session = models.OneToOneField(
        EngineSession,
        on_delete=models.CASCADE,
        related_name="wellness_program",
    )
    institution = models.ForeignKey(HealthInstitution, on_delete=models.CASCADE, related_name="wellness_program_sessions")
    service = models.ForeignKey(HealthService, on_delete=models.CASCADE, related_name="wellness_program_sessions")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_ops_wellness_program_sessions")
    status = models.CharField(
        max_length=24,
        choices=WellnessProgramStatus.choices,
        default=WellnessProgramStatus.WAITING,
        db_index=True,
    )
    program_name = models.CharField(max_length=160, blank=True, default="")
    goal_payload = models.JSONField(default=dict, blank=True)
    habit_payload = models.JSONField(default=dict, blank=True)
    current_streak = models.PositiveIntegerField(default=0)
    completion_percent = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    step_state = models.JSONField(default=dict, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    activity_events = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    paused_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "health_ops_wellness_program_session"
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["service", "created_at"]),
            models.Index(fields=["program_name"]),
            models.Index(fields=["last_activity_at"]),
        ]

    def __str__(self):
        return f"{self.workflow_session_id}:{self.status}"


class NotificationReminderSession(TimeStampedUUIDModel):
    workflow_session = models.OneToOneField(
        ServiceWorkflowSession,
        on_delete=models.CASCADE,
        related_name="notification_reminder",
    )
    engine_session = models.OneToOneField(
        EngineSession,
        on_delete=models.CASCADE,
        related_name="notification_reminder",
    )
    institution = models.ForeignKey(HealthInstitution, on_delete=models.CASCADE, related_name="notification_reminder_sessions")
    service = models.ForeignKey(HealthService, on_delete=models.CASCADE, related_name="notification_reminder_sessions")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_ops_notification_reminder_sessions")
    status = models.CharField(
        max_length=24,
        choices=NotificationReminderStatus.choices,
        default=NotificationReminderStatus.WAITING,
        db_index=True,
    )
    channel_config = models.JSONField(default=dict, blank=True)
    rule_config = models.JSONField(default=dict, blank=True)
    reminder_timezone = models.CharField(max_length=64, default="UTC")
    next_run_at = models.DateTimeField(null=True, blank=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    sent_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    step_state = models.JSONField(default=dict, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    delivery_events = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    last_delivery_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "health_ops_notification_reminder_session"
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["service", "created_at"]),
            models.Index(fields=["next_run_at"]),
            models.Index(fields=["last_delivery_at"]),
        ]

    def __str__(self):
        return f"{self.workflow_session_id}:{self.status}"


class InstitutionEngineManagedItem(TimeStampedUUIDModel):
    institution = models.ForeignKey(
        HealthInstitution,
        on_delete=models.CASCADE,
        related_name="engine_managed_items",
    )
    engine_key = models.SlugField(max_length=120, db_index=True)
    engine_name = models.CharField(max_length=255, blank=True, default="")
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="children",
    )
    item_kind = models.SlugField(max_length=80, db_index=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    amount_micro = models.BigIntegerField(default=0, validators=[MinValueValidator(0)])
    quantity = models.IntegerField(null=True, blank=True, validators=[MinValueValidator(0)])
    value_int = models.IntegerField(null=True, blank=True)
    value_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=60, blank=True, default="", db_index=True)
    # Keep this flexible: managers may send local/file URIs or uploaded URLs.
    image_url = models.TextField(blank=True, null=True)
    sort_order = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_ops_managed_items_created")
    updated_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="health_ops_managed_items_updated",
    )

    class Meta:
        db_table = "health_ops_institution_engine_managed_item"
        ordering = ["sort_order", "created_at"]
        indexes = [
            models.Index(fields=["institution", "engine_key"]),
            models.Index(fields=["institution", "engine_key", "item_kind"]),
            models.Index(fields=["institution", "engine_key", "parent"]),
        ]


class EngineContentBlock(TimeStampedUUIDModel):
    engine = models.ForeignKey(EngineRegistry, on_delete=models.CASCADE, related_name="content_blocks")
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="health_ops_content_blocks")
    block_type = models.CharField(max_length=20, choices=ContentBlockType.choices)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    file_url = models.URLField(blank=True, null=True)
    text_content = models.TextField(blank=True, default="")
    order = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "health_ops_engine_content_block"
        ordering = ["order", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["engine", "order"], name="health_ops_engine_content_order_unique"),
        ]


class HealthOpsAuditLog(TimeStampedUUIDModel):
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="health_ops_audit_logs")
    institution = models.ForeignKey(
        HealthInstitution,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=120, db_index=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "health_ops_audit_log"
        indexes = [
            models.Index(fields=["action", "created_at"]),
        ]
