from django.contrib import admin

from .models import (
    AdmissionBedSession,
    ClinicalEngineSession,
    EmergencyDispatchSession,
    EngineContentBlock,
    EngineRegistry,
    EngineSession,
    EngineStepDefinition,
    EngineStepProgress,
    HealthInstitution,
    HealthInstitutionMembership,
    HomeLogisticsSession,
    HealthOpsAuditLog,
    HealthService,
    InstitutionEngineManagedItem,
    NotificationReminderSession,
    PaymentBillingSession,
    PharmacyFulfillmentSession,
    SecureMessage,
    SecureMessagingSession,
    ServiceEngineMap,
    ServiceWorkflowSession,
    VideoEngineItem,
    VideoEngineItemComment,
    VideoEngineItemLike,
    VideoEngineItemProgress,
    VideoConsultationSession,
    WellnessProgramSession,
)


@admin.register(HealthInstitution)
class HealthInstitutionAdmin(admin.ModelAdmin):
    list_display = ("name", "institution_type", "owner", "is_active", "created_at")
    search_fields = ("name", "slug")
    list_filter = ("institution_type", "is_active")


@admin.register(HealthInstitutionMembership)
class HealthInstitutionMembershipAdmin(admin.ModelAdmin):
    list_display = ("institution", "user", "role", "is_active", "created_at")
    search_fields = ("institution__name", "user__phone", "user__email")
    list_filter = ("role", "is_active")


@admin.register(HealthService)
class HealthServiceAdmin(admin.ModelAdmin):
    list_display = ("name", "institution", "is_active", "base_cost_micro", "requires_assessment")
    search_fields = ("name", "institution__name")
    list_filter = ("is_active", "requires_assessment")


@admin.register(EngineRegistry)
class EngineRegistryAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_fixed", "is_active", "schema_version")
    search_fields = ("code", "name")
    list_filter = ("is_fixed", "is_active")


@admin.register(ServiceEngineMap)
class ServiceEngineMapAdmin(admin.ModelAdmin):
    list_display = ("service", "engine", "execution_order", "cost_micro", "is_required", "access_window_days", "completion_mode")
    search_fields = ("service__name", "engine__name", "engine__code")


@admin.register(EngineStepDefinition)
class EngineStepDefinitionAdmin(admin.ModelAdmin):
    list_display = ("engine", "step_key", "title", "step_order", "is_required")
    search_fields = ("engine__code", "step_key", "title")


@admin.register(ServiceWorkflowSession)
class ServiceWorkflowSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "institution", "service", "user", "status", "progress_percent", "is_locked_by_payment")
    search_fields = ("service__name", "institution__name", "user__phone", "user__email")
    list_filter = ("status", "is_locked_by_payment")


@admin.register(EngineSession)
class EngineSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "workflow_session", "engine_map", "is_unlocked", "is_completed", "is_expired", "progress_percent", "expires_at")
    list_filter = ("is_unlocked", "is_completed", "is_paused", "is_expired")


@admin.register(EngineStepProgress)
class EngineStepProgressAdmin(admin.ModelAdmin):
    list_display = ("engine_session", "step_key", "is_completed", "content_position", "updated_at")
    search_fields = ("step_key",)
    list_filter = ("is_completed",)


@admin.register(VideoConsultationSession)
class VideoConsultationSessionAdmin(admin.ModelAdmin):
    list_display = ("room_code", "service", "institution", "user", "status", "token_expires_at")
    search_fields = ("room_code", "service__name", "institution__name", "user__phone", "user__email")
    list_filter = ("status", "recording_enabled", "waiting_room_enabled")


@admin.register(VideoEngineItem)
class VideoEngineItemAdmin(admin.ModelAdmin):
    list_display = ("title", "engine_map", "sort_order", "is_active", "duration_seconds", "updated_at")
    search_fields = ("title", "engine_map__service__name", "engine_map__engine__code")
    list_filter = ("is_active",)


@admin.register(VideoEngineItemProgress)
class VideoEngineItemProgressAdmin(admin.ModelAdmin):
    list_display = ("item", "engine_session", "user", "watched_seconds", "is_completed", "last_watched_at")
    search_fields = ("item__title", "user__phone", "user__email")
    list_filter = ("is_completed",)


@admin.register(VideoEngineItemLike)
class VideoEngineItemLikeAdmin(admin.ModelAdmin):
    list_display = ("item", "engine_session", "user", "created_at")
    search_fields = ("item__title", "user__phone", "user__email")


@admin.register(VideoEngineItemComment)
class VideoEngineItemCommentAdmin(admin.ModelAdmin):
    list_display = ("item", "engine_session", "user", "is_deleted", "created_at")
    search_fields = ("item__title", "user__phone", "user__email", "body")
    list_filter = ("is_deleted",)


