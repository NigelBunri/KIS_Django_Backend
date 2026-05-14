from django.contrib import admin

from .models import (
    BroadcastChannel,
    BroadcastChannelRole,
    BroadcastChannelSubscription,
    BroadcastPlaylist,
    ChannelAnalyticsDailyRollup,
    ChannelContent,
    ChannelContentAsset,
    ChannelModerationRecord,
    Medium,
    Service,
    BroadcastHealthProfile,
    BroadcastHealthInstitution,
    BroadcastHealthInstitutionMember,
    BroadcastHealthInstitutionService,
    BroadcastItem,
)


@admin.register(BroadcastChannel)
class BroadcastChannelAdmin(admin.ModelAdmin):
    list_display = (
        'handle',
        'display_name',
        'owner_type',
        'owner_id',
        'is_public',
        'is_verified',
        'subscriber_count',
        'created_at',
    )
    list_filter = ('owner_type', 'is_public', 'is_verified', 'is_deleted')
    search_fields = ('handle', 'display_name', 'description')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(BroadcastChannelRole)
class BroadcastChannelRoleAdmin(admin.ModelAdmin):
    list_display = ('channel', 'user', 'role', 'created_at')
    list_filter = ('role',)
    search_fields = ('channel__handle', 'channel__display_name', 'user__phone', 'user__username')
    readonly_fields = ('created_at',)


@admin.register(BroadcastChannelSubscription)
class BroadcastChannelSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('channel', 'user', 'notifications', 'created_at', 'updated_at')
    list_filter = ('notifications',)
    search_fields = ('channel__handle', 'channel__display_name', 'user__phone', 'user__username')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(BroadcastPlaylist)
class BroadcastPlaylistAdmin(admin.ModelAdmin):
    list_display = ('id', 'channel', 'title', 'visibility', 'sort_order', 'created_at')
    list_filter = ('visibility',)
    search_fields = ('title', 'description', 'channel__handle', 'channel__display_name')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(ChannelContent)
class ChannelContentAdmin(admin.ModelAdmin):
    list_display = ('id', 'channel', 'content_type', 'title', 'visibility', 'status', 'published_at', 'is_deleted')
    list_filter = ('content_type', 'visibility', 'status', 'is_deleted')
    search_fields = ('title', 'description', 'text_plain', 'channel__handle', 'legacy_feed_entry_id')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(ChannelContentAsset)
class ChannelContentAssetAdmin(admin.ModelAdmin):
    list_display = ('id', 'content', 'asset_type', 'mime_type', 'processing_status', 'sort_order', 'created_at')
    list_filter = ('asset_type', 'processing_status')
    search_fields = ('url', 'storage_path', 'caption', 'content__title')
    readonly_fields = ('created_at',)


@admin.register(ChannelModerationRecord)
class ChannelModerationRecordAdmin(admin.ModelAdmin):
    list_display = ('id', 'channel', 'target_type', 'status', 'action', 'reporter', 'actor', 'created_at', 'resolved_at')
    list_filter = ('target_type', 'status', 'action', 'created_at')
    search_fields = ('reason', 'notes', 'channel__handle', 'channel__display_name', 'content__title', 'comment__body')
    readonly_fields = ('created_at', 'updated_at', 'resolved_at')


@admin.register(ChannelAnalyticsDailyRollup)
class ChannelAnalyticsDailyRollupAdmin(admin.ModelAdmin):
    list_display = ('channel', 'content', 'date', 'views', 'unique_viewers', 'watch_time_seconds', 'shares', 'comments', 'reactions')
    list_filter = ('date',)
    search_fields = ('channel__handle', 'channel__display_name', 'content__title')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Medium)
class HealthMediumAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'description', 'created_at')
    search_fields = ('name',)

@admin.register(Service)
class HealthServiceAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'description', 'created_at')
    search_fields = ('name',)

@admin.register(BroadcastHealthProfile)
class BroadcastHealthProfileAdmin(admin.ModelAdmin):
    list_display = ('id', 'profile', 'payload', 'created_at', 'updated_at')
    search_fields = ('id',)


@admin.register(BroadcastHealthInstitution)
class BroadcastHealthInstitutionAdmin(admin.ModelAdmin):
    list_display = ('id', 'health_profile', 'institution_uid', 'name', 'institution_type', 'updated_at')
    search_fields = ('institution_uid', 'name')


@admin.register(BroadcastHealthInstitutionMember)
class BroadcastHealthInstitutionMemberAdmin(admin.ModelAdmin):
    list_display = ('id', 'institution', 'member_uid', 'name', 'role', 'updated_at')
    search_fields = ('member_uid', 'name', 'phone', 'email')


@admin.register(BroadcastHealthInstitutionService)
class BroadcastHealthInstitutionServiceAdmin(admin.ModelAdmin):
    list_display = ('id', 'institution', 'service_uid', 'name', 'active', 'updated_at')
    search_fields = ('service_uid', 'name')


@admin.register(BroadcastItem)
class BroadcastItemAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'source_type',
        'source_id',
        'broadcasted_by',
        'broadcasted_at',
        'expires_at',
        'is_deleted',
    )
    list_filter = ('source_type', 'is_deleted')
    search_fields = ('source_id', 'metadata')
    readonly_fields = ('created_at', 'updated_at')
