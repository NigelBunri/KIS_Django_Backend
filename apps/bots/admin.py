# apps/bots/admin.py
"""KIS superadmin integration (section 17) — Phase 1 ships via Django
admin (already staff-only, already audited/permissioned) rather than a
new mobile admin console screen. Deliberately read-heavy: a superadmin
can inspect, suspend and revoke, but every mutating action here still
goes through apps.bots.services so it's logged to BotActivityLog like
any other change — no action bypasses the audit trail."""
from django.contrib import admin
from django.utils import timezone

from . import services
from .models import (
    Bot,
    BotActivityLog,
    BotAIConnection,
    BotApprovalRequest,
    BotCredential,
    BotResourceGrant,
    BotStatus,
    BotWebhookDelivery,
    BotWebhookSubscription,
)


@admin.action(description="Suspend selected bots")
def suspend_bots(modeladmin, request, queryset):
    for bot in queryset:
        services.set_status_admin(bot, request.user, BotStatus.SUSPENDED, reason="Suspended via admin console.")


@admin.action(description="Reinstate selected bots (suspended → active)")
def reinstate_bots(modeladmin, request, queryset):
    for bot in queryset.filter(status=BotStatus.SUSPENDED):
        services.set_status_admin(bot, request.user, BotStatus.ACTIVE, reason="Reinstated via admin console.")


@admin.action(description="Revoke credentials for selected bots")
def revoke_credentials(modeladmin, request, queryset):
    for bot in queryset:
        BotCredential.objects.filter(bot=bot, is_active=True).update(is_active=False, revoked_at=timezone.now())


@admin.register(Bot)
class BotAdmin(admin.ModelAdmin):
    list_display = ("name", "handle", "owner", "partner", "kind", "status", "created_at")
    list_filter = ("status", "kind")
    search_fields = ("name", "handle", "owner__email", "owner__phone")
    readonly_fields = ("id", "created_at", "updated_at", "deleted_at")
    actions = [suspend_bots, reinstate_bots, revoke_credentials]


@admin.register(BotCredential)
class BotCredentialAdmin(admin.ModelAdmin):
    list_display = ("bot", "client_id", "is_active", "last_used_at", "created_at")
    readonly_fields = ("client_id", "client_secret_hash", "created_at")
    search_fields = ("bot__name", "client_id")


@admin.register(BotResourceGrant)
class BotResourceGrantAdmin(admin.ModelAdmin):
    list_display = ("bot", "resource_type", "resource_id", "is_active", "created_at")
    list_filter = ("resource_type", "is_active")
    search_fields = ("bot__name", "resource_id")


@admin.register(BotWebhookSubscription)
class BotWebhookSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("bot", "url", "is_active", "last_sent_at", "last_error")
    readonly_fields = ("secret",)


@admin.register(BotWebhookDelivery)
class BotWebhookDeliveryAdmin(admin.ModelAdmin):
    list_display = ("subscription", "event", "status", "attempt_count", "response_code", "created_at")
    list_filter = ("status", "event")


@admin.register(BotActivityLog)
class BotActivityLogAdmin(admin.ModelAdmin):
    list_display = ("bot", "action", "resource_type", "actor", "created_at")
    list_filter = ("action",)
    search_fields = ("bot__name",)


@admin.register(BotApprovalRequest)
class BotApprovalRequestAdmin(admin.ModelAdmin):
    list_display = ("bot", "action", "status", "decided_by", "created_at")
    list_filter = ("status",)


@admin.register(BotAIConnection)
class BotAIConnectionAdmin(admin.ModelAdmin):
    list_display = ("bot", "provider", "model", "is_active")
    readonly_fields = ("api_key_encrypted",)
