# apps/family/admin.py
from django.contrib import admin

from .models import (
    FamilyAccount,
    FamilyMember,
    ParentalControl,
    FamilyEvent,
    FamilyPhotoAlbum,
    FamilyPhoto,
    MemorialPage,
    MilestoneRecord,
    TimeCapsule,
    FamilyPrayerItem,
    FamilyNoticeBoard,
    GriefSupportGroup,
    GriefGroupMembership,
)


@admin.register(FamilyAccount)
class FamilyAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "admin_user", "invite_code", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "invite_code", "admin_user__email")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(FamilyMember)
class FamilyMemberAdmin(admin.ModelAdmin):
    list_display = ("family", "user", "role", "nickname", "is_admin", "is_minor")
    list_filter = ("role", "is_admin", "is_minor")
    search_fields = ("nickname", "user__email", "family__name")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(ParentalControl)
class ParentalControlAdmin(admin.ModelAdmin):
    list_display = (
        "family_member",
        "content_filter_level",
        "screen_time_minutes_per_day",
        "location_sharing",
        "sos_enabled",
        "activity_report_frequency",
    )
    list_filter = ("content_filter_level", "location_sharing", "sos_enabled")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(FamilyEvent)
class FamilyEventAdmin(admin.ModelAdmin):
    list_display = ("title", "family", "type", "event_date", "created_by")
    list_filter = ("type",)
    search_fields = ("title", "notes")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(FamilyPhotoAlbum)
class FamilyPhotoAlbumAdmin(admin.ModelAdmin):
    list_display = ("name", "family", "created_by", "created_at")
    search_fields = ("name",)
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(FamilyPhoto)
class FamilyPhotoAdmin(admin.ModelAdmin):
    list_display = ("album", "uploader", "taken_at", "created_at")
    search_fields = ("caption",)
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(MemorialPage)
class MemorialPageAdmin(admin.ModelAdmin):
    list_display = ("name", "family", "birth_date", "death_date", "is_public")
    list_filter = ("is_public",)
    search_fields = ("name", "tribute")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(MilestoneRecord)
class MilestoneRecordAdmin(admin.ModelAdmin):
    list_display = ("title", "family_member", "type", "milestone_date")
    list_filter = ("type",)
    search_fields = ("title", "description")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(TimeCapsule)
class TimeCapsuleAdmin(admin.ModelAdmin):
    list_display = ("title", "family", "unlock_date", "is_locked", "creator")
    list_filter = ("is_locked",)
    search_fields = ("title",)
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(FamilyPrayerItem)
class FamilyPrayerItemAdmin(admin.ModelAdmin):
    list_display = ("family", "user", "is_answered", "created_at")
    list_filter = ("is_answered",)
    search_fields = ("text",)
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(FamilyNoticeBoard)
class FamilyNoticeBoardAdmin(admin.ModelAdmin):
    list_display = ("title", "family", "posted_by", "is_pinned", "expires_at")
    list_filter = ("is_pinned",)
    search_fields = ("title", "content")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(GriefSupportGroup)
class GriefSupportGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "facilitator", "member_count", "is_active")
    list_filter = ("type", "is_active", "is_anonymous_allowed")
    search_fields = ("name", "description")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(GriefGroupMembership)
class GriefGroupMembershipAdmin(admin.ModelAdmin):
    list_display = ("group", "user", "is_anonymous", "join_date")
    list_filter = ("is_anonymous",)
    search_fields = ("user__email", "group__name")
    readonly_fields = ("id", "join_date", "created_at", "updated_at")
