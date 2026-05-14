from django.contrib import admin

from .models import AuditLog, Flag, UserBlock


@admin.register(Flag)
class FlagAdmin(admin.ModelAdmin):
    list_display = ("id", "target_type", "target_id", "severity", "status", "reporter_id", "created_at")
    list_filter = ("target_type", "severity", "status", "source")
    search_fields = ("id", "target_id", "reason", "reporter_id")
    readonly_fields = ("id", "created_at", "updated_at", "reviewed_at", "resolved_at")


@admin.register(AuditLog)
class ModerationAuditLogAdmin(admin.ModelAdmin):
    list_display = ("id", "action", "target_type", "target_id", "actor_id", "created_at")
    list_filter = ("action", "target_type")
    search_fields = ("id", "action", "target_id", "actor_id")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(UserBlock)
class UserBlockAdmin(admin.ModelAdmin):
    list_display = ("id", "blocker", "blocked", "reason", "created_at")
    search_fields = ("id", "blocker__username", "blocker__phone", "blocked__username", "blocked__phone", "reason")
    readonly_fields = ("id", "created_at", "updated_at")
