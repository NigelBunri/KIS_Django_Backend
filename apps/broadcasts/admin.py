from django.contrib import admin

from .models import (
    BroadcastChannel,
    BroadcastChannelRole,
    BroadcastChannelSubscription,
    BroadcastPlaylist,
    ChannelAdCampaign,
    ChannelAdSlot,
    ChannelAnalyticsDailyRollup,
    ChannelContent,
    ChannelContentAsset,
    ChannelContentComment,
    ChannelContentCopyrightClaim,
    ChannelContentReaction,
    ChannelHomepageShelf,
    ChannelLiveStream,
    ChannelMembership,
    ChannelMembershipTier,
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
    list_display = (
        'id', 'channel', 'content_type', 'title', 'visibility', 'status',
        'moderation_status', 'moderation_expires_at', 'published_at', 'is_deleted',
    )
    list_filter = ('content_type', 'visibility', 'status', 'moderation_status', 'is_deleted')
    search_fields = ('title', 'description', 'text_plain', 'channel__handle', 'legacy_feed_entry_id')
    readonly_fields = ('created_at', 'updated_at')
    raw_id_fields = ('channel', 'moderation_reviewed_by', 'created_by')


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


@admin.register(ChannelLiveStream)
class ChannelLiveStreamAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'channel', 'title', 'status', 'provider', 'viewer_count',
        'peak_viewer_count', 'scheduled_start_at', 'started_at', 'ended_at',
    )
    list_filter = ('status', 'provider', 'latency_mode', 'dvr_enabled')
    search_fields = ('title', 'channel__handle', 'channel__display_name', 'provider_stream_id')
    raw_id_fields = ('channel', 'content', 'created_by')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(ChannelContentComment)
class ChannelContentCommentAdmin(admin.ModelAdmin):
    list_display = ('id', 'content', 'user', 'is_pinned', 'is_deleted', 'created_at')
    list_filter = ('is_pinned', 'is_deleted')
    search_fields = ('body', 'content__title', 'user__phone', 'user__username')
    raw_id_fields = ('content', 'user', 'parent')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(ChannelContentReaction)
class ChannelContentReactionAdmin(admin.ModelAdmin):
    list_display = ('id', 'content', 'user', 'reaction', 'created_at')
    list_filter = ('reaction',)
    search_fields = ('content__title', 'user__phone', 'user__username')
    raw_id_fields = ('content', 'user')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(ChannelContentCopyrightClaim)
class ChannelContentCopyrightClaimAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'content', 'claimant_name', 'claimant_channel', 'claim_type',
        'status', 'resolved_at', 'created_at',
    )
    list_filter = ('status', 'claim_type')
    search_fields = ('claimant_name', 'asset_fingerprint', 'content__title', 'dispute_reason', 'resolution_notes')
    raw_id_fields = ('content', 'claimant_channel')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(ChannelAdCampaign)
class ChannelAdCampaignAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'title', 'advertiser_channel', 'status', 'budget_cents',
        'spent_cents', 'currency', 'start_date', 'end_date',
    )
    list_filter = ('status', 'currency')
    search_fields = ('title', 'advertiser_channel__handle', 'advertiser_channel__display_name')
    raw_id_fields = ('advertiser_channel',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(ChannelAdSlot)
class ChannelAdSlotAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'content', 'campaign', 'placement', 'timestamp_seconds',
        'is_skippable', 'impression_count', 'skip_count', 'created_at',
    )
    list_filter = ('placement', 'is_skippable')
    search_fields = ('content__title', 'campaign__title')
    raw_id_fields = ('content', 'campaign')
    readonly_fields = ('created_at',)


@admin.register(ChannelMembershipTier)
class ChannelMembershipTierAdmin(admin.ModelAdmin):
    list_display = ('id', 'channel', 'title', 'price_cents', 'currency', 'is_active', 'sort_order')
    list_filter = ('is_active', 'currency')
    search_fields = ('title', 'channel__handle', 'channel__display_name')
    raw_id_fields = ('channel',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(ChannelMembership)
class ChannelMembershipAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'tier', 'status', 'started_at', 'expires_at')
    list_filter = ('status',)
    search_fields = ('user__phone', 'user__username', 'tier__title', 'payment_reference')
    raw_id_fields = ('user', 'tier')
    readonly_fields = ('started_at',)


@admin.register(ChannelHomepageShelf)
class ChannelHomepageShelfAdmin(admin.ModelAdmin):
    list_display = ('id', 'channel', 'title', 'shelf_type', 'sort_order', 'is_active')
    list_filter = ('shelf_type', 'is_active')
    search_fields = ('title', 'channel__handle', 'channel__display_name')
    raw_id_fields = ('channel',)
    readonly_fields = ('created_at', 'updated_at')


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
