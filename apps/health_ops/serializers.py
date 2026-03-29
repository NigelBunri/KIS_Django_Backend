from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.conf import settings
from rest_framework import serializers

from .models import (
    AdmissionBedSession,
    AdmissionBedStatus,
    EngineCompletionMode,
    ClinicalEngineCode,
    ClinicalEngineSession,
    ClinicalEngineSessionStatus,
    EngineContentBlock,
    EmergencyDispatchSession,
    EmergencyDispatchStatus,
    EngineRegistry,
    EngineSession,
    EngineStepProgress,
    HealthInstitution,
    HealthInstitutionMembership,
    HomeLogisticsSession,
    HomeLogisticsStatus,
    HealthService,
    InstitutionEngineManagedItem,
    NotificationReminderSession,
    NotificationReminderStatus,
    PaymentBillingSession,
    PaymentBillingStatus,
    PharmacyFulfillmentSession,
    PharmacyFulfillmentStatus,
    SecureMessage,
    SecureMessageType,
    SecureMessagingSession,
    SecureMessagingStatus,
    ServiceEngineMap,
    ServiceWorkflowSession,
    VideoEngineItem,
    VideoEngineItemComment,
    VideoEngineItemLike,
    VideoEngineItemProgress,
    VideoConsultationStatus,
    VideoConsultationSession,
    WellnessProgramSession,
    WellnessProgramStatus,
)
from .services import (
    build_workflow_runtime_payload,
    get_engine_remaining_seconds,
    get_engine_runtime_state,
    resolve_engine_access_window_days,
)

KISC_MICRO_PER_KISC = 100000


def _micro_to_kisc_text(micro_value) -> str:
    try:
        micro = int(micro_value or 0)
    except (TypeError, ValueError):
        micro = 0
    safe = max(0, micro)
    amount = (Decimal(safe) / Decimal(KISC_MICRO_PER_KISC)).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)
    text = format(amount, "f").rstrip("0").rstrip(".")
    return text or "0"


