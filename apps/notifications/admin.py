
from django.contrib import admin
from . import models


@admin.register(models.Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("type", "user_id", "channel", "priority", "is_read", "created_at")
    list_filter = ("channel", "priority")
    search_fields = ("title", "body", "type", "dedup_key")


@admin.register(models.NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    list_display = ("key", "default_channel")
    search_fields = ("key", "title_template")


@admin.register(models.NotificationDeviceToken)
class NotificationDeviceTokenAdmin(admin.ModelAdmin):
    list_display = ("user_id", "device_id", "platform", "token_type", "masked_push_token", "enabled", "last_seen_at")
    list_filter = ("platform", "token_type", "enabled")
    search_fields = ("user_id", "device_id")
    readonly_fields = ("masked_push_token", "last_seen_at", "created_at", "updated_at")

    def masked_push_token(self, obj):
        token = str(getattr(obj, "push_token", "") or "")
        if len(token) <= 10:
            return "***"
        return f"{token[:6]}...{token[-4:]}"


for m in [models.NotificationDelivery, models.NotificationRule, models.NotificationDigest]:
    try:
        admin.site.register(m)
    except admin.sites.AlreadyRegistered:
        pass
