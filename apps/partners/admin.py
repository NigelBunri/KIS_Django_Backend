# apps/partners/admin.py
from django.contrib import admin

from apps.partners.models import (
    Partner,
    PartnerPost,
    PartnerInvite,
    PartnerOnboardingProgress,
    PartnerModerationAction,
    PartnerOrganizationApp,
    PartnerServerCategory,
    PartnerChannelPermissionOverwrite,
)


@admin.register(Partner)
class PartnerAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "owner",
        "is_active",
        "created_at",
    )
    list_filter = ("is_active",)
    search_fields = ("name", "slug", "owner__username", "owner__email")
    raw_id_fields = ("owner", "main_conversation")


@admin.register(PartnerPost)
class PartnerPostAdmin(admin.ModelAdmin):
    list_display = ("partner", "author", "created_at", "is_deleted")
    list_filter = ("partner", "is_deleted")
    search_fields = ("partner__name", "author__display_name", "text")
    raw_id_fields = ("partner", "author")


@admin.register(PartnerOrganizationApp)
class PartnerOrganizationAppAdmin(admin.ModelAdmin):
    list_display = (
        "partner",
        "name",
        "type",
        "is_active",
        "order",
        "created_at",
    )
    list_filter = ("partner", "type", "is_active")
    search_fields = ("partner__name", "name", "slug", "module")
    raw_id_fields = ("partner",)
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "partner",
                    "name",
                    "slug",
                    "type",
                    "description",
                    "link",
                    "module",
                    "icon",
                    "badge_label",
                    "visible_to",
                    "group",
                    "order",
                    "is_active",
                )
            },
        ),
        ("Advanced", {"classes": ("collapse",), "fields": ("config", "metadata", "created_at", "updated_at")}),
    )


@admin.register(PartnerServerCategory)
class PartnerServerCategoryAdmin(admin.ModelAdmin):
    list_display = ("partner", "name", "slug", "order", "is_private", "created_at")
    list_filter = ("partner", "is_private")
    search_fields = ("partner__name", "name", "slug")
    raw_id_fields = ("partner",)


@admin.register(PartnerChannelPermissionOverwrite)
class PartnerChannelPermissionOverwriteAdmin(admin.ModelAdmin):
    list_display = ("partner", "channel", "subject_type", "role", "user", "created_at")
    list_filter = ("partner", "subject_type")
    search_fields = ("partner__name", "channel__name", "role__name", "user__username", "user__email")
    raw_id_fields = ("partner", "channel", "role", "user")


@admin.register(PartnerInvite)
class PartnerInviteAdmin(admin.ModelAdmin):
    list_display = ("partner", "code", "label", "created_by", "use_count", "max_uses", "is_active", "expires_at")
    list_filter = ("partner", "is_active")
    search_fields = ("partner__name", "code", "label", "created_by__username", "created_by__email")
    raw_id_fields = ("partner", "created_by")


@admin.register(PartnerOnboardingProgress)
class PartnerOnboardingProgressAdmin(admin.ModelAdmin):
    list_display = ("partner", "user", "invite", "rules_accepted_at", "completed_at", "updated_at")
    list_filter = ("partner",)
    search_fields = ("partner__name", "user__username", "user__email")
    raw_id_fields = ("partner", "user", "invite")


@admin.register(PartnerModerationAction)
class PartnerModerationActionAdmin(admin.ModelAdmin):
    list_display = ("partner", "user", "action_type", "actor", "expires_at", "created_at")
    list_filter = ("partner", "action_type")
    search_fields = ("partner__name", "user__username", "user__email", "actor__username", "actor__email")
    raw_id_fields = ("partner", "user", "actor", "membership")