@admin.register(SecureMessagingSession)
class SecureMessagingSessionAdmin(admin.ModelAdmin):
    list_display = ("thread_code", "service", "institution", "user", "status", "last_message_at")
    search_fields = ("thread_code", "service__name", "institution__name", "user__phone", "user__email")
    list_filter = ("status",)


@admin.register(SecureMessage)
class SecureMessageAdmin(admin.ModelAdmin):
    list_display = ("session", "sender", "message_type", "is_read", "delivered_at")
    search_fields = ("session__thread_code", "sender__phone", "sender__email", "body")
    list_filter = ("message_type", "is_read")


@admin.register(ClinicalEngineSession)
class ClinicalEngineSessionAdmin(admin.ModelAdmin):
    list_display = ("engine_code", "service", "institution", "user", "status", "updated_at")
    search_fields = ("service__name", "institution__name", "user__phone", "user__email", "engine_code")
    list_filter = ("engine_code", "status")


@admin.register(AdmissionBedSession)
class AdmissionBedSessionAdmin(admin.ModelAdmin):
    list_display = ("service", "institution", "user", "status", "ward_name", "bed_code", "updated_at")
    search_fields = ("service__name", "institution__name", "user__phone", "user__email", "ward_name", "bed_code")
    list_filter = ("status", "requires_isolation", "requires_icu")


@admin.register(EmergencyDispatchSession)
class EmergencyDispatchSessionAdmin(admin.ModelAdmin):
    list_display = ("dispatch_code", "service", "institution", "user", "status", "current_eta_minutes", "updated_at")
    search_fields = ("dispatch_code", "service__name", "institution__name", "user__phone", "user__email")
    list_filter = ("status",)


@admin.register(PharmacyFulfillmentSession)
class PharmacyFulfillmentSessionAdmin(admin.ModelAdmin):
    list_display = ("service", "institution", "user", "status", "delivery_mode", "current_eta_minutes", "updated_at")
    search_fields = (
        "service__name",
        "institution__name",
        "user__phone",
        "user__email",
        "payment_reference",
        "fulfillment_reference",
    )
    list_filter = ("status", "delivery_mode")


@admin.register(PaymentBillingSession)
class PaymentBillingSessionAdmin(admin.ModelAdmin):
    list_display = ("service", "institution", "user", "status", "payable_amount_micro", "amount_paid_micro", "updated_at")
    search_fields = ("service__name", "institution__name", "user__phone", "user__email", "payment_reference", "invoice_number")
    list_filter = ("status", "payment_provider")


@admin.register(HomeLogisticsSession)
class HomeLogisticsSessionAdmin(admin.ModelAdmin):
    list_display = ("logistics_code", "service", "institution", "user", "status", "current_eta_minutes", "updated_at")
    search_fields = (
        "logistics_code",
        "service__name",
        "institution__name",
        "user__phone",
        "user__email",
        "route_reference",
    )
    list_filter = ("status", "task_type")


@admin.register(WellnessProgramSession)
class WellnessProgramSessionAdmin(admin.ModelAdmin):
    list_display = ("service", "institution", "user", "status", "program_name", "current_streak", "updated_at")
    search_fields = ("service__name", "institution__name", "user__phone", "user__email", "program_name")
    list_filter = ("status",)


@admin.register(NotificationReminderSession)
class NotificationReminderSessionAdmin(admin.ModelAdmin):
    list_display = ("service", "institution", "user", "status", "next_run_at", "sent_count", "failed_count", "updated_at")
    search_fields = ("service__name", "institution__name", "user__phone", "user__email")
    list_filter = ("status", "reminder_timezone")


@admin.register(EngineContentBlock)
class EngineContentBlockAdmin(admin.ModelAdmin):
    list_display = ("engine", "title", "block_type", "order", "is_active", "version")
    search_fields = ("title", "engine__name", "engine__code")
    list_filter = ("block_type", "is_active")


@admin.register(InstitutionEngineManagedItem)
class InstitutionEngineManagedItemAdmin(admin.ModelAdmin):
    list_display = ("institution", "engine_key", "item_kind", "name", "status", "sort_order", "is_active", "updated_at")
    search_fields = ("institution__name", "engine_key", "item_kind", "name")
    list_filter = ("engine_key", "item_kind", "is_active", "status")


@admin.register(HealthOpsAuditLog)
class HealthOpsAuditLogAdmin(admin.ModelAdmin):
    list_display = ("action", "actor", "institution", "created_at")
    search_fields = ("action", "actor__phone", "actor__email", "institution__name")
