from django.contrib import admin

from .models import (
    VerificationAuditEvent,
    VerificationBadge,
    VerificationCase,
    VerificationCheck,
    VerificationSubject,
)


@admin.register(VerificationSubject)
class VerificationSubjectAdmin(admin.ModelAdmin):
    list_display = ("subject_type", "subject_id", "display_name", "current_status", "current_level", "owner", "last_verified_at")
    list_filter = ("subject_type", "current_status", "current_level", "country")
    search_fields = ("subject_id", "display_name", "owner__phone", "owner__email", "owner__username")
    readonly_fields = ("id", "created_at", "updated_at")


class VerificationCheckInline(admin.TabularInline):
    model = VerificationCheck
    extra = 0
    readonly_fields = ("id", "created_at", "updated_at")
    fields = ("check_type", "status", "provider", "confidence", "result_code", "checked_at", "expires_at")


@admin.register(VerificationCase)
class VerificationCaseAdmin(admin.ModelAdmin):
    list_display = ("subject", "level", "status", "provider", "risk_score", "requested_by", "reviewed_by", "submitted_at", "reviewed_at")
    list_filter = ("status", "level", "provider", "subject__subject_type", "expires_at")
    search_fields = ("subject__subject_id", "subject__display_name", "provider_applicant_id", "provider_case_id")
    readonly_fields = ("id", "created_at", "updated_at")
    date_hierarchy = "created_at"
    inlines = [VerificationCheckInline]


@admin.register(VerificationCheck)
class VerificationCheckAdmin(admin.ModelAdmin):
    list_display = ("case", "check_type", "status", "provider", "confidence", "result_code", "checked_at", "expires_at")
    list_filter = ("status", "check_type", "provider")
    search_fields = ("case__subject__subject_id", "provider_check_id", "result_code", "result_summary")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(VerificationBadge)
class VerificationBadgeAdmin(admin.ModelAdmin):
    list_display = ("subject", "code", "label", "level", "status", "public", "issued_at", "expires_at", "revoked_at")
    list_filter = ("status", "public", "code", "level", "expires_at")
    search_fields = ("subject__subject_id", "subject__display_name", "code", "label")
    readonly_fields = ("id", "created_at", "updated_at")
    date_hierarchy = "issued_at"


@admin.register(VerificationAuditEvent)
class VerificationAuditEventAdmin(admin.ModelAdmin):
    list_display = ("action", "subject", "case", "actor", "provider", "created_at")
    list_filter = ("action", "provider", "subject__subject_type", "created_at")
    search_fields = ("subject__subject_id", "subject__display_name", "case__provider_case_id", "actor__phone", "actor__email")
    readonly_fields = ("id", "created_at", "updated_at")
    date_hierarchy = "created_at"
