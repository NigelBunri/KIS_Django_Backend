from django.contrib import admin

from .models import Website, WebsitePage


@admin.register(Website)
class WebsiteAdmin(admin.ModelAdmin):
    list_display = ("id", "slug", "name", "owner_type", "owner_id", "status", "seeded_from_legacy", "updated_at")
    list_filter = ("owner_type", "status", "seeded_from_legacy")
    search_fields = ("slug", "name", "owner_id")


@admin.register(WebsitePage)
class WebsitePageAdmin(admin.ModelAdmin):
    list_display = ("id", "website", "slug", "title", "is_home", "status", "sort_order", "updated_at")
    list_filter = ("status", "is_home")
    search_fields = ("title", "slug", "website__slug")