def _kisc_to_micro_or_none(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        raise serializers.ValidationError("Invalid KISC amount.")
    if parsed < 0:
        raise serializers.ValidationError("KISC amount cannot be negative.")
    return int((parsed * Decimal(KISC_MICRO_PER_KISC)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


class EngineRegistrySerializer(serializers.ModelSerializer):
    class Meta:
        model = EngineRegistry
        fields = [
            "id",
            "code",
            "name",
            "description",
            "category",
            "is_fixed",
            "is_active",
            "schema_version",
            "default_step_count",
            "metadata",
        ]


class HealthInstitutionSerializer(serializers.ModelSerializer):
    class Meta:
        model = HealthInstitution
        fields = [
            "id",
            "owner",
            "name",
            "slug",
            "institution_type",
            "timezone",
            "settings",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["owner", "slug", "created_at", "updated_at"]


class HealthServiceSerializer(serializers.ModelSerializer):
    base_cost_kisc = serializers.SerializerMethodField()

    class Meta:
        model = HealthService
        fields = [
            "id",
            "institution",
            "name",
            "description",
            "is_active",
            "requires_assessment",
            "assessment_schema",
            "base_cost_micro",
            "base_cost_kisc",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def get_base_cost_kisc(self, obj):
        return _micro_to_kisc_text(getattr(obj, "base_cost_micro", 0))


class ServiceEngineMapSerializer(serializers.ModelSerializer):
    engine = EngineRegistrySerializer(read_only=True)
    engine_id = serializers.UUIDField(write_only=True, required=True)
    cost_kisc = serializers.SerializerMethodField()

    class Meta:
        model = ServiceEngineMap
        fields = [
            "id",
            "service",
            "engine",
            "engine_id",
            "execution_order",
            "config",
            "cost_micro",
            "cost_kisc",
            "is_required",
            "access_window_days",
            "completion_mode",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def create(self, validated_data):
        engine_id = validated_data.pop("engine_id")
        validated_data["engine_id"] = engine_id
        return super().create(validated_data)

    def get_cost_kisc(self, obj):
        return _micro_to_kisc_text(getattr(obj, "cost_micro", 0))


class EngineStepProgressSerializer(serializers.ModelSerializer):
    class Meta:
        model = EngineStepProgress
        fields = [
            "id",
            "engine_session",
            "step_definition",
            "step_key",
            "content_position",
            "payload",
            "is_completed",
            "completed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["completed_at", "created_at", "updated_at"]


class EngineSessionSerializer(serializers.ModelSerializer):
    engine_code = serializers.SerializerMethodField()
    engine_name = serializers.SerializerMethodField()
    runtime_state = serializers.SerializerMethodField()
    remaining_seconds = serializers.SerializerMethodField()
    access_window_days = serializers.SerializerMethodField()

    class Meta:
        model = EngineSession
        fields = [
            "id",
            "workflow_session",
            "engine_map",
            "user",
            "engine_code",
            "engine_name",
            "progress_step",
            "progress_percent",
            "is_completed",
            "is_paused",
            "is_unlocked",
            "is_expired",
            "runtime_state",
            "access_window_days",
            "remaining_seconds",
            "state_blob",
            "unlocked_at",
            "expires_at",
            "expired_at",
            "started_at",
            "completed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def get_engine_code(self, obj):
        engine = getattr(getattr(obj, "engine_map", None), "engine", None)
        return str(getattr(engine, "code", "") or "")

    def get_engine_name(self, obj):
        engine = getattr(getattr(obj, "engine_map", None), "engine", None)
        return str(getattr(engine, "name", "") or "")

    def get_runtime_state(self, obj):
        return get_engine_runtime_state(obj)

    def get_remaining_seconds(self, obj):
        return get_engine_remaining_seconds(obj)

    def get_access_window_days(self, obj):
        return int(resolve_engine_access_window_days(obj))


class ServiceWorkflowSessionSerializer(serializers.ModelSerializer):
    engine_sessions = EngineSessionSerializer(many=True, read_only=True)
    runtime = serializers.SerializerMethodField()

    class Meta:
        model = ServiceWorkflowSession
        fields = [
            "id",
            "institution",
            "service",
            "user",
            "status",
            "current_engine_map",
            "current_step_index",
            "total_steps",
            "completed_steps",
            "progress_percent",
            "is_locked_by_payment",
            "requires_assessment",
            "assessment_completed",
            "started_at",
            "completed_at",
            "metadata",
            "engine_sessions",
            "runtime",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "current_engine_map",
            "current_step_index",
            "total_steps",
            "completed_steps",
            "progress_percent",
            "started_at",
            "completed_at",
            "created_at",
            "updated_at",
        ]

    def get_runtime(self, obj):
        return build_workflow_runtime_payload(obj)


class WorkflowStartSerializer(serializers.Serializer):
    institution_id = serializers.UUIDField()
    service_id = serializers.UUIDField()
    auto_debit = serializers.BooleanField(default=True)
    owner_preview = serializers.BooleanField(default=False)
    assessment_payload = serializers.JSONField(required=False, default=dict)


class WorkflowStepUpdateSerializer(serializers.Serializer):
    engine_session_id = serializers.UUIDField()
    step_key = serializers.SlugField(max_length=120)
    is_completed = serializers.BooleanField(default=False)
    content_position = serializers.FloatField(required=False, allow_null=True)
    payload = serializers.JSONField(required=False, default=dict)


class EngineContentBlockSerializer(serializers.ModelSerializer):
    class Meta:
        model = EngineContentBlock
        fields = [
            "id",
            "engine",
            "created_by",
            "block_type",
            "title",
            "description",
            "file_url",
            "text_content",
            "order",
            "is_active",
            "version",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_by", "version", "created_at", "updated_at"]


class InstitutionEngineManagedItemSerializer(serializers.ModelSerializer):
    amount_kisc = serializers.SerializerMethodField()
    image_url = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    amount_kisc_input = serializers.DecimalField(
        max_digits=20,
        decimal_places=5,
        required=False,
        min_value=0,
        write_only=True,
    )

    class Meta:
        model = InstitutionEngineManagedItem
        fields = [
            "id",
            "institution",
            "engine_key",
            "engine_name",
            "parent",
            "item_kind",
            "name",
            "description",
            "amount_micro",
            "amount_kisc",
            "amount_kisc_input",
            "quantity",
            "value_int",
            "value_date",
            "status",
            "image_url",
            "sort_order",
            "is_active",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_by", "updated_by", "created_at", "updated_at"]

    def get_amount_kisc(self, obj):
        return _micro_to_kisc_text(getattr(obj, "amount_micro", 0))

    def validate(self, attrs):
        if "amount_kisc_input" in attrs and "amount_micro" not in attrs:
            attrs["amount_micro"] = _kisc_to_micro_or_none(attrs.get("amount_kisc_input")) or 0
        if "image_url" in attrs:
            raw_value = attrs.get("image_url")
            if raw_value is None:
                attrs["image_url"] = None
            else:
                clean = str(raw_value).strip()
                attrs["image_url"] = clean or None
        return attrs


class MembershipSerializer(serializers.ModelSerializer):
    class Meta:
        model = HealthInstitutionMembership
        fields = ["id", "institution", "user", "role", "is_active", "invited_by", "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at", "invited_by"]

VIDEO_CONSULTATION_STEP_CHOICES = (
    ("confirm_identity", "Confirm identity"),
    ("test_mic_camera", "Test mic/camera"),
    ("confirm_consent", "Confirm consent"),
    ("join_session", "Join session"),
    ("post_session_summary", "Post-session summary"),
)


class VideoConsultationSessionSerializer(serializers.ModelSerializer):
    host_join_url = serializers.SerializerMethodField()
    participant_join_url = serializers.SerializerMethodField()

    class Meta:
        model = VideoConsultationSession
        fields = [
            "id",
            "workflow_session",
            "engine_session",
            "institution",
            "service",
            "user",
            "room_code",
            "host_join_token",
            "participant_join_token",
            "token_expires_at",
            "status",
            "recording_enabled",
            "waiting_room_enabled",
            "step_state",
            "metadata",
            "started_at",
            "ended_at",
            "host_join_url",
            "participant_join_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "workflow_session",
            "engine_session",
            "institution",
            "service",
            "user",
            "room_code",
            "host_join_token",
            "participant_join_token",
            "token_expires_at",
            "status",
            "step_state",
            "started_at",
            "ended_at",
            "created_at",
            "updated_at",
        ]

    def _video_base_url(self) -> str:
        default_url = "https://video.kis.health"
        configured = str(getattr(settings, "HEALTH_OPS_VIDEO_BASE_URL", "") or "").strip()
        if configured:
            return configured.rstrip("/")
        request = self.context.get("request")
        if request:
            return request.build_absolute_uri("/video").rstrip("/")
        return default_url

    def get_host_join_url(self, obj):
        base = self._video_base_url()
        return f"{base}/room/{obj.room_code}?role=host&token={obj.host_join_token}"

    def get_participant_join_url(self, obj):
        base = self._video_base_url()
        return f"{base}/room/{obj.room_code}?role=participant&token={obj.participant_join_token}"


class VideoConsultationStartSerializer(serializers.Serializer):
    workflow_session_id = serializers.UUIDField()
    recording_enabled = serializers.BooleanField(default=False)
    waiting_room_enabled = serializers.BooleanField(default=True)
    metadata = serializers.JSONField(required=False, default=dict)


class VideoConsultationStepUpdateSerializer(serializers.Serializer):
    step_key = serializers.ChoiceField(choices=VIDEO_CONSULTATION_STEP_CHOICES)
    is_completed = serializers.BooleanField(default=True)
    payload = serializers.JSONField(required=False, default=dict)


class VideoConsultationEndSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[
            (VideoConsultationStatus.COMPLETED, "Completed"),
            (VideoConsultationStatus.CANCELLED, "Cancelled"),
        ],
        default=VideoConsultationStatus.COMPLETED,
    )
    summary = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    metadata = serializers.JSONField(required=False, default=dict)


class VideoEngineItemSerializer(serializers.ModelSerializer):
    likes_count = serializers.SerializerMethodField()
    comments_count = serializers.SerializerMethodField()
    viewer_liked = serializers.SerializerMethodField()
    viewer_progress_seconds = serializers.SerializerMethodField()
    viewer_completed = serializers.SerializerMethodField()

    class Meta:
        model = VideoEngineItem
        fields = [
            "id",
            "engine_map",
            "title",
            "description",
            "source_url",
            "thumbnail_url",
            "duration_seconds",
            "sort_order",
            "is_active",
            "likes_count",
            "comments_count",
            "viewer_liked",
            "viewer_progress_seconds",
            "viewer_completed",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def _viewer_progress_row(self, obj):
        request = self.context.get("request")
        if not request or not getattr(request, "user", None) or not request.user.is_authenticated:
            return None
        engine_session_id = self.context.get("engine_session_id")
        qs = obj.progress_rows.filter(user=request.user)
        if engine_session_id:
            qs = qs.filter(engine_session_id=engine_session_id)
        return qs.order_by("-updated_at").first()

    def get_likes_count(self, obj):
        return int(obj.likes.count())

    def get_comments_count(self, obj):
        return int(obj.comments.filter(is_deleted=False).count())

    def get_viewer_liked(self, obj):
        request = self.context.get("request")
        if not request or not getattr(request, "user", None) or not request.user.is_authenticated:
            return False
        engine_session_id = self.context.get("engine_session_id")
        qs = obj.likes.filter(user=request.user)
        if engine_session_id:
            qs = qs.filter(engine_session_id=engine_session_id)
        return qs.exists()

    def get_viewer_progress_seconds(self, obj):
        row = self._viewer_progress_row(obj)
        return int(getattr(row, "watched_seconds", 0) or 0) if row else 0

    def get_viewer_completed(self, obj):
        row = self._viewer_progress_row(obj)
        return bool(getattr(row, "is_completed", False)) if row else False


class VideoEngineItemProgressSerializer(serializers.ModelSerializer):
    class Meta:
        model = VideoEngineItemProgress
        fields = [
            "id",
            "item",
            "engine_session",
            "user",
            "watched_seconds",
            "is_completed",
            "started_at",
            "completed_at",
            "last_watched_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["user", "started_at", "completed_at", "last_watched_at", "created_at", "updated_at"]


class VideoEngineItemCommentSerializer(serializers.ModelSerializer):
    user_id = serializers.UUIDField(source="user.id", read_only=True)

    class Meta:
        model = VideoEngineItemComment
        fields = [
            "id",
            "item",
            "engine_session",
            "user",
            "user_id",
            "body",
            "is_deleted",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["user", "is_deleted", "created_at", "updated_at"]


class VideoEngineItemCommentCreateSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=2000)


SECURE_MESSAGING_STEP_CHOICES = (
    ("open_thread", "Open thread"),
    ("send_message", "Send first message"),
    ("attach_files", "Attach files"),
    ("close_thread", "Close thread"),
)


class SecureMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = SecureMessage
        fields = [
            "id",
            "session",
            "sender",
            "message_type",
            "body",
            "attachment_url",
            "metadata",
            "delivered_at",
            "is_read",
            "read_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "sender",
            "delivered_at",
            "is_read",
            "read_at",
            "created_at",
            "updated_at",
        ]


class SecureMessagingSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = SecureMessagingSession
        fields = [
            "id",
            "workflow_session",
            "engine_session",
            "institution",
            "service",
            "user",
            "thread_code",
            "status",
            "step_state",
            "metadata",
            "started_at",
            "last_message_at",
            "ended_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "workflow_session",
            "engine_session",
            "institution",
            "service",
            "user",
            "thread_code",
            "status",
            "step_state",
            "started_at",
            "last_message_at",
            "ended_at",
            "created_at",
            "updated_at",
        ]


class SecureMessagingStartSerializer(serializers.Serializer):
    workflow_session_id = serializers.UUIDField()
    metadata = serializers.JSONField(required=False, default=dict)


class SecureMessagingStepUpdateSerializer(serializers.Serializer):
    step_key = serializers.ChoiceField(choices=SECURE_MESSAGING_STEP_CHOICES)
    is_completed = serializers.BooleanField(default=True)
    payload = serializers.JSONField(required=False, default=dict)


class SecureMessageCreateSerializer(serializers.Serializer):
    message_type = serializers.ChoiceField(choices=SecureMessageType.choices, default=SecureMessageType.TEXT)
    body = serializers.CharField(required=False, allow_blank=True, default="")
    attachment_url = serializers.URLField(required=False, allow_blank=True)
    metadata = serializers.JSONField(required=False, default=dict)

    def validate(self, attrs):
        body = str(attrs.get("body") or "").strip()
        attachment_url = str(attrs.get("attachment_url") or "").strip()
        if not body and not attachment_url:
            raise serializers.ValidationError("Either body or attachment_url is required.")
        attrs["body"] = body
        attrs["attachment_url"] = attachment_url or None
        return attrs


class SecureMessagingEndSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[
            (SecureMessagingStatus.COMPLETED, "Completed"),
            (SecureMessagingStatus.CLOSED, "Closed"),
        ],
        default=SecureMessagingStatus.COMPLETED,
    )
    summary = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    metadata = serializers.JSONField(required=False, default=dict)


class ClinicalEngineSessionSerializer(serializers.ModelSerializer):
 class Meta:
        model = ClinicalEngineSession
        fields = [
            "id",
            "workflow_session",
            "engine_session",
            "institution",
            "service",
            "user",
            "engine_code",
            "status",
            "step_state",
            "payload",
            "metadata",
            "started_at",
            "ended_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "workflow_session",
            "engine_session",
            "institution",
            "service",
            "user",
            "engine_code",
            "status",
            "step_state",
            "started_at",
            "ended_at",
            "created_at",
            "updated_at",
        ]


class ClinicalEngineStartSerializer(serializers.Serializer):
    workflow_session_id = serializers.UUIDField()
    engine_code = serializers.ChoiceField(choices=ClinicalEngineCode.choices)
    payload = serializers.JSONField(required=False, default=dict)
    metadata = serializers.JSONField(required=False, default=dict)


class ClinicalEngineStepUpdateSerializer(serializers.Serializer):
    step_key = serializers.SlugField(max_length=120)
    is_completed = serializers.BooleanField(default=True)
    content_position = serializers.FloatField(required=False, allow_null=True)
    payload = serializers.JSONField(required=False, default=dict)


class ClinicalEnginePayloadSerializer(serializers.Serializer):
    payload = serializers.JSONField(required=False, default=dict)
    metadata = serializers.JSONField(required=False, default=dict)
    merge = serializers.BooleanField(default=True)


class ClinicalEngineEndSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[
            (ClinicalEngineSessionStatus.COMPLETED, "Completed"),
            (ClinicalEngineSessionStatus.CANCELLED, "Cancelled"),
        ],
        default=ClinicalEngineSessionStatus.COMPLETED,
    )
    summary = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    metadata = serializers.JSONField(required=False, default=dict)


ADMISSION_BED_STEP_CHOICES = (
    ("admission_reason", "Admission reason"),
    ("insurance_verification", "Insurance verification"),
    ("bed_assignment", "Bed assignment"),
    ("admission_confirmation", "Admission confirmation"),
)


class AdmissionBedSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdmissionBedSession
        fields = [
            "id",
            "workflow_session",
            "engine_session",
            "institution",
            "service",
            "user",
            "status",
            "ward_name",
            "bed_code",
            "triage_level",
            "requires_isolation",
            "requires_icu",
            "step_state",
            "payload",
            "metadata",
            "started_at",
            "assigned_at",
            "ended_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "workflow_session",
            "engine_session",
            "institution",
            "service",
            "user",
            "status",
            "step_state",
            "started_at",
            "assigned_at",
            "ended_at",
            "created_at",
            "updated_at",
        ]


class AdmissionBedStartSerializer(serializers.Serializer):
    workflow_session_id = serializers.UUIDField()
    payload = serializers.JSONField(required=False, default=dict)
    metadata = serializers.JSONField(required=False, default=dict)


class AdmissionBedStepUpdateSerializer(serializers.Serializer):
    step_key = serializers.SlugField(max_length=120)
    is_completed = serializers.BooleanField(default=True)
    payload = serializers.JSONField(required=False, default=dict)


class AdmissionBedPayloadSerializer(serializers.Serializer):
    payload = serializers.JSONField(required=False, default=dict)
    metadata = serializers.JSONField(required=False, default=dict)
    merge = serializers.BooleanField(default=True)


class AdmissionBedEndSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[
            (AdmissionBedStatus.COMPLETED, "Completed"),
            (AdmissionBedStatus.CANCELLED, "Cancelled"),
        ],
        default=AdmissionBedStatus.COMPLETED,
    )
    summary = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    metadata = serializers.JSONField(required=False, default=dict)


EMERGENCY_DISPATCH_STEP_CHOICES = (
    ("capture_location", "Capture location"),
    ("triage_form", "Triage form"),
    ("dispatch_ambulance", "Dispatch ambulance"),
    ("track_response", "Track response"),
)


class EmergencyDispatchSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmergencyDispatchSession
        fields = [
            "id",
            "workflow_session",
            "engine_session",
            "institution",
            "service",
            "user",
            "dispatch_code",
            "status",
            "triage_level",
            "location_latitude",
            "location_longitude",
            "ambulance_reference",
            "current_eta_minutes",
            "step_state",
            "payload",
            "tracking_events",
            "metadata",
            "started_at",
            "dispatched_at",
            "arrived_at",
            "resolved_at",
            "ended_at",
            "last_tracking_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "workflow_session",
            "engine_session",
            "institution",
            "service",
            "user",
            "dispatch_code",
            "status",
            "step_state",
            "tracking_events",
            "started_at",
            "dispatched_at",
            "arrived_at",
            "resolved_at",
            "ended_at",
            "last_tracking_at",
            "created_at",
            "updated_at",
        ]


class EmergencyDispatchStartSerializer(serializers.Serializer):
    workflow_session_id = serializers.UUIDField()
    dispatch_code = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    payload = serializers.JSONField(required=False, default=dict)
    metadata = serializers.JSONField(required=False, default=dict)


class EmergencyDispatchStepUpdateSerializer(serializers.Serializer):
    step_key = serializers.SlugField(max_length=120)
    is_completed = serializers.BooleanField(default=True)
    payload = serializers.JSONField(required=False, default=dict)


class EmergencyDispatchPayloadSerializer(serializers.Serializer):
    payload = serializers.JSONField(required=False, default=dict)
    metadata = serializers.JSONField(required=False, default=dict)
    merge = serializers.BooleanField(default=True)


class EmergencyDispatchTrackingSerializer(serializers.Serializer):
    latitude = serializers.FloatField(required=False, allow_null=True)
    longitude = serializers.FloatField(required=False, allow_null=True)
    eta_minutes = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    ambulance_reference = serializers.CharField(max_length=120, required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=EmergencyDispatchStatus.choices, required=False)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    payload = serializers.JSONField(required=False, default=dict)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("At least one tracking field is required.")
        return attrs


class EmergencyDispatchEndSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[
            (EmergencyDispatchStatus.RESOLVED, "Resolved"),
            (EmergencyDispatchStatus.CANCELLED, "Cancelled"),
        ],
        default=EmergencyDispatchStatus.RESOLVED,
    )
    summary = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    metadata = serializers.JSONField(required=False, default=dict)


PHARMACY_FULFILLMENT_STEP_CHOICES = (
    ("verify_prescription", "Verify prescription"),
    ("validate_inventory", "Validate inventory"),
    ("confirm_delivery", "Confirm delivery"),
    ("fulfillment_tracking", "Fulfillment tracking"),
)


class PharmacyFulfillmentSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PharmacyFulfillmentSession
        fields = [
            "id",
            "workflow_session",
            "engine_session",
            "institution",
            "service",
            "user",
            "status",
            "cart_items",
            "delivery_mode",
            "payment_reference",
            "fulfillment_reference",
            "current_eta_minutes",
            "step_state",
            "payload",
            "tracking_events",
            "metadata",
            "started_at",
            "ready_at",
            "delivered_at",
            "ended_at",
            "last_tracking_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "workflow_session",
            "engine_session",
            "institution",
            "service",
            "user",
            "status",
            "step_state",
            "tracking_events",
            "started_at",
            "ready_at",
            "delivered_at",
            "ended_at",
            "last_tracking_at",
            "created_at",
            "updated_at",
        ]


class PharmacyFulfillmentStartSerializer(serializers.Serializer):
    workflow_session_id = serializers.UUIDField()
    cart_items = serializers.ListField(child=serializers.JSONField(), required=False, default=list)
    payload = serializers.JSONField(required=False, default=dict)
    metadata = serializers.JSONField(required=False, default=dict)


class PharmacyFulfillmentStepUpdateSerializer(serializers.Serializer):
    step_key = serializers.SlugField(max_length=120)
    is_completed = serializers.BooleanField(default=True)
    payload = serializers.JSONField(required=False, default=dict)


class PharmacyFulfillmentPayloadSerializer(serializers.Serializer):
    payload = serializers.JSONField(required=False, default=dict)
    metadata = serializers.JSONField(required=False, default=dict)
    merge = serializers.BooleanField(default=True)


class PharmacyFulfillmentTrackingSerializer(serializers.Serializer):
    eta_minutes = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    delivery_mode = serializers.CharField(max_length=32, required=False, allow_blank=True)
    payment_reference = serializers.CharField(max_length=120, required=False, allow_blank=True)
    fulfillment_reference = serializers.CharField(max_length=120, required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=PharmacyFulfillmentStatus.choices, required=False)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    payload = serializers.JSONField(required=False, default=dict)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("At least one tracking field is required.")
        return attrs


class PharmacyFulfillmentEndSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[
            (PharmacyFulfillmentStatus.COMPLETED, "Completed"),
            (PharmacyFulfillmentStatus.CANCELLED, "Cancelled"),
        ],
        default=PharmacyFulfillmentStatus.COMPLETED,
    )
    summary = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    metadata = serializers.JSONField(required=False, default=dict)


PAYMENT_BILLING_STEP_CHOICES = (
    ("review_charges", "Review charges"),
    ("select_payment_method", "Select payment method"),
    ("authorize_payment", "Authorize payment"),
    ("issue_receipt", "Issue receipt"),
)


class PaymentBillingSessionSerializer(serializers.ModelSerializer):
    total_amount_kisc = serializers.SerializerMethodField()
    insurance_coverage_kisc = serializers.SerializerMethodField()
    payable_amount_kisc = serializers.SerializerMethodField()
    amount_paid_kisc = serializers.SerializerMethodField()

    class Meta:
        model = PaymentBillingSession
        fields = [
            "id",
            "workflow_session",
            "engine_session",
            "institution",
            "service",
            "user",
            "status",
            "total_amount_micro",
            "total_amount_kisc",
            "insurance_coverage_micro",
            "insurance_coverage_kisc",
            "payable_amount_micro",
            "payable_amount_kisc",
            "amount_paid_micro",
            "amount_paid_kisc",
            "payment_provider",
            "payment_reference",
            "invoice_number",
            "step_state",
            "payload",
            "metadata",
            "started_at",
            "paid_at",
            "ended_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "workflow_session",
            "engine_session",
            "institution",
            "service",
            "user",
            "status",
            "step_state",
            "started_at",
            "paid_at",
            "ended_at",
            "created_at",
            "updated_at",
        ]

    def get_total_amount_kisc(self, obj):
        return _micro_to_kisc_text(getattr(obj, "total_amount_micro", 0))

    def get_insurance_coverage_kisc(self, obj):
        return _micro_to_kisc_text(getattr(obj, "insurance_coverage_micro", 0))

    def get_payable_amount_kisc(self, obj):
        return _micro_to_kisc_text(getattr(obj, "payable_amount_micro", 0))

    def get_amount_paid_kisc(self, obj):
        return _micro_to_kisc_text(getattr(obj, "amount_paid_micro", 0))


class PaymentBillingStartSerializer(serializers.Serializer):
    workflow_session_id = serializers.UUIDField()
    total_amount_micro = serializers.IntegerField(required=False, min_value=0)
    total_amount_kisc = serializers.DecimalField(max_digits=20, decimal_places=5, required=False, min_value=0)
    insurance_coverage_micro = serializers.IntegerField(required=False, min_value=0)
    insurance_coverage_kisc = serializers.DecimalField(max_digits=20, decimal_places=5, required=False, min_value=0)
    payable_amount_micro = serializers.IntegerField(required=False, min_value=0)
    payable_amount_kisc = serializers.DecimalField(max_digits=20, decimal_places=5, required=False, min_value=0)
    payment_provider = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")
    payload = serializers.JSONField(required=False, default=dict)
    metadata = serializers.JSONField(required=False, default=dict)

    def validate(self, attrs):
        # Optional KISC aliases are normalized to micro for canonical storage.
        if "total_amount_kisc" in attrs and "total_amount_micro" not in attrs:
            attrs["total_amount_micro"] = _kisc_to_micro_or_none(attrs.get("total_amount_kisc"))
        if "insurance_coverage_kisc" in attrs and "insurance_coverage_micro" not in attrs:
            attrs["insurance_coverage_micro"] = _kisc_to_micro_or_none(attrs.get("insurance_coverage_kisc"))
        if "payable_amount_kisc" in attrs and "payable_amount_micro" not in attrs:
            attrs["payable_amount_micro"] = _kisc_to_micro_or_none(attrs.get("payable_amount_kisc"))
        provider = str(attrs.get("payment_provider") or "").strip().lower()
        if provider and provider != "kis_wallet":
            raise serializers.ValidationError({"payment_provider": "Only kis_wallet is supported for health billing."})
        attrs["payment_provider"] = "kis_wallet"
        return attrs


class PaymentBillingStepUpdateSerializer(serializers.Serializer):
    step_key = serializers.SlugField(max_length=120)
    is_completed = serializers.BooleanField(default=True)
    payload = serializers.JSONField(required=False, default=dict)


class PaymentBillingPayloadSerializer(serializers.Serializer):
    payload = serializers.JSONField(required=False, default=dict)
    metadata = serializers.JSONField(required=False, default=dict)
    merge = serializers.BooleanField(default=True)


class PaymentBillingEndSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[
            (PaymentBillingStatus.COMPLETED, "Completed"),
            (PaymentBillingStatus.FAILED, "Failed"),
            (PaymentBillingStatus.CANCELLED, "Cancelled"),
        ],
        default=PaymentBillingStatus.COMPLETED,
    )
    summary = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    metadata = serializers.JSONField(required=False, default=dict)


HOME_LOGISTICS_STEP_CHOICES = (
    ("select_logistics_mode", "Select logistics mode"),
    ("schedule_window", "Schedule window"),
    ("assign_route", "Assign route"),
    ("track_eta", "Track ETA"),
)


class HomeLogisticsSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = HomeLogisticsSession
        fields = [
            "id",
            "workflow_session",
            "engine_session",
            "institution",
            "service",
            "user",
            "logistics_code",
            "status",
            "task_type",
            "route_reference",
            "assignee_name",
            "current_eta_minutes",
            "location_latitude",
            "location_longitude",
            "scheduled_window_start",
            "scheduled_window_end",
            "step_state",
            "payload",
            "tracking_events",
            "metadata",
            "started_at",
            "dispatched_at",
            "arrived_at",
            "ended_at",
            "last_tracking_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "workflow_session",
            "engine_session",
            "institution",
            "service",
            "user",
            "logistics_code",
            "status",
            "step_state",
            "tracking_events",
            "started_at",
            "dispatched_at",
            "arrived_at",
            "ended_at",
            "last_tracking_at",
            "created_at",
            "updated_at",
        ]


class HomeLogisticsStartSerializer(serializers.Serializer):
    workflow_session_id = serializers.UUIDField()
    logistics_code = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    task_type = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")
    payload = serializers.JSONField(required=False, default=dict)
    metadata = serializers.JSONField(required=False, default=dict)


class HomeLogisticsStepUpdateSerializer(serializers.Serializer):
    step_key = serializers.SlugField(max_length=120)
    is_completed = serializers.BooleanField(default=True)
    payload = serializers.JSONField(required=False, default=dict)


class HomeLogisticsPayloadSerializer(serializers.Serializer):
    payload = serializers.JSONField(required=False, default=dict)
    metadata = serializers.JSONField(required=False, default=dict)
    merge = serializers.BooleanField(default=True)


class HomeLogisticsTrackingSerializer(serializers.Serializer):
    latitude = serializers.FloatField(required=False, allow_null=True)
    longitude = serializers.FloatField(required=False, allow_null=True)
    eta_minutes = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    route_reference = serializers.CharField(max_length=120, required=False, allow_blank=True)
    assignee_name = serializers.CharField(max_length=120, required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=HomeLogisticsStatus.choices, required=False)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    payload = serializers.JSONField(required=False, default=dict)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("At least one tracking field is required.")
        return attrs


class HomeLogisticsEndSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[
            (HomeLogisticsStatus.COMPLETED, "Completed"),
            (HomeLogisticsStatus.CANCELLED, "Cancelled"),
        ],
        default=HomeLogisticsStatus.COMPLETED,
    )
    summary = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    metadata = serializers.JSONField(required=False, default=dict)


WELLNESS_PROGRAM_STEP_CHOICES = (
    ("enroll_program", "Enroll program"),
    ("set_goals", "Set goals"),
    ("track_habits", "Track habits"),
    ("review_progress", "Review progress"),
)


class WellnessProgramSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WellnessProgramSession
        fields = [
            "id",
            "workflow_session",
            "engine_session",
            "institution",
            "service",
            "user",
            "status",
            "program_name",
            "goal_payload",
            "habit_payload",
            "current_streak",
            "completion_percent",
            "step_state",
            "payload",
            "activity_events",
            "metadata",
            "started_at",
            "paused_at",
            "ended_at",
            "last_activity_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "workflow_session",
            "engine_session",
            "institution",
            "service",
            "user",
            "status",
            "step_state",
            "activity_events",
            "started_at",
            "paused_at",
            "ended_at",
            "last_activity_at",
            "created_at",
            "updated_at",
        ]


class WellnessProgramStartSerializer(serializers.Serializer):
    workflow_session_id = serializers.UUIDField()
    program_name = serializers.CharField(max_length=160, required=False, allow_blank=True, default="")
    payload = serializers.JSONField(required=False, default=dict)
    metadata = serializers.JSONField(required=False, default=dict)


class WellnessProgramStepUpdateSerializer(serializers.Serializer):
    step_key = serializers.SlugField(max_length=120)
    is_completed = serializers.BooleanField(default=True)
    payload = serializers.JSONField(required=False, default=dict)


class WellnessProgramPayloadSerializer(serializers.Serializer):
    payload = serializers.JSONField(required=False, default=dict)
    metadata = serializers.JSONField(required=False, default=dict)
    merge = serializers.BooleanField(default=True)


class WellnessProgramActivitySerializer(serializers.Serializer):
    event_type = serializers.CharField(max_length=64, required=False, allow_blank=True, default="progress")
    status = serializers.ChoiceField(choices=WellnessProgramStatus.choices, required=False)
    streak_delta = serializers.IntegerField(required=False)
    completion_percent = serializers.IntegerField(required=False, min_value=0, max_value=100)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    payload = serializers.JSONField(required=False, default=dict)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("At least one activity field is required.")
        return attrs


class WellnessProgramEndSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[
            (WellnessProgramStatus.COMPLETED, "Completed"),
            (WellnessProgramStatus.CANCELLED, "Cancelled"),
        ],
        default=WellnessProgramStatus.COMPLETED,
    )
    summary = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    metadata = serializers.JSONField(required=False, default=dict)


NOTIFICATION_REMINDER_STEP_CHOICES = (
    ("select_channels", "Select channels"),
    ("configure_rules", "Configure rules"),
    ("schedule_reminders", "Schedule reminders"),
    ("confirm_delivery", "Confirm delivery"),
)


class NotificationReminderSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationReminderSession
        fields = [
            "id",
            "workflow_session",
            "engine_session",
            "institution",
            "service",
            "user",
            "status",
            "channel_config",
            "rule_config",
            "reminder_timezone",
            "next_run_at",
            "last_sent_at",
            "sent_count",
            "failed_count",
            "step_state",
            "payload",
            "delivery_events",
            "metadata",
            "started_at",
            "ended_at",
            "last_delivery_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "workflow_session",
            "engine_session",
            "institution",
            "service",
            "user",
            "status",
            "step_state",
            "delivery_events",
            "started_at",
            "ended_at",
            "last_delivery_at",
            "created_at",
            "updated_at",
        ]

class NotificationReminderStartSerializer(serializers.Serializer):
    workflow_session_id = serializers.UUIDField()
    reminder_timezone = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    payload = serializers.JSONField(required=False, default=dict)
    metadata = serializers.JSONField(required=False, default=dict)


class NotificationReminderStepUpdateSerializer(serializers.Serializer):
    step_key = serializers.SlugField(max_length=120)
    is_completed = serializers.BooleanField(default=True)
    payload = serializers.JSONField(required=False, default=dict)


class NotificationReminderPayloadSerializer(serializers.Serializer):
    payload = serializers.JSONField(required=False, default=dict)
    metadata = serializers.JSONField(required=False, default=dict)
    merge = serializers.BooleanField(default=True)


class NotificationReminderDeliverySerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=NotificationReminderStatus.choices, required=False)
    next_run_at = serializers.DateTimeField(required=False, allow_null=True)
    sent = serializers.BooleanField(required=False)
    failed = serializers.BooleanField(required=False)
    channel = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    payload = serializers.JSONField(required=False, default=dict)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("At least one delivery field is required.")
        return attrs


class NotificationReminderEndSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[
            (NotificationReminderStatus.COMPLETED, "Completed"),
            (NotificationReminderStatus.DISABLED, "Disabled"),
            (NotificationReminderStatus.CANCELLED, "Cancelled"),
        ],
        default=NotificationReminderStatus.COMPLETED,
    )
    summary = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    metadata = serializers.JSONField(required=False, default=dict)
