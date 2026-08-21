import uuid
from datetime import timedelta

from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone

from apps.accounts.models import Profile, User
from apps.chat.models import Conversation
from apps.channels.models import Channel
from apps.communities.models import Community
from apps.partners.models import Partner
from apps.billing.models import WalletTransaction
from apps.commerce.constants import KIS_COIN_CODE
from common.media_urls import normalize_image_payload


class BroadcastSourceType(models.TextChoices):
    COMMUNITY_POST = "community_post", "Community Post"
    PARTNER_POST = "partner_post", "Partner Post"
    CHANNEL_MESSAGE = "channel_message", "Channel Message"
    BROADCAST_CHANNEL = "broadcast_channel", "Broadcast Channel"
    CHANNEL_CONTENT = "channel_content", "Channel Content"
    BROADCAST_FEED_ENTRY = "broadcast_feed_entry", "Broadcast Feed Entry"
    MARKET_PRODUCT = "market_product", "Market Product"
    MARKET_SERVICE = "market_service", "Market Service"
    EDUCATION_COURSE = "education_course", "Education Course"
    EDUCATION_PROFILE = "education_profile", "Education Profile"
    EDUCATION_BROADCAST = "education_broadcast", "Education Broadcast"


def _default_expires_at():
    return timezone.now() + timedelta(days=10)


class BroadcastItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_type = models.CharField(max_length=32, choices=BroadcastSourceType.choices, db_index=True)
    source_id = models.CharField(max_length=128, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    conversation_id = models.UUIDField(null=True, blank=True, db_index=True)
    channel = models.ForeignKey(Channel, null=True, blank=True, on_delete=models.SET_NULL, related_name="broadcast_items")
    broadcasted_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="broadcasts")
    broadcasted_at = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField(default=_default_expires_at, db_index=True)
    comment_conversation = models.ForeignKey(
        Conversation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="broadcast_comment_threads",
    )
    is_deleted = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcast_item"
        unique_together = [("source_type", "source_id")]
        indexes = [
            models.Index(fields=["source_type", "source_id"]),
            models.Index(fields=["expires_at"]),
        ]


class EducationProfileType(models.TextChoices):
    COURSE = "course", "Course"
    DEGREE = "degree", "Degree Program"
    CAMP = "camp", "Camp"
    VOCATIONAL = "vocational", "Vocational Training"
    WORKSHOP = "workshop", "Workshop"
    MISC = "misc", "Other"


class BroadcastFeedProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile = models.OneToOneField(Profile, on_delete=models.CASCADE, related_name="broadcast_feed_profile")
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcast_feed_profile"


class BroadcastChannelPayoutAccountStatus(models.TextChoices):
    """Mirrors EducationInstitutionPayoutAccountStatus below — kept as a
    separate definition per model rather than a shared import, matching
    the same per-model convention used for Shop/HealthInstitution."""
    NOT_CONNECTED = "not_connected", "Not connected"
    PENDING = "pending", "Pending"
    ACTIVE = "active", "Active"


class BroadcastChannel(models.Model):
    class OwnerType(models.TextChoices):
        USER = "user", "User"
        SHOP = "shop", "Shop"
        HEALTH = "health", "Health institution"
        EDUCATION = "education", "Education institution"
        PARTNER = "partner", "Partner organization"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_type = models.CharField(max_length=24, choices=OwnerType.choices, db_index=True)
    owner_id = models.UUIDField(db_index=True)
    owner_user = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="owned_broadcast_channels",
    )
    handle = models.SlugField(max_length=80, unique=True)
    display_name = models.CharField(max_length=140)
    description = models.TextField(blank=True, default="")
    avatar_url = models.URLField(blank=True, default="")
    banner_url = models.URLField(blank=True, default="")
    country = models.CharField(max_length=8, blank=True, default="")
    language = models.CharField(max_length=16, blank=True, default="")
    category = models.CharField(max_length=64, blank=True, default="")
    links = models.JSONField(default=list, blank=True)
    branding = models.JSONField(default=dict, blank=True)
    verification_badges = models.JSONField(default=list, blank=True)
    settings = models.JSONField(default=dict, blank=True)
    trailer_content = models.ForeignKey(
        'ChannelContent',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='trailer_for_channels',
    )
    featured_content = models.ForeignKey(
        'ChannelContent',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='featured_for_channels',
    )
    is_public = models.BooleanField(default=True, db_index=True)
    is_verified = models.BooleanField(default=False, db_index=True)
    is_deleted = models.BooleanField(default=False, db_index=True)
    subscriber_count = models.PositiveIntegerField(default=0)
    content_count = models.PositiveIntegerField(default=0)
    # Flutterwave subaccount for direct-to-creator payout splitting on
    # tips/memberships/PPV — only meaningful for owner_type=USER channels
    # (institution-owned channels already settle via their owning Shop/
    # HealthInstitution/EducationInstitution's own payout account). Only
    # the provider-issued subaccount id and display-safe fields are
    # stored; the raw bank account number is never persisted (see
    # ChannelPayoutAccountConnectView, apps/broadcasts/views.py).
    flutterwave_subaccount_id = models.CharField(max_length=128, blank=True, default="")
    payout_account_status = models.CharField(
        max_length=16,
        choices=BroadcastChannelPayoutAccountStatus.choices,
        default=BroadcastChannelPayoutAccountStatus.NOT_CONNECTED,
    )
    payout_account_name = models.CharField(max_length=255, blank=True, default="")
    payout_bank_last4 = models.CharField(max_length=8, blank=True, default="")
    # Stripe Connect (Express account) — second supported payout rail
    # alongside the Flutterwave subaccount above, same owner_type=USER-only
    # scope, kept in sync by the account.updated webhook
    # (apps.billing.views.StripeWebhookView).
    stripe_account_id = models.CharField(max_length=128, blank=True, default="")
    stripe_charges_enabled = models.BooleanField(default=False)
    stripe_payouts_enabled = models.BooleanField(default=False)
    stripe_details_submitted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcast_channel"
        indexes = [
            models.Index(fields=["owner_type", "owner_id"]),
            models.Index(fields=["handle"]),
            models.Index(fields=["is_public", "is_deleted"]),
        ]
        constraints = [
            models.UniqueConstraint(Lower("handle"), name="broadcast_channel_handle_ci_unique"),
        ]

    def __str__(self):
        return f"@{self.handle}"


class BroadcastChannelRole(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        MANAGER = "manager", "Manager"
        EDITOR = "editor", "Editor"
        MODERATOR = "moderator", "Moderator"
        ANALYST = "analyst", "Analyst"

    channel = models.ForeignKey(BroadcastChannel, on_delete=models.CASCADE, related_name="roles")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="broadcast_channel_roles")
    role = models.CharField(max_length=24, choices=Role.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "broadcast_channel_role"
        unique_together = [("channel", "user", "role")]
        indexes = [
            models.Index(fields=["channel", "role"]),
            models.Index(fields=["user", "role"]),
        ]

    def __str__(self):
        return f"{self.channel_id}:{self.user_id}:{self.role}"


class BroadcastChannelSubscription(models.Model):
    class NotificationLevel(models.TextChoices):
        NONE = "none", "None"
        PERSONALIZED = "personalized", "Personalized"
        ALL = "all", "All"

    channel = models.ForeignKey(BroadcastChannel, on_delete=models.CASCADE, related_name="subscriptions")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="broadcast_channel_subscriptions")
    notifications = models.CharField(
        max_length=16,
        choices=NotificationLevel.choices,
        default=NotificationLevel.PERSONALIZED,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcast_channel_subscription"
        unique_together = [("channel", "user")]
        indexes = [
            models.Index(fields=["channel", "notifications"]),
            models.Index(fields=["user", "notifications"]),
        ]

    def __str__(self):
        return f"{self.channel_id}:{self.user_id}:{self.notifications}"


class BroadcastPlaylist(models.Model):
    class Visibility(models.TextChoices):
        PUBLIC = "public", "Public"
        UNLISTED = "unlisted", "Unlisted"
        PRIVATE = "private", "Private"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.ForeignKey(BroadcastChannel, on_delete=models.CASCADE, related_name="playlists")
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True, default="")
    visibility = models.CharField(max_length=16, choices=Visibility.choices, default=Visibility.PUBLIC)
    sort_order = models.PositiveIntegerField(default=0)
    shuffle_enabled = models.BooleanField(default=False)
    metadata = models.JSONField(default=dict, blank=True)
    is_collaborative = models.BooleanField(default=False)
    collaborators = models.ManyToManyField(
        User, blank=True, related_name="collaborative_playlists"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcast_playlist"
        ordering = ["sort_order", "-created_at"]
        indexes = [
            models.Index(fields=["channel", "visibility"]),
            models.Index(fields=["channel", "sort_order"]),
        ]

    def __str__(self):
        return self.title


class ChannelContentType(models.TextChoices):
    VIDEO = "video", "Video"
    SHORT_VIDEO = "short_video", "Short video"
    IMAGE = "image", "Image"
    GALLERY = "gallery", "Gallery"
    TEXT = "text", "Text"
    RICH_TEXT = "rich_text", "Rich text"
    AUDIO = "audio", "Audio"
    DOCUMENT = "document", "Document"
    LINK = "link", "Link"
    POLL = "poll", "Poll"
    EVENT = "event", "Event"
    LIVE_STREAM = "live_stream", "Live stream"
    REPLAY = "replay", "Replay"


class ChannelContent(models.Model):
    class Visibility(models.TextChoices):
        PUBLIC = "public", "Public"
        UNLISTED = "unlisted", "Unlisted"
        PRIVATE = "private", "Private"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SCHEDULED = "scheduled", "Scheduled"
        PUBLISHED = "published", "Published"
        PROCESSING = "processing", "Processing"
        FAILED = "failed", "Failed"
        ARCHIVED = "archived", "Archived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.ForeignKey(BroadcastChannel, on_delete=models.CASCADE, related_name="contents")
    legacy_broadcast_item = models.OneToOneField(
        BroadcastItem,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="channel_content",
    )
    legacy_feed_entry_id = models.UUIDField(null=True, blank=True, db_index=True)
    content_type = models.CharField(max_length=32, choices=ChannelContentType.choices, db_index=True)
    title = models.CharField(max_length=220, blank=True, default="")
    description = models.TextField(blank=True, default="")
    text_plain = models.TextField(blank=True, default="")
    text_doc = models.JSONField(default=dict, blank=True)
    # Populated from feed attachment URLs, which can be presigned S3 GET
    # URLs (signature/credential/expiry query string) — needs real headroom
    # beyond URLField's default max_length=200.
    thumbnail_url = models.URLField(max_length=2048, blank=True, default="")
    visibility = models.CharField(max_length=16, choices=Visibility.choices, default=Visibility.PUBLIC, db_index=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    scheduled_at = models.DateTimeField(null=True, blank=True, db_index=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    tags = models.JSONField(default=list, blank=True, help_text="List of string tags for discovery filtering.")
    comments_disabled = models.BooleanField(default=False)
    age_restriction = models.CharField(
        max_length=8,
        choices=[("none", "None"), ("13+", "13+"), ("18+", "18+")],
        default="none",
        db_index=True,
    )
    content_rating = models.CharField(
        max_length=8,
        choices=[("NR", "Not Rated"), ("G", "G"), ("PG", "PG"), ("PG-13", "PG-13"), ("R", "R")],
        default="NR",
    )
    stats = models.JSONField(default=dict, blank=True)
    is_deleted = models.BooleanField(default=False, db_index=True)
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_channel_contents",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_content"
        indexes = [
            models.Index(fields=["channel", "status", "published_at"]),
            models.Index(fields=["content_type", "status"]),
            models.Index(fields=["visibility", "is_deleted"]),
        ]

    def __str__(self):
        return self.title or str(self.id)


class ChannelContentAsset(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    content = models.ForeignKey(ChannelContent, on_delete=models.CASCADE, related_name="assets")
    asset_type = models.CharField(max_length=32)
    # Same presigned-S3-URL headroom concern as ChannelContent.thumbnail_url
    # above — these are populated straight from attachment["url"]/["thumbnail_url"].
    url = models.URLField(max_length=2048, blank=True, default="")
    storage_path = models.CharField(max_length=512, blank=True, default="")
    mime_type = models.CharField(max_length=128, blank=True, default="")
    size_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    thumbnail_url = models.URLField(max_length=2048, blank=True, default="")
    caption = models.TextField(blank=True, default="")
    sort_order = models.PositiveIntegerField(default=0)
    processing_status = models.CharField(max_length=24, default="ready")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "channel_content_asset"
        ordering = ["sort_order", "created_at"]
        indexes = [
            models.Index(fields=["content", "asset_type"]),
            models.Index(fields=["processing_status"]),
        ]

    def __str__(self):
        return f"{self.content_id}:{self.asset_type}"


class ChannelLiveStream(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        LIVE = "live", "Live"
        ENDED = "ended", "Ended"
        CANCELLED = "cancelled", "Cancelled"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.ForeignKey(BroadcastChannel, on_delete=models.CASCADE, related_name="live_streams")
    content = models.OneToOneField(
        ChannelContent,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="live_stream",
    )
    title = models.CharField(max_length=220)
    description = models.TextField(blank=True, default="")
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.SCHEDULED, db_index=True)
    scheduled_start_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    provider = models.CharField(max_length=32, blank=True, default="")
    provider_stream_id = models.CharField(max_length=160, blank=True, default="")
    ingest_url = models.CharField(max_length=512, blank=True, default="")
    stream_key_hash = models.CharField(max_length=128, blank=True, default="")
    playback_url = models.URLField(blank=True, default="")
    replay_url = models.URLField(blank=True, default="")
    thumbnail_url = models.URLField(blank=True, default="")
    viewer_count = models.PositiveIntegerField(default=0)
    peak_viewer_count = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_channel_live_streams",
    )
    latency_mode = models.CharField(
        max_length=16,
        choices=[("normal", "Normal"), ("low", "Low Latency"), ("ultra_low", "Ultra Low Latency")],
        default="normal",
    )
    dvr_enabled = models.BooleanField(default=False)
    dvr_window_seconds = models.PositiveIntegerField(default=3600)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_live_stream"
        indexes = [
            models.Index(fields=["channel", "status", "scheduled_start_at"]),
            models.Index(fields=["provider", "provider_stream_id"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        return self.title or str(self.id)


class ChannelEmbedPolicy(models.Model):
    channel = models.OneToOneField(
        BroadcastChannel,
        on_delete=models.CASCADE,
        related_name="embed_policy",
    )
    allow_embeds = models.BooleanField(default=True)
    allowed_domains = models.JSONField(default=list, blank=True)
    blocked_domains = models.JSONField(default=list, blank=True)
    require_signed_token = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_embed_policy"

    def __str__(self):
        return f"{self.channel_id}:embeds"


class ChannelContentEmbed(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    content = models.ForeignKey(
        ChannelContent,
        on_delete=models.CASCADE,
        related_name="embeds",
    )
    domain = models.CharField(max_length=255, blank=True, default="")
    token_hash = models.CharField(max_length=128, blank=True, default="")
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_channel_content_embeds",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "channel_content_embed"
        indexes = [
            models.Index(fields=["content", "is_active"]),
            models.Index(fields=["domain", "is_active"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self):
        return f"{self.content_id}:{self.domain or 'any'}"


class ChannelContentReaction(models.Model):
    id = models.BigAutoField(primary_key=True)
    content = models.ForeignKey(ChannelContent, on_delete=models.CASCADE, related_name="reactions")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="channel_content_reactions")
    reaction = models.CharField(max_length=32, default="like")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_content_reaction"
        unique_together = [("content", "user")]
        indexes = [
            models.Index(fields=["content", "reaction"]),
            models.Index(fields=["user", "created_at"]),
        ]


class ChannelContentSave(models.Model):
    id = models.BigAutoField(primary_key=True)
    content = models.ForeignKey(ChannelContent, on_delete=models.CASCADE, related_name="saves")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="saved_channel_contents")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "channel_content_save"
        unique_together = [("content", "user")]
        indexes = [
            models.Index(fields=["content", "created_at"]),
            models.Index(fields=["user", "created_at"]),
        ]


class ChannelContentComment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    content = models.ForeignKey(ChannelContent, on_delete=models.CASCADE, related_name="comments")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="channel_content_comments")
    body = models.TextField()
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="replies")
    is_pinned = models.BooleanField(default=False, db_index=True)
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_content_comment"
        indexes = [
            models.Index(fields=["content", "created_at"]),
            models.Index(fields=["content", "is_pinned"]),
            models.Index(fields=["user", "created_at"]),
        ]


class ChannelCommentReaction(models.Model):
    id = models.BigAutoField(primary_key=True)
    comment = models.ForeignKey(ChannelContentComment, on_delete=models.CASCADE, related_name="reactions")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="channel_comment_reactions")
    reaction = models.CharField(max_length=32, default="like")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "channel_comment_reaction"
        unique_together = [("comment", "user")]
        indexes = [
            models.Index(fields=["comment", "reaction"]),
            models.Index(fields=["user", "created_at"]),
        ]


class ChannelContentClip(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    content = models.ForeignKey(ChannelContent, on_delete=models.CASCADE, related_name="clips")
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="channel_content_clips")
    title = models.CharField(max_length=255, blank=True)
    start_seconds = models.PositiveIntegerField()
    end_seconds = models.PositiveIntegerField()
    status = models.CharField(max_length=24, default="pending")
    clip_url = models.URLField(blank=True)
    thumbnail_url = models.URLField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_content_clip"
        indexes = [
            models.Index(fields=["content", "created_at"]),
            models.Index(fields=["created_by", "created_at"]),
        ]


class ChannelContentChapter(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    content = models.ForeignKey(ChannelContent, on_delete=models.CASCADE, related_name="chapters")
    title = models.CharField(max_length=255)
    start_seconds = models.PositiveIntegerField()
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_content_chapter"
        ordering = ["sort_order", "start_seconds"]
        unique_together = [("content", "start_seconds")]
        indexes = [
            models.Index(fields=["content", "sort_order"]),
        ]


class ChannelWatchHistory(models.Model):
    id = models.BigAutoField(primary_key=True)
    content = models.ForeignKey(ChannelContent, on_delete=models.CASCADE, related_name="watch_history")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="channel_watch_history")
    progress_seconds = models.PositiveIntegerField(default=0)
    completed = models.BooleanField(default=False)
    last_viewed_at = models.DateTimeField(default=timezone.now, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "channel_watch_history"
        unique_together = [("content", "user")]
        indexes = [
            models.Index(fields=["content", "last_viewed_at"]),
            models.Index(fields=["user", "last_viewed_at"]),
        ]


class BroadcastPlaylistItem(models.Model):
    id = models.BigAutoField(primary_key=True)
    playlist = models.ForeignKey(BroadcastPlaylist, on_delete=models.CASCADE, related_name="items")
    content = models.ForeignKey(ChannelContent, on_delete=models.CASCADE, related_name="playlist_items")
    sort_order = models.PositiveIntegerField(default=0)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "broadcast_playlist_item"
        unique_together = [("playlist", "content")]
        ordering = ["sort_order", "added_at"]
        indexes = [
            models.Index(fields=["playlist", "sort_order"]),
            models.Index(fields=["content"]),
        ]


class ChannelContentSubtitle(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    content = models.ForeignKey(ChannelContent, on_delete=models.CASCADE, related_name='subtitles')
    language = models.CharField(max_length=16)
    label = models.CharField(max_length=80, blank=True, default='')
    vtt_url = models.URLField(blank=True, default='')
    segments = models.JSONField(default=list, blank=True)
    is_auto_generated = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'channel_content_subtitle'
        unique_together = [('content', 'language')]


class ChannelContentEndScreen(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    content = models.OneToOneField(ChannelContent, on_delete=models.CASCADE, related_name='end_screen')
    config = models.JSONField(default=list, blank=True)
    is_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'channel_content_end_screen'


class ChannelContentCard(models.Model):
    class CardType(models.TextChoices):
        VIDEO = 'video', 'Video'
        POLL = 'poll', 'Poll'
        LINK = 'link', 'Link'
        PLAYLIST = 'playlist', 'Playlist'
        CHANNEL = 'channel', 'Channel'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    content = models.ForeignKey(ChannelContent, on_delete=models.CASCADE, related_name='cards')
    card_type = models.CharField(max_length=16, choices=CardType.choices)
    title = models.CharField(max_length=140, blank=True, default='')
    start_seconds = models.PositiveIntegerField(default=0)
    end_seconds = models.PositiveIntegerField(null=True, blank=True)
    target_id = models.CharField(max_length=80, blank=True, default='')
    url = models.URLField(blank=True, default='')
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = 'channel_content_card'
        ordering = ['start_seconds', 'sort_order']


class ChannelActivityEvent(models.Model):
    class EventType(models.TextChoices):
        NEW_CONTENT = 'new_content', 'New Content'
        NEW_SUBSCRIBER = 'new_subscriber', 'New Subscriber'
        NEW_COMMENT = 'new_comment', 'New Comment'
        NEW_REACTION = 'new_reaction', 'New Reaction'
        LIVE_STARTED = 'live_started', 'Live Started'
        MILESTONE = 'milestone', 'Milestone'

    id = models.BigAutoField(primary_key=True)
    channel = models.ForeignKey(BroadcastChannel, on_delete=models.CASCADE, related_name='activity_events')
    event_type = models.CharField(max_length=24, choices=EventType.choices, db_index=True)
    actor_display = models.CharField(max_length=120, blank=True, default='')
    target_type = models.CharField(max_length=32, blank=True, default='')
    target_id = models.CharField(max_length=80, blank=True, default='')
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'channel_activity_event'
        indexes = [models.Index(fields=['channel', 'created_at'])]


class ChannelLivePoll(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        ENDED = 'ended', 'Ended'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    stream = models.ForeignKey(ChannelLiveStream, on_delete=models.CASCADE, related_name='polls')
    question = models.CharField(max_length=300)
    options = models.JSONField(default=list)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_live_polls')
    created_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'channel_live_poll'


class ChannelLivePollVote(models.Model):
    poll = models.ForeignKey(ChannelLivePoll, on_delete=models.CASCADE, related_name='votes')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='live_poll_votes')
    option_index = models.PositiveSmallIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'channel_live_poll_vote'
        unique_together = [('poll', 'user')]


class ChannelLiveQA(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        ENDED = 'ended', 'Ended'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    stream = models.ForeignKey(ChannelLiveStream, on_delete=models.CASCADE, related_name='qa_sessions')
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_qa_sessions')
    created_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'channel_live_qa'


class ChannelLiveQAQuestion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(ChannelLiveQA, on_delete=models.CASCADE, related_name='questions')
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='live_qa_questions')
    user_display = models.CharField(max_length=120, blank=True, default='')
    question_text = models.TextField()
    upvote_count = models.PositiveIntegerField(default=0)
    is_answered = models.BooleanField(default=False)
    is_pinned = models.BooleanField(default=False)
    is_hidden = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'channel_live_qa_question'
        ordering = ['-is_pinned', '-upvote_count', 'created_at']


class ChannelLiveQAQuestionUpvote(models.Model):
    question = models.ForeignKey(ChannelLiveQAQuestion, on_delete=models.CASCADE, related_name='upvotes')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='qa_question_upvotes')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'channel_live_qa_question_upvote'
        unique_together = [('question', 'user')]


class CommentCreatorHeart(models.Model):
    comment = models.OneToOneField('ChannelContentComment', on_delete=models.CASCADE, related_name='creator_heart')
    channel = models.ForeignKey(BroadcastChannel, on_delete=models.CASCADE, related_name='hearted_comments')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'comment_creator_heart'


class ChannelWatchHistorySettings(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='watch_history_settings')
    is_paused = models.BooleanField(default=False)

    class Meta:
        db_table = 'channel_watch_history_settings'


class ChannelModerationRecord(models.Model):
    class TargetType(models.TextChoices):
        CHANNEL = "channel", "Channel"
        CONTENT = "content", "Content"
        COMMENT = "comment", "Comment"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        REVIEWING = "reviewing", "Reviewing"
        ACTIONED = "actioned", "Actioned"
        DISMISSED = "dismissed", "Dismissed"

    class Action(models.TextChoices):
        NONE = "none", "None"
        KEEP = "keep", "Keep"
        HIDE = "hide", "Hide"
        REMOVE = "remove", "Remove"
        RESTRICT_COMMENTS = "restrict_comments", "Restrict comments"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.ForeignKey(BroadcastChannel, on_delete=models.CASCADE, related_name="moderation_records")
    content = models.ForeignKey(ChannelContent, null=True, blank=True, on_delete=models.CASCADE, related_name="moderation_records")
    comment = models.ForeignKey(ChannelContentComment, null=True, blank=True, on_delete=models.CASCADE, related_name="moderation_records")
    target_type = models.CharField(max_length=16, choices=TargetType.choices, db_index=True)
    target_id = models.UUIDField(db_index=True)
    reporter = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="channel_moderation_reports")
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="channel_moderation_actions")
    reason = models.TextField(blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN, db_index=True)
    action = models.CharField(max_length=32, choices=Action.choices, default=Action.NONE)
    notes = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_moderation_record"
        indexes = [
            models.Index(fields=["channel", "status", "created_at"]),
            models.Index(fields=["target_type", "target_id"]),
            models.Index(fields=["reporter", "created_at"]),
        ]


class ChannelAnalyticsDailyRollup(models.Model):
    id = models.BigAutoField(primary_key=True)
    channel = models.ForeignKey(BroadcastChannel, on_delete=models.CASCADE, related_name="analytics_rollups")
    content = models.ForeignKey(ChannelContent, null=True, blank=True, on_delete=models.CASCADE, related_name="analytics_rollups")
    date = models.DateField(db_index=True)
    views = models.PositiveIntegerField(default=0)
    unique_viewers = models.PositiveIntegerField(default=0)
    impressions = models.PositiveIntegerField(default=0)
    watch_time_seconds = models.PositiveBigIntegerField(default=0)
    average_duration_seconds = models.PositiveIntegerField(default=0)
    subscribers_gained = models.PositiveIntegerField(default=0)
    subscribers_lost = models.PositiveIntegerField(default=0)
    shares = models.PositiveIntegerField(default=0)
    saves = models.PositiveIntegerField(default=0)
    comments = models.PositiveIntegerField(default=0)
    reactions = models.PositiveIntegerField(default=0)
    embed_impressions = models.PositiveIntegerField(default=0)
    live_peak_viewers = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_analytics_daily_rollup"
        unique_together = [("channel", "content", "date")]
        indexes = [
            models.Index(fields=["channel", "date"]),
            models.Index(fields=["content", "date"]),
        ]


class BroadcastHealthProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile = models.OneToOneField(Profile, on_delete=models.CASCADE, related_name="broadcast_health_profile")
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcast_health_profile"


class BroadcastHealthInstitution(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    health_profile = models.ForeignKey(
        BroadcastHealthProfile,
        on_delete=models.CASCADE,
        related_name="institution_rows",
    )
    institution_uid = models.CharField(max_length=128, db_index=True)
    institution_type = models.CharField(max_length=64, default="clinic")
    name = models.CharField(max_length=255)
    owner_user = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="broadcast_health_institutions_owned",
    )
    owner_name = models.CharField(max_length=255, blank=True, default="")
    owner_phone = models.CharField(max_length=64, blank=True, default="")
    owner_email = models.CharField(max_length=255, blank=True, default="")
    members_target_count = models.PositiveIntegerField(default=1)
    membership_open = models.BooleanField(default=False)
    membership_discount_pct = models.PositiveIntegerField(default=10)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcast_health_institution"
        constraints = [
            models.UniqueConstraint(
                fields=["health_profile", "institution_uid"],
                name="broadcast_health_institution_unique_profile_uid",
            ),
        ]
        indexes = [
            models.Index(fields=["health_profile", "institution_uid"]),
            models.Index(fields=["owner_user"]),
        ]


class BroadcastHealthInstitutionMember(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        BroadcastHealthInstitution,
        on_delete=models.CASCADE,
        related_name="member_rows",
    )
    member_uid = models.CharField(max_length=128, db_index=True)
    name = models.CharField(max_length=255)
    role = models.CharField(max_length=64, default="staff")
    phone = models.CharField(max_length=64, blank=True, default="")
    email = models.CharField(max_length=255, blank=True, default="")
    user = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="broadcast_health_memberships",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcast_health_institution_member"
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "member_uid"],
                name="broadcast_health_member_unique_institution_uid",
            ),
        ]
        indexes = [
            models.Index(fields=["institution", "member_uid"]),
            models.Index(fields=["user"]),
        ]


class BroadcastHealthInstitutionService(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        BroadcastHealthInstitution,
        on_delete=models.CASCADE,
        related_name="service_rows",
    )
    service_uid = models.CharField(max_length=128, db_index=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    active = models.BooleanField(default=True)
    base_price_cents = models.PositiveIntegerField(null=True, blank=True)
    medium_ids = models.JSONField(default=list, blank=True)
    medium_names = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcast_health_institution_service"
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "service_uid"],
                name="broadcast_health_service_unique_institution_uid",
            ),
        ]
        indexes = [
            models.Index(fields=["institution", "service_uid"]),
        ]


class BroadcastMarketProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile = models.OneToOneField(Profile, on_delete=models.CASCADE, related_name="broadcast_market_profile")
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcast_market_profile"


class Medium(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True, default="")
    system_flag = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mediums"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Service(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=180)
    description = models.TextField(blank=True, default="")
    is_default = models.BooleanField(default=False, db_index=True)
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="broadcast_health_services",
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "services"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["name", "created_by"], name="service_name_per_creator_uniq")
        ]

    def __str__(self):
        return self.name


class ServiceMediumMap(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name="medium_links")
    medium = models.ForeignKey(Medium, on_delete=models.CASCADE, related_name="service_links")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "service_medium_map"
        constraints = [
            models.UniqueConstraint(fields=["service", "medium"], name="service_medium_unique")
        ]


class EducationProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="education_profiles")
    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name="broadcast_education_profiles",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    profile_type = models.CharField(max_length=32, choices=EducationProfileType.choices, default=EducationProfileType.COURSE)
    metadata = models.JSONField(default=dict, blank=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_profile"
        indexes = [
            models.Index(fields=["user", "is_default"]),
            models.Index(fields=["profile", "is_default"]),
        ]

    def __str__(self):
        return f"{self.user_id}:{self.name}"


class EducationProfileCourse(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile = models.ForeignKey(EducationProfile, on_delete=models.CASCADE, related_name="courses")
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_profile_course"
        ordering = ["-created_at"]


class EducationProfileModule(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile = models.ForeignKey(EducationProfile, on_delete=models.CASCADE, related_name="modules")
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    resource_url = models.URLField(blank=True, null=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_profile_module"
        ordering = ["-created_at"]


class EducationProfileRole(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile = models.ForeignKey(EducationProfile, on_delete=models.CASCADE, related_name="roles")
    name = models.CharField(max_length=150)
    permissions = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_profile_role"
        ordering = ["-created_at"]


class EducationProfileRoleAssignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    role = models.ForeignKey(EducationProfileRole, on_delete=models.CASCADE, related_name="assignments")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="education_role_assignments")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "education_profile_role_assignment"
        unique_together = [("role", "user")]


class EducationInstitutionType(models.TextChoices):
    SCHOOL = "school", "School"
    COLLEGE = "college", "College"
    UNIVERSITY = "university", "University"
    ACADEMY = "academy", "Academy"
    TRAINING_CENTER = "training_center", "Training Center"
    BOOTCAMP = "bootcamp", "Bootcamp"
    COMMUNITY = "community", "Community"
    OTHER = "other", "Other"


class EducationInstitutionMembershipPolicy(models.TextChoices):
    OPEN = "open", "Open Membership"
    APPLICATION = "application", "Application Required"
    CLOSED = "closed", "Closed Membership"


class EducationInstitutionMembershipRole(models.TextChoices):
    OWNER = "owner", "Owner"
    MANAGER = "manager", "Manager"
    ADMINISTRATOR = "administrator", "Administrator"
    LECTURER = "lecturer", "Lecturer"
    ACADEMIC_STAFF = "academic_staff", "Academic Staff"
    STUDENT = "student", "Student"


class EducationInstitutionMembershipStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    PENDING = "pending", "Pending"
    REJECTED = "rejected", "Rejected"
    INVITED = "invited", "Invited"
    REMOVED = "removed", "Removed"


class EducationInstitutionPayoutAccountStatus(models.TextChoices):
    NOT_CONNECTED = "not_connected", "Not connected"
    PENDING = "pending", "Pending"
    ACTIVE = "active", "Active"


class EducationInstitution(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="owned_education_institutions",
    )
    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name="education_institutions",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    institution_type = models.CharField(
        max_length=32,
        choices=EducationInstitutionType.choices,
        default=EducationInstitutionType.ACADEMY,
    )
    membership_policy = models.CharField(
        max_length=16,
        choices=EducationInstitutionMembershipPolicy.choices,
        default=EducationInstitutionMembershipPolicy.APPLICATION,
    )
    contact_email = models.EmailField(blank=True, default="")
    contact_phone = models.CharField(max_length=64, blank=True, default="")
    branding = models.JSONField(default=dict, blank=True)
    settings = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    # Flutterwave subaccount for paid-course payout splitting. Only the
    # provider-issued subaccount id and display-safe fields are stored —
    # the raw bank account number is never persisted (see
    # EducationInstitutionPayoutAccountConnectView, apps/broadcasts/views.py).
    flutterwave_subaccount_id = models.CharField(max_length=128, blank=True, default="")
    payout_account_status = models.CharField(
        max_length=16,
        choices=EducationInstitutionPayoutAccountStatus.choices,
        default=EducationInstitutionPayoutAccountStatus.NOT_CONNECTED,
    )
    payout_account_name = models.CharField(max_length=255, blank=True, default="")
    payout_bank_last4 = models.CharField(max_length=8, blank=True, default="")
    # Stripe Connect (Express account) — second supported payout rail
    # alongside the Flutterwave subaccount above, kept in sync by the
    # account.updated webhook (apps.billing.views.StripeWebhookView).
    stripe_account_id = models.CharField(max_length=128, blank=True, default="")
    stripe_charges_enabled = models.BooleanField(default=False)
    stripe_payouts_enabled = models.BooleanField(default=False)
    stripe_details_submitted = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution"
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["owner", "is_active"]),
            models.Index(fields=["institution_type", "is_active"]),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if isinstance(self.branding, dict):
            image_keys = {
                "logo_url",
                "logoUrl",
                "image_url",
                "imageUrl",
                "banner_image_url",
                "bannerImageUrl",
                "cover_image_url",
                "coverImageUrl",
            }
            self.branding = {
                key: normalize_image_payload(value) if key in image_keys else value
                for key, value in self.branding.items()
            }
        super().save(*args, **kwargs)


class EducationInstitutionMembership(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="education_institution_memberships",
    )
    role = models.CharField(
        max_length=32,
        choices=EducationInstitutionMembershipRole.choices,
        default=EducationInstitutionMembershipRole.STUDENT,
    )
    status = models.CharField(
        max_length=16,
        choices=EducationInstitutionMembershipStatus.choices,
        default=EducationInstitutionMembershipStatus.PENDING,
    )
    title = models.CharField(max_length=255, blank=True, default="")
    permissions = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="created_education_institution_memberships",
        null=True,
        blank=True,
    )
    decided_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="decided_education_institution_memberships",
        null=True,
        blank=True,
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_membership"
        unique_together = [("institution", "user")]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["institution", "role"]),
            models.Index(fields=["user", "status"]),
        ]

    def __str__(self):
        return f"{self.institution_id}:{self.user_id}:{self.role}"


class EducationAcademicRecordStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"
    ARCHIVED = "archived", "Archived"


class EducationCourseVisibility(models.TextChoices):
    PUBLIC = "public", "Public"
    PRIVATE = "private", "Private"


class EducationCourseAccessRequestStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class EducationClassSessionStatus(models.TextChoices):
    SCHEDULED = "scheduled", "Scheduled"
    LIVE = "live", "Live"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class EducationClassSessionMode(models.TextChoices):
    ONLINE = "online", "Online"
    ONSITE = "onsite", "Onsite"
    HYBRID = "hybrid", "Hybrid"


class EducationMaterialKind(models.TextChoices):
    DOCUMENT = "document", "Document"
    VIDEO = "video", "Video"
    LINK = "link", "Link"
    SLIDES = "slides", "Slides"
    ASSIGNMENT = "assignment", "Assignment"
    REFERENCE = "reference", "Reference"


class EducationAssessmentType(models.TextChoices):
    MCQ = "mcq", "MCQ"
    THEORY = "theory", "Theory"
    MIXED = "mixed", "Mixed"


class EducationAssessmentQuestionType(models.TextChoices):
    MCQ = "mcq", "MCQ"
    TRUE_FALSE = "true_false", "True / False"
    SHORT_ANSWER = "short_answer", "Short Answer"
    ESSAY = "essay", "Essay"


class EducationAssessmentSubmissionStatus(models.TextChoices):
    STARTED = "started", "Started"
    SUBMITTED = "submitted", "Submitted"
    GRADED = "graded", "Graded"
    RETURNED = "returned", "Returned"
    CANCELLED = "cancelled", "Cancelled"


class EducationInstitutionEventType(models.TextChoices):
    EVENT = "event", "Event"
    TRAINING_SESSION = "training_session", "Training Session"


class EducationBroadcastKind(models.TextChoices):
    PROGRAM = "program", "Program"
    COURSE = "course", "Course"
    LESSON = "lesson", "Lesson"
    CLASS_SESSION = "class_session", "Class Session"
    TRAINING_SESSION = "training_session", "Training Session"
    EVENT = "event", "Event"
    INSTITUTION_NOTICE = "institution_notice", "Institution Notice"


class EducationBroadcastStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"
    ARCHIVED = "archived", "Archived"


class EducationEnrollmentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ENROLLED = "enrolled", "Enrolled"
    WAITLISTED = "waitlisted", "Waitlisted"
    CANCELLED = "cancelled", "Cancelled"
    COMPLETED = "completed", "Completed"


class EducationBookingStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PAYMENT_PENDING = "payment_pending", "Payment Pending"
    CONFIRMED = "confirmed", "Confirmed"
    WAITLISTED = "waitlisted", "Waitlisted"
    AWAITING_SATISFACTION = "awaiting_satisfaction", "Awaiting Satisfaction"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"
    EXPIRED = "expired", "Expired"
    REFUNDED = "refunded", "Refunded"


class EducationInstitutionStaffAssignmentRole(models.TextChoices):
    INSTRUCTOR = "instructor", "Instructor"
    COORDINATOR = "coordinator", "Coordinator"
    EXAMINER = "examiner", "Examiner"
    ADVISOR = "advisor", "Advisor"
    MODERATOR = "moderator", "Moderator"
    EVENT_HOST = "event_host", "Event Host"


class EducationInstitutionStaffAssignmentStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    INACTIVE = "inactive", "Inactive"


class EducationCourseModuleItemType(models.TextChoices):
    LESSON = "lesson", "Lesson"
    MATERIAL = "material", "Material"
    CLASS_SESSION = "class_session", "Class Session"
    ASSESSMENT = "assessment", "Assessment"
    EVENT = "event", "Event"
    BROADCAST = "broadcast", "Broadcast"


class EducationInstitutionProgram(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="programs",
    )
    title = models.CharField(max_length=255)
    code = models.CharField(max_length=64, blank=True, default="")
    summary = models.TextField(blank=True, default="")
    description = models.TextField(blank=True, default="")
    cover_image_url = models.URLField(max_length=2048, blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=EducationAcademicRecordStatus.choices,
        default=EducationAcademicRecordStatus.DRAFT,
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_program"
        ordering = ["title", "-created_at"]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["institution", "code"]),
        ]

    def __str__(self):
        return self.title


class EducationInstitutionStaffAssignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="staff_assignments",
    )
    membership = models.ForeignKey(
        EducationInstitutionMembership,
        on_delete=models.CASCADE,
        related_name="staff_assignments",
    )
    program = models.ForeignKey(
        "EducationInstitutionProgram",
        on_delete=models.SET_NULL,
        related_name="staff_assignments",
        null=True,
        blank=True,
    )
    course = models.ForeignKey(
        "EducationInstitutionCourse",
        on_delete=models.SET_NULL,
        related_name="staff_assignments",
        null=True,
        blank=True,
    )
    class_session = models.ForeignKey(
        "EducationInstitutionClassSession",
        on_delete=models.SET_NULL,
        related_name="staff_assignments",
        null=True,
        blank=True,
    )
    event = models.ForeignKey(
        "EducationInstitutionEvent",
        on_delete=models.SET_NULL,
        related_name="staff_assignments",
        null=True,
        blank=True,
    )
    assessment = models.ForeignKey(
        "EducationInstitutionAssessment",
        on_delete=models.SET_NULL,
        related_name="staff_assignments",
        null=True,
        blank=True,
    )
    role = models.CharField(
        max_length=24,
        choices=EducationInstitutionStaffAssignmentRole.choices,
        default=EducationInstitutionStaffAssignmentRole.INSTRUCTOR,
    )
    status = models.CharField(
        max_length=16,
        choices=EducationInstitutionStaffAssignmentStatus.choices,
        default=EducationInstitutionStaffAssignmentStatus.ACTIVE,
    )
    notes = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="created_education_staff_assignments",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_staff_assignment"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["membership", "status"]),
            models.Index(fields=["course", "status"]),
            models.Index(fields=["class_session", "status"]),
            models.Index(fields=["event", "status"]),
        ]

    def __str__(self):
        return f"{self.institution_id}:{self.membership_id}:{self.role}"


class EducationInstitutionCourse(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="courses_v2",
    )
    program = models.ForeignKey(
        EducationInstitutionProgram,
        on_delete=models.SET_NULL,
        related_name="courses",
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=255)
    code = models.CharField(max_length=64, blank=True, default="")
    summary = models.TextField(blank=True, default="")
    description = models.TextField(blank=True, default="")
    cover_image_url = models.URLField(max_length=2048, blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=EducationAcademicRecordStatus.choices,
        default=EducationAcademicRecordStatus.DRAFT,
    )
    duration_minutes = models.PositiveIntegerField(default=0)
    seat_limit = models.PositiveIntegerField(null=True, blank=True)
    # Pricing: null/0 means free — matches the exact convention already
    # used for EducationInstitutionBroadcast.price_amount. This is the
    # source of truth; any course-kind broadcast pointing at this course
    # has its own price fields kept in sync (see _sync_course_pricing_to_broadcasts).
    price_amount = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
    )
    price_currency = models.CharField(max_length=8, blank=True, default=KIS_COIN_CODE)
    visibility = models.CharField(
        max_length=16,
        choices=EducationCourseVisibility.choices,
        default=EducationCourseVisibility.PUBLIC,
    )
    metadata = models.JSONField(default=dict, blank=True)
    settings = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_course"
        ordering = ["title", "-created_at"]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["institution", "code"]),
            models.Index(fields=["program", "status"]),
            models.Index(fields=["institution", "visibility"]),
        ]

    def __str__(self):
        return self.title

    @property
    def is_free(self) -> bool:
        return not self.price_amount or self.price_amount <= 0


class EducationInstitutionCourseAccessRequest(models.Model):
    """Request-to-access workflow for private courses — same shape as
    EducationInstitutionMembership, one level down (per-course instead of
    per-institution). See _ensure_course_access_ready in views.py."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    course = models.ForeignKey(
        EducationInstitutionCourse,
        on_delete=models.CASCADE,
        related_name="access_requests",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="education_course_access_requests",
    )
    status = models.CharField(
        max_length=16,
        choices=EducationCourseAccessRequestStatus.choices,
        default=EducationCourseAccessRequestStatus.PENDING,
    )
    decided_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="decided_education_course_access_requests",
        null=True,
        blank=True,
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_course_access_request"
        unique_together = [("course", "user")]
        indexes = [
            models.Index(fields=["course", "status"]),
            models.Index(fields=["user", "status"]),
        ]

    def __str__(self):
        return f"{self.course_id}:{self.user_id}:{self.status}"


class EducationInstitutionCourseModule(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="course_modules",
    )
    course = models.ForeignKey(
        EducationInstitutionCourse,
        on_delete=models.CASCADE,
        related_name="modules_v2",
    )
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    module_order = models.PositiveIntegerField(default=0)
    is_preview = models.BooleanField(default=False)
    status = models.CharField(
        max_length=16,
        choices=EducationAcademicRecordStatus.choices,
        default=EducationAcademicRecordStatus.DRAFT,
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_course_module"
        ordering = ["module_order", "title", "-created_at"]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["course", "module_order"]),
        ]

    def __str__(self):
        return self.title


class EducationInstitutionLesson(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="lessons_v2",
    )
    course = models.ForeignKey(
        EducationInstitutionCourse,
        on_delete=models.CASCADE,
        related_name="lessons",
    )
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    content = models.TextField(blank=True, default="")
    cover_image_url = models.URLField(max_length=2048, blank=True, default="")
    lesson_order = models.PositiveIntegerField(default=0)
    duration_minutes = models.PositiveIntegerField(default=0)
    is_preview = models.BooleanField(default=False)
    status = models.CharField(
        max_length=16,
        choices=EducationAcademicRecordStatus.choices,
        default=EducationAcademicRecordStatus.DRAFT,
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_lesson"
        ordering = ["lesson_order", "title", "-created_at"]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["course", "lesson_order"]),
        ]

    def __str__(self):
        return self.title


class EducationInstitutionCourseModuleItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="course_module_items",
    )
    course = models.ForeignKey(
        EducationInstitutionCourse,
        on_delete=models.CASCADE,
        related_name="module_items",
    )
    module = models.ForeignKey(
        EducationInstitutionCourseModule,
        on_delete=models.CASCADE,
        related_name="items",
    )
    item_type = models.CharField(
        max_length=24,
        choices=EducationCourseModuleItemType.choices,
        default=EducationCourseModuleItemType.LESSON,
    )
    item_order = models.PositiveIntegerField(default=0)
    title_override = models.CharField(max_length=255, blank=True, default="")
    summary_override = models.TextField(blank=True, default="")
    estimated_minutes = models.PositiveIntegerField(default=0)
    lesson = models.ForeignKey(
        "EducationInstitutionLesson",
        on_delete=models.CASCADE,
        related_name="module_items",
        null=True,
        blank=True,
    )
    material = models.ForeignKey(
        "EducationInstitutionMaterial",
        on_delete=models.CASCADE,
        related_name="module_items",
        null=True,
        blank=True,
    )
    class_session = models.ForeignKey(
        "EducationInstitutionClassSession",
        on_delete=models.CASCADE,
        related_name="module_items",
        null=True,
        blank=True,
    )
    assessment = models.ForeignKey(
        "EducationInstitutionAssessment",
        on_delete=models.CASCADE,
        related_name="module_items",
        null=True,
        blank=True,
    )
    event = models.ForeignKey(
        "EducationInstitutionEvent",
        on_delete=models.CASCADE,
        related_name="module_items",
        null=True,
        blank=True,
    )
    broadcast = models.ForeignKey(
        "EducationInstitutionBroadcast",
        on_delete=models.CASCADE,
        related_name="module_items",
        null=True,
        blank=True,
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_course_module_item"
        ordering = ["item_order", "created_at"]
        indexes = [
            models.Index(fields=["institution", "item_type"]),
            models.Index(fields=["course", "item_order"]),
            models.Index(fields=["module", "item_order"]),
        ]

    def __str__(self):
        return f"{self.module_id}:{self.item_type}:{self.item_order}"


class EducationInstitutionClassSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="class_sessions",
    )
    course = models.ForeignKey(
        EducationInstitutionCourse,
        on_delete=models.CASCADE,
        related_name="class_sessions",
        null=True,
        blank=True,
    )
    lesson = models.ForeignKey(
        EducationInstitutionLesson,
        on_delete=models.SET_NULL,
        related_name="class_sessions",
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    cover_image_url = models.URLField(max_length=2048, blank=True, default="")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    timezone_name = models.CharField(max_length=64, blank=True, default="UTC")
    delivery_mode = models.CharField(
        max_length=16,
        choices=EducationClassSessionMode.choices,
        default=EducationClassSessionMode.ONLINE,
    )
    location_text = models.CharField(max_length=255, blank=True, default="")
    meeting_url = models.URLField(blank=True, default="")
    seat_limit = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=EducationClassSessionStatus.choices,
        default=EducationClassSessionStatus.SCHEDULED,
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_class_session"
        ordering = ["starts_at", "-created_at"]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["institution", "starts_at"]),
            models.Index(fields=["course", "starts_at"]),
        ]

    def __str__(self):
        return self.title


class EducationInstitutionMaterial(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="materials",
    )
    program = models.ForeignKey(
        EducationInstitutionProgram,
        on_delete=models.SET_NULL,
        related_name="materials",
        null=True,
        blank=True,
    )
    course = models.ForeignKey(
        EducationInstitutionCourse,
        on_delete=models.CASCADE,
        related_name="materials",
        null=True,
        blank=True,
    )
    lesson = models.ForeignKey(
        EducationInstitutionLesson,
        on_delete=models.CASCADE,
        related_name="materials",
        null=True,
        blank=True,
    )
    class_session = models.ForeignKey(
        EducationInstitutionClassSession,
        on_delete=models.SET_NULL,
        related_name="materials",
        null=True,
        blank=True,
    )
    assessment = models.ForeignKey(
        "EducationInstitutionAssessment",
        on_delete=models.SET_NULL,
        related_name="materials",
        null=True,
        blank=True,
    )
    program_links = models.ManyToManyField(
        EducationInstitutionProgram,
        related_name="linked_materials",
        blank=True,
    )
    course_links = models.ManyToManyField(
        "EducationInstitutionCourse",
        related_name="linked_materials",
        blank=True,
    )
    lesson_links = models.ManyToManyField(
        "EducationInstitutionLesson",
        related_name="linked_materials",
        blank=True,
    )
    class_session_links = models.ManyToManyField(
        "EducationInstitutionClassSession",
        related_name="linked_materials",
        blank=True,
    )
    assessment_links = models.ManyToManyField(
        "EducationInstitutionAssessment",
        related_name="linked_materials",
        blank=True,
    )
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    cover_image_url = models.URLField(max_length=2048, blank=True, default="")
    kind = models.CharField(
        max_length=16,
        choices=EducationMaterialKind.choices,
        default=EducationMaterialKind.DOCUMENT,
    )
    resource_url = models.URLField(max_length=2048, blank=True, default="")
    resource_name = models.CharField(max_length=255, blank=True, default="")
    resource_mime_type = models.CharField(max_length=128, blank=True, default="")
    storage_path = models.CharField(max_length=512, blank=True, default="")
    is_downloadable = models.BooleanField(default=True)
    status = models.CharField(
        max_length=16,
        choices=EducationAcademicRecordStatus.choices,
        default=EducationAcademicRecordStatus.DRAFT,
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_material"
        ordering = ["title", "-created_at"]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["program", "status"]),
            models.Index(fields=["course", "status"]),
            models.Index(fields=["lesson", "status"]),
            models.Index(fields=["class_session", "status"]),
            models.Index(fields=["assessment", "status"]),
        ]

    def __str__(self):
        return self.title


class EducationInstitutionAssessment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="assessments",
    )
    course = models.ForeignKey(
        EducationInstitutionCourse,
        on_delete=models.CASCADE,
        related_name="assessments",
        null=True,
        blank=True,
    )
    lesson = models.ForeignKey(
        EducationInstitutionLesson,
        on_delete=models.CASCADE,
        related_name="assessments",
        null=True,
        blank=True,
    )
    class_session = models.ForeignKey(
        EducationInstitutionClassSession,
        on_delete=models.CASCADE,
        related_name="assessments",
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    instructions = models.TextField(blank=True, default="")
    cover_image_url = models.URLField(max_length=2048, blank=True, default="")
    assessment_type = models.CharField(
        max_length=16,
        choices=EducationAssessmentType.choices,
        default=EducationAssessmentType.MCQ,
    )
    status = models.CharField(
        max_length=16,
        choices=EducationAcademicRecordStatus.choices,
        default=EducationAcademicRecordStatus.DRAFT,
    )
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    duration_minutes = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=1)
    passing_score_percent = models.PositiveIntegerField(default=0)
    total_points = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    metadata = models.JSONField(default=dict, blank=True)
    settings = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_assessment"
        ordering = ["title", "-created_at"]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["course", "status"]),
            models.Index(fields=["lesson", "status"]),
            models.Index(fields=["class_session", "status"]),
        ]

    def __str__(self):
        return self.title


class EducationInstitutionAssessmentQuestion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assessment = models.ForeignKey(
        EducationInstitutionAssessment,
        on_delete=models.CASCADE,
        related_name="questions",
    )
    prompt = models.TextField()
    question_type = models.CharField(
        max_length=16,
        choices=EducationAssessmentQuestionType.choices,
        default=EducationAssessmentQuestionType.MCQ,
    )
    question_order = models.PositiveIntegerField(default=0)
    points = models.DecimalField(max_digits=8, decimal_places=2, default=1)
    is_required = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_assessment_question"
        ordering = ["question_order", "created_at"]
        indexes = [
            models.Index(fields=["assessment", "question_order"]),
            models.Index(fields=["assessment", "question_type"]),
        ]

    def __str__(self):
        return self.prompt[:80]


class EducationInstitutionAssessmentOption(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    question = models.ForeignKey(
        EducationInstitutionAssessmentQuestion,
        on_delete=models.CASCADE,
        related_name="options",
    )
    option_text = models.TextField()
    option_order = models.PositiveIntegerField(default=0)
    is_correct = models.BooleanField(default=False)
    explanation = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_assessment_option"
        ordering = ["option_order", "created_at"]
        indexes = [
            models.Index(fields=["question", "option_order"]),
        ]

    def __str__(self):
        return self.option_text[:80]


class EducationInstitutionAssessmentSubmission(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assessment = models.ForeignKey(
        EducationInstitutionAssessment,
        on_delete=models.CASCADE,
        related_name="submissions",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="education_assessment_submissions",
    )
    attempt_number = models.PositiveIntegerField(default=1)
    status = models.CharField(
        max_length=16,
        choices=EducationAssessmentSubmissionStatus.choices,
        default=EducationAssessmentSubmissionStatus.STARTED,
    )
    earned_points = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    score_percent = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    grader = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="graded_education_assessment_submissions",
        null=True,
        blank=True,
    )
    grader_feedback = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    submitted_at = models.DateTimeField(null=True, blank=True)
    graded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_assessment_submission"
        ordering = ["-created_at"]
        unique_together = [("assessment", "user", "attempt_number")]
        indexes = [
            models.Index(fields=["assessment", "status"]),
            models.Index(fields=["user", "status"]),
        ]

    def __str__(self):
        return f"{self.assessment_id}:{self.user_id}:#{self.attempt_number}"


class EducationInstitutionAssessmentResponse(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    submission = models.ForeignKey(
        EducationInstitutionAssessmentSubmission,
        on_delete=models.CASCADE,
        related_name="responses",
    )
    question = models.ForeignKey(
        EducationInstitutionAssessmentQuestion,
        on_delete=models.CASCADE,
        related_name="responses",
    )
    answer_text = models.TextField(blank=True, default="")
    is_correct = models.BooleanField(null=True, blank=True)
    earned_points = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    grader_feedback = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_assessment_response"
        unique_together = [("submission", "question")]
        indexes = [
            models.Index(fields=["submission", "question"]),
        ]

    def __str__(self):
        return f"{self.submission_id}:{self.question_id}"


class EducationInstitutionAssessmentResponseOption(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    response = models.ForeignKey(
        EducationInstitutionAssessmentResponse,
        on_delete=models.CASCADE,
        related_name="selected_options",
    )
    option = models.ForeignKey(
        EducationInstitutionAssessmentOption,
        on_delete=models.CASCADE,
        related_name="response_links",
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "education_institution_assessment_response_option"
        unique_together = [("response", "option")]
        indexes = [
            models.Index(fields=["response", "option"]),
        ]


class EducationInstitutionEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="events",
    )
    program = models.ForeignKey(
        EducationInstitutionProgram,
        on_delete=models.SET_NULL,
        related_name="events",
        null=True,
        blank=True,
    )
    course = models.ForeignKey(
        EducationInstitutionCourse,
        on_delete=models.SET_NULL,
        related_name="events",
        null=True,
        blank=True,
    )
    class_session = models.ForeignKey(
        EducationInstitutionClassSession,
        on_delete=models.SET_NULL,
        related_name="events",
        null=True,
        blank=True,
    )
    event_type = models.CharField(
        max_length=24,
        choices=EducationInstitutionEventType.choices,
        default=EducationInstitutionEventType.EVENT,
    )
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    description = models.TextField(blank=True, default="")
    cover_image_url = models.URLField(max_length=2048, blank=True, default="")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    timezone_name = models.CharField(max_length=64, blank=True, default="UTC")
    delivery_mode = models.CharField(
        max_length=16,
        choices=EducationClassSessionMode.choices,
        default=EducationClassSessionMode.ONLINE,
    )
    location_text = models.CharField(max_length=255, blank=True, default="")
    meeting_url = models.URLField(blank=True, default="")
    seat_limit = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=EducationAcademicRecordStatus.choices,
        default=EducationAcademicRecordStatus.DRAFT,
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_event"
        ordering = ["starts_at", "-created_at"]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["program", "status"]),
            models.Index(fields=["course", "status"]),
            models.Index(fields=["class_session", "status"]),
            models.Index(fields=["institution", "event_type"]),
            models.Index(fields=["institution", "starts_at"]),
        ]

    def __str__(self):
        return self.title


class EducationInstitutionBroadcast(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="education_broadcasts",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="education_broadcasts_created",
    )
    broadcast_item = models.OneToOneField(
        BroadcastItem,
        on_delete=models.SET_NULL,
        related_name="education_broadcast_row",
        null=True,
        blank=True,
    )
    broadcast_kind = models.CharField(
        max_length=24,
        choices=EducationBroadcastKind.choices,
    )
    program = models.ForeignKey(
        EducationInstitutionProgram,
        on_delete=models.SET_NULL,
        related_name="broadcasts",
        null=True,
        blank=True,
    )
    course = models.ForeignKey(
        EducationInstitutionCourse,
        on_delete=models.CASCADE,
        related_name="broadcasts",
        null=True,
        blank=True,
    )
    lesson = models.ForeignKey(
        EducationInstitutionLesson,
        on_delete=models.CASCADE,
        related_name="broadcasts",
        null=True,
        blank=True,
    )
    class_session = models.ForeignKey(
        EducationInstitutionClassSession,
        on_delete=models.CASCADE,
        related_name="broadcasts",
        null=True,
        blank=True,
    )
    event = models.ForeignKey(
        EducationInstitutionEvent,
        on_delete=models.CASCADE,
        related_name="broadcasts",
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    description = models.TextField(blank=True, default="")
    cover_image_url = models.URLField(max_length=2048, blank=True, default="")
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    timezone_name = models.CharField(max_length=64, blank=True, default="UTC")
    seat_limit = models.PositiveIntegerField(null=True, blank=True)
    booking_enabled = models.BooleanField(default=False)
    price_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    price_currency = models.CharField(max_length=8, blank=True, default=KIS_COIN_CODE)
    status = models.CharField(
        max_length=16,
        choices=EducationBroadcastStatus.choices,
        default=EducationBroadcastStatus.PUBLISHED,
    )
    published_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(default=_default_expires_at)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_broadcast"
        ordering = ["-published_at", "-created_at"]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["institution", "broadcast_kind"]),
            models.Index(fields=["program", "status"]),
            models.Index(fields=["published_at"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        self.cover_image_url = normalize_image_payload(self.cover_image_url)
        super().save(*args, **kwargs)


class EducationInstitutionEnrollment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="enrollments",
    )
    broadcast = models.ForeignKey(
        EducationInstitutionBroadcast,
        on_delete=models.CASCADE,
        related_name="enrollments",
    )
    program = models.ForeignKey(
        EducationInstitutionProgram,
        on_delete=models.SET_NULL,
        related_name="enrollments",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="education_enrollments",
    )
    course = models.ForeignKey(
        EducationInstitutionCourse,
        on_delete=models.CASCADE,
        related_name="enrollments",
        null=True,
        blank=True,
    )
    lesson = models.ForeignKey(
        EducationInstitutionLesson,
        on_delete=models.CASCADE,
        related_name="enrollments",
        null=True,
        blank=True,
    )
    class_session = models.ForeignKey(
        EducationInstitutionClassSession,
        on_delete=models.CASCADE,
        related_name="enrollments",
        null=True,
        blank=True,
    )
    event = models.ForeignKey(
        EducationInstitutionEvent,
        on_delete=models.CASCADE,
        related_name="enrollments",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=16,
        choices=EducationEnrollmentStatus.choices,
        default=EducationEnrollmentStatus.PENDING,
    )
    enrolled_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_enrollment"
        unique_together = [("broadcast", "user")]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["broadcast", "status"]),
            models.Index(fields=["program", "status"]),
            models.Index(fields=["user", "status"]),
        ]


class EducationCourseReviewStatus(models.TextChoices):
    PENDING = "pending", "Pending review"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    HIDDEN = "hidden", "Hidden"


class EducationCourseQuestionStatus(models.TextChoices):
    OPEN = "open", "Open"
    ANSWERED = "answered", "Answered"
    HIDDEN = "hidden", "Hidden"
    CLOSED = "closed", "Closed"


class EducationCourseReview(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="course_reviews",
    )
    broadcast = models.ForeignKey(
        EducationInstitutionBroadcast,
        on_delete=models.CASCADE,
        related_name="course_reviews",
    )
    course = models.ForeignKey(
        EducationInstitutionCourse,
        on_delete=models.SET_NULL,
        related_name="reviews",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="education_course_reviews",
    )
    rating = models.PositiveSmallIntegerField(default=5)
    title = models.CharField(max_length=140, blank=True, default="")
    comment = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=EducationCourseReviewStatus.choices,
        default=EducationCourseReviewStatus.APPROVED,
        db_index=True,
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_course_review"
        unique_together = [("broadcast", "user")]
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["broadcast", "status"]),
            models.Index(fields=["course", "status"]),
            models.Index(fields=["user", "created_at"]),
        ]

    def save(self, *args, **kwargs):
        self.rating = min(5, max(1, int(self.rating or 1)))
        super().save(*args, **kwargs)


class EducationCourseQuestion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="course_questions",
    )
    broadcast = models.ForeignKey(
        EducationInstitutionBroadcast,
        on_delete=models.CASCADE,
        related_name="course_questions",
    )
    course = models.ForeignKey(
        EducationInstitutionCourse,
        on_delete=models.SET_NULL,
        related_name="questions",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="education_course_questions",
    )
    question = models.TextField()
    answer = models.TextField(blank=True, default="")
    answered_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="answered_education_course_questions",
        null=True,
        blank=True,
    )
    answered_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=EducationCourseQuestionStatus.choices,
        default=EducationCourseQuestionStatus.OPEN,
        db_index=True,
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_course_question"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["broadcast", "status"]),
            models.Index(fields=["course", "status"]),
            models.Index(fields=["user", "created_at"]),
        ]


class EducationInstitutionBooking(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="bookings",
    )
    broadcast = models.ForeignKey(
        EducationInstitutionBroadcast,
        on_delete=models.CASCADE,
        related_name="bookings",
    )
    program = models.ForeignKey(
        EducationInstitutionProgram,
        on_delete=models.SET_NULL,
        related_name="bookings",
        null=True,
        blank=True,
    )
    course = models.ForeignKey(
        EducationInstitutionCourse,
        on_delete=models.SET_NULL,
        related_name="bookings",
        null=True,
        blank=True,
    )
    class_session = models.ForeignKey(
        EducationInstitutionClassSession,
        on_delete=models.SET_NULL,
        related_name="bookings",
        null=True,
        blank=True,
    )
    event = models.ForeignKey(
        EducationInstitutionEvent,
        on_delete=models.SET_NULL,
        related_name="bookings",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="education_bookings",
    )
    status = models.CharField(
        max_length=24,
        choices=EducationBookingStatus.choices,
        default=EducationBookingStatus.PENDING,
    )
    seat_count = models.PositiveIntegerField(default=1)
    amount_cents = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=8, default=KIS_COIN_CODE)
    payment_method = models.CharField(max_length=32, blank=True, default="")
    wallet_transaction = models.ForeignKey(
        WalletTransaction,
        on_delete=models.SET_NULL,
        related_name="education_bookings",
        null=True,
        blank=True,
    )
    provider_credit_transaction = models.ForeignKey(
        WalletTransaction,
        on_delete=models.SET_NULL,
        related_name="education_booking_provider_payouts",
        null=True,
        blank=True,
    )
    reserved_at = models.DateTimeField(default=timezone.now)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    provider_completed_at = models.DateTimeField(null=True, blank=True)
    payer_satisfied_at = models.DateTimeField(null=True, blank=True)
    satisfaction_deadline = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_booking"
        unique_together = [("broadcast", "user")]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["broadcast", "status"]),
            models.Index(fields=["program", "status"]),
            models.Index(fields=["course", "status"]),
            models.Index(fields=["class_session", "status"]),
            models.Index(fields=["event", "status"]),
            models.Index(fields=["user", "status"]),
            models.Index(fields=["satisfaction_deadline"]),
        ]

    @property
    def complaint_window_expires(self):
        if not self.provider_completed_at:
            return None
        return self.provider_completed_at + timedelta(days=3)


FEATURE_DEFINITIONS = [
    {
        "slug": "broadcast_scheduling",
        "name": "Broadcast scheduling",
        "description": "Plan broadcasts ahead of time and let the system auto-launch.",
        "category": "Planning",
        "default_enabled": True,
    },
    {
        "slug": "live_ratings",
        "name": "Live ratings",
        "description": "Let viewers rate the broadcast in real time.",
        "category": "Engagement",
        "default_enabled": True,
    },
    {
        "slug": "automated_highlights",
        "name": "Automated highlights",
        "description": "AI-generated highlight reels after every broadcast.",
        "category": "Discovery",
        "default_enabled": True,
    },
    {
        "slug": "interactive_polling",
        "name": "Interactive polls",
        "description": "Embed polls that update instantly for the audience.",
        "category": "Engagement",
        "default_enabled": True,
    },
    {
        "slug": "collaborative_annotations",
        "name": "Collaborative annotations",
        "description": "Viewers and hosts can pin notes or callouts together.",
        "category": "Collaboration",
        "default_enabled": False,
    },
    {
        "slug": "layered_reactions",
        "name": "Layered reactions",
        "description": "Support stacked reactions and reaction heatmaps.",
        "category": "Engagement",
        "default_enabled": True,
    },
    {
        "slug": "monetized_pin",
        "name": "Monetized pin",
        "description": "Pin your product, link or CTA as a paid highlight.",
        "category": "Commerce",
        "default_enabled": False,
    },
    {
        "slug": "multi_host",
        "name": "Multi-host desk",
        "description": "Switch smoothly between hosts and moderators.",
        "category": "Production",
        "default_enabled": True,
    },
    {
        "slug": "audience_qna",
        "name": "Audience Q&A",
        "description": "Curate an expert Q&A queue for live broadcasts.",
        "category": "Engagement",
        "default_enabled": True,
    },
    {
        "slug": "live_translation",
        "name": "Live translation",
        "description": "Auto-translate captions for every viewer region.",
        "category": "Accessibility",
        "default_enabled": False,
    },
    {
        "slug": "custom_cta",
        "name": "Custom CTA",
        "description": "Embed programmable CTAs with tracking.",
        "category": "Commerce",
        "default_enabled": True,
    },
    {
        "slug": "private_replay",
        "name": "Private replay",
        "description": "Share replays only with approved viewers.",
        "category": "Privacy",
        "default_enabled": False,
    },
    {
        "slug": "broadcast_rankings",
        "name": "Broadcast rankings",
        "description": "Show your placement on a dynamic leaderboard.",
        "category": "Discovery",
        "default_enabled": True,
    },
    {
        "slug": "real_time_moderation",
        "name": "Real-time moderation",
        "description": "Auto-filter comments and highlight infractions.",
        "category": "Safety",
        "default_enabled": True,
    },
    {
        "slug": "adaptive_layout",
        "name": "Adaptive layout",
        "description": "Switch between cinematic, grid, and engagement layouts.",
        "category": "Production",
        "default_enabled": False,
    },
    {
        "slug": "lesson_mode",
        "name": "Lesson mode",
        "description": "Treat a broadcast as a structured lesson with swipeable modules.",
        "category": "Education",
        "default_enabled": True,
    },
    {
        "slug": "lesson_enrollment",
        "name": "Lesson enrollment automation",
        "description": "Auto-enroll viewers and track lesson-only memberships.",
        "category": "Learning",
        "default_enabled": True,
    },
    {
        "slug": "lesson_only_membership",
        "name": "Lesson-only membership",
        "description": "Grant access only to the lesson segment regardless of broader partner feeds.",
        "category": "Access",
        "default_enabled": False,
    },
    {
        "slug": "broadcast_dropkit",
        "name": "Broadcast drop kit",
        "description": "Drop digital kits or products tied to the broadcast in-view.",
        "category": "Commerce",
        "default_enabled": False,
    },
    {
        "slug": "ai_moderator_insights",
        "name": "AI moderator insights",
        "description": "Surface AI-curated moderation cues and risk signals mid-session.",
        "category": "Safety",
        "default_enabled": True,
    },
    {
        "slug": "co_host_scheduler",
        "name": "Co-host scheduler",
        "description": "Queue co-hosts and guests, then transition them live with confirmations.",
        "category": "Production",
        "default_enabled": False,
    },
    {
        "slug": "vaulted_replay",
        "name": "Vaulted replay",
        "description": "Store replays behind a vault that unlocks per membership or purchase.",
        "category": "Discovery",
        "default_enabled": False,
    },
    {
        "slug": "broadcast_storefront",
        "name": "Broadcast storefront",
        "description": "Show a curated storefront inside the broadcast feed for instant purchases.",
        "category": "Commerce",
        "default_enabled": True,
    },
    {
        "slug": "real_time_transcriptions",
        "name": "Real-time transcriptions",
        "description": "Deliver on-screen captions plus downloadable transcripts.",
        "category": "Accessibility",
        "default_enabled": True,
    },
    {
        "slug": "subscriber_only_comments",
        "name": "Subscriber-only comments",
        "description": "Restrict commenting to subscribers to keep chats premium.",
        "category": "Access",
        "default_enabled": False,
    },
    {
        "slug": "broadcast_rewards",
        "name": "Broadcast rewards",
        "description": "Issue credits or badges for attendees who complete an experience.",
        "category": "Engagement",
        "default_enabled": False,
    },
    {
        "slug": "viewer_progress_tracker",
        "name": "Viewer progress tracker",
        "description": "Track watched segments, highlight drop-in/out points, and resume.",
        "category": "Insights",
        "default_enabled": True,
    },
    {
        "slug": "auto_mixer",
        "name": "Auto mixer",
        "description": "Let the system balance audio/video feeds and add transitions.",
        "category": "Production",
        "default_enabled": False,
    },
    {
        "slug": "global_chat_rooms",
        "name": "Global chat rooms",
        "description": "Spawn regional chat rooms to pair with the broadcast view.",
        "category": "Community",
        "default_enabled": True,
    },
    {
        "slug": "audience_heatmap",
        "name": "Audience heatmap",
        "description": "Visualize who is watching and where engagement spikes happen.",
        "category": "Insights",
        "default_enabled": True,
    },
]


class BroadcastReaction(models.Model):
    id = models.BigAutoField(primary_key=True)
    broadcast_item = models.ForeignKey(
        BroadcastItem,
        on_delete=models.CASCADE,
        related_name="reactions",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="broadcast_reactions",
    )
    emoji = models.CharField(max_length=16, default="❤️")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "broadcast_reaction"
        unique_together = [("broadcast_item", "user")]
        indexes = [
            models.Index(fields=["broadcast_item", "user"]),
        ]


class BroadcastEngagementEventType(models.TextChoices):
    IMPRESSION = "impression", "Impression"
    VIEW = "view", "View"
    SHARE = "share", "Share"


class BroadcastEngagementEvent(models.Model):
    id = models.BigAutoField(primary_key=True)
    broadcast_item = models.ForeignKey(
        BroadcastItem,
        on_delete=models.CASCADE,
        related_name="engagement_events",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="broadcast_engagement_events",
    )
    event_type = models.CharField(
        max_length=32,
        choices=BroadcastEngagementEventType.choices,
        db_index=True,
    )
    platform = models.CharField(max_length=64, blank=True, default="")
    window_key = models.CharField(max_length=128, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "broadcast_engagement_event"
        unique_together = [("broadcast_item", "user", "event_type", "window_key")]
        indexes = [
            models.Index(
                fields=["broadcast_item", "event_type", "created_at"],
                name="broadcast_e_broadca_b8c59e_idx",
            ),
            models.Index(
                fields=["user", "event_type", "created_at"],
                name="broadcast_e_user_id_59ae71_idx",
            ),
        ]


class BroadcastFeature(models.Model):
    slug = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=64, blank=True)
    default_enabled = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcast_feature"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class BroadcastFeatureFlag(models.Model):
    feature = models.ForeignKey(
        BroadcastFeature,
        on_delete=models.CASCADE,
        related_name="flags",
    )
    channel = models.ForeignKey(
        Channel,
        on_delete=models.CASCADE,
        related_name="feature_flags",
    )
    broadcast_item = models.ForeignKey(
        BroadcastItem,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="feature_flags",
    )
    enabled = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcast_feature_flag"
        unique_together = [
            ("feature", "channel", "broadcast_item"),
        ]
        indexes = [
            models.Index(fields=["feature", "channel"]),
        ]


class BroadcastVideo(models.Model):
    VIDEO_TYPES = [
        ("short", "Short"),
        ("video", "Video"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    channel = models.ForeignKey(Channel, null=True, blank=True, on_delete=models.SET_NULL, related_name="videos")
    creator = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="videos")
    # Presigned S3 GET URLs carry a signature/credential/expiry query string
    # and comfortably exceed URLField's default max_length=200 — that
    # overflow used to be invisible because build_media_url() only ever
    # produced short same-domain /media/... links; now that it can return a
    # real presigned URL (private bucket), these need real headroom.
    video_url = models.URLField(max_length=2048)
    thumbnail_url = models.URLField(max_length=2048, blank=True)
    mime_type = models.CharField(max_length=256, blank=True)
    storage_path = models.CharField(max_length=1024, blank=True)
    type = models.CharField(max_length=16, choices=VIDEO_TYPES, default="video", db_index=True)
    duration_seconds = models.PositiveIntegerField(default=0)
    transcript_segments = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcast_video"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title


class BroadcastLesson(models.Model):
    LESSON_TYPES = [
        ("partner", "Partner"),
        ("community", "Community"),
        ("global", "Global"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    broadcast_item = models.OneToOneField(
        BroadcastItem,
        on_delete=models.CASCADE,
        related_name="lesson",
    )
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True)
    lesson_url = models.URLField(blank=True)
    lesson_type = models.CharField(max_length=16, choices=LESSON_TYPES, default="global", db_index=True)
    partner = models.ForeignKey(
        Partner,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lessons",
    )
    community = models.ForeignKey(
        Community,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lessons",
    )
    public_info = models.JSONField(default=dict, blank=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    price_cents = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=10, default="USD")
    is_public = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcast_lesson"
        indexes = [
            models.Index(fields=["lesson_type"]),
            models.Index(fields=["partner"]),
            models.Index(fields=["community"]),
        ]

    def __str__(self) -> str:
        return self.title


class LessonEnrollmentStatus(models.TextChoices):
    ENROLLED = "enrolled", "Enrolled"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class LessonEnrollment(models.Model):
    id = models.BigAutoField(primary_key=True)
    lesson = models.ForeignKey(
        BroadcastLesson,
        on_delete=models.CASCADE,
        related_name="enrollments",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="lesson_enrollments",
    )
    status = models.CharField(
        max_length=16,
        choices=LessonEnrollmentStatus.choices,
        default=LessonEnrollmentStatus.ENROLLED,
    )
    enrolled_at = models.DateTimeField(default=timezone.now)
    partner_membership_id = models.BigIntegerField(null=True, blank=True)
    community_membership_id = models.BigIntegerField(null=True, blank=True)

    class Meta:
        db_table = "lesson_enrollment"
        unique_together = [("lesson", "user")]
        indexes = [
            models.Index(fields=["lesson", "user"]),
            models.Index(fields=["user", "status"]),
        ]


# ── User Content Playlists (cross-device, server-persisted video playlists) ──

class UserContentPlaylist(models.Model):
    class Visibility(models.TextChoices):
        PUBLIC = "public", "Public"
        UNLISTED = "unlisted", "Unlisted"
        PRIVATE = "private", "Private"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="content_playlists")
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True, default="")
    visibility = models.CharField(
        max_length=16,
        choices=Visibility.choices,
        default=Visibility.PRIVATE,
    )
    sort_order = models.PositiveIntegerField(default=0)
    is_system = models.BooleanField(default=False)
    system_key = models.CharField(max_length=32, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_content_playlist"
        ordering = ["sort_order", "-created_at"]
        indexes = [
            models.Index(fields=["user", "visibility"]),
            models.Index(fields=["user", "system_key"]),
            models.Index(fields=["user", "sort_order"]),
        ]

    def __str__(self):
        return self.title


class UserContentPlaylistItem(models.Model):
    id = models.BigAutoField(primary_key=True)
    playlist = models.ForeignKey(UserContentPlaylist, on_delete=models.CASCADE, related_name="items")
    content = models.ForeignKey(ChannelContent, on_delete=models.CASCADE, related_name="user_playlist_items")
    sort_order = models.PositiveIntegerField(default=0)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "user_content_playlist_item"
        unique_together = [("playlist", "content")]
        ordering = ["sort_order", "-added_at"]
        indexes = [
            models.Index(fields=["playlist", "sort_order"]),
            models.Index(fields=["content", "added_at"]),
        ]


# ── Live Stream Cameras (multi-camera source management) ──────────────────────

class LiveStreamCamera(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    stream = models.ForeignKey(
        "ChannelLiveStream",
        on_delete=models.CASCADE,
        related_name="cameras",
    )
    source_id = models.CharField(max_length=128, blank=True, default="")
    label = models.CharField(max_length=128)
    facing = models.CharField(max_length=16, blank=True, default="")
    is_active = models.BooleanField(default=False)
    is_external = models.BooleanField(default=False)
    thumbnail_url = models.URLField(max_length=1024, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "live_stream_camera"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["stream", "is_active"]),
        ]

    def __str__(self):
        return f"{self.label} ({self.stream_id})"


# ── Live Chat Messages ────────────────────────────────────────────────────────

class LiveChatMessage(models.Model):
    id = models.BigAutoField(primary_key=True)
    stream = models.ForeignKey(
        "ChannelLiveStream", on_delete=models.CASCADE, related_name="chat_messages"
    )
    user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    display_name = models.CharField(max_length=150, blank=True, default="")
    avatar_url = models.URLField(max_length=1024, blank=True, default="")
    message = models.TextField(max_length=1000)
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "live_chat_message"
        indexes = [
            models.Index(fields=["stream", "created_at"], name="live_chat_stream_time_idx"),
        ]


# ── Channel Membership Tiers ──────────────────────────────────────────────────

class ChannelMembershipTier(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.ForeignKey(
        BroadcastChannel, on_delete=models.CASCADE, related_name="membership_tiers"
    )
    title = models.CharField(max_length=100)
    description = models.TextField(blank=True, default="")
    price_cents = models.PositiveIntegerField(default=0)
    currency = models.CharField(max_length=3, default="USD")
    perks = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_membership_tier"
        ordering = ["sort_order", "price_cents"]
        indexes = [
            models.Index(fields=["channel", "is_active"], name="membership_tier_channel_idx"),
        ]


class ChannelMembership(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="channel_memberships"
    )
    tier = models.ForeignKey(
        ChannelMembershipTier, on_delete=models.CASCADE, related_name="memberships"
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )
    started_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    payment_reference = models.CharField(max_length=256, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "channel_membership"
        unique_together = [("user", "tier")]
        indexes = [
            models.Index(fields=["user", "status"], name="membership_user_status_idx"),
            models.Index(fields=["tier", "status"], name="membership_tier_status_idx"),
        ]


# ─── Super Thanks (video tip) ─────────────────────────────────────────────────
class ChannelContentTip(models.Model):
    """Super Thanks: tip on a published video."""
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        REFUNDED = "refunded", "Refunded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    content = models.ForeignKey(ChannelContent, on_delete=models.CASCADE, related_name="tips")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="channel_content_tips")
    amount_cents = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="USD")
    message = models.CharField(max_length=150, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    payment_reference = models.CharField(max_length=256, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_content_tip"
        indexes = [
            models.Index(fields=["content", "status"]),
            models.Index(fields=["user", "status"]),
        ]


# ─── Super Chat (live stream tip) ─────────────────────────────────────────────
class ChannelLiveStreamTip(models.Model):
    """Super Chat: tip during a live stream with a highlighted message."""
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        REFUNDED = "refunded", "Refunded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    live_stream = models.ForeignKey(ChannelLiveStream, on_delete=models.CASCADE, related_name="tips")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="live_stream_tips")
    amount_cents = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="USD")
    message = models.CharField(max_length=150, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    is_pinned = models.BooleanField(default=False)
    pinned_until = models.DateTimeField(null=True, blank=True)
    payment_reference = models.CharField(max_length=256, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_live_stream_tip"
        indexes = [
            models.Index(fields=["live_stream", "status"]),
            models.Index(fields=["live_stream", "is_pinned"]),
        ]


# ─── Channel Monetization ─────────────────────────────────────────────────────
class ChannelMonetizationSettings(models.Model):
    class PayoutSchedule(models.TextChoices):
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"
        ON_REQUEST = "on_request", "On Request"

    channel = models.OneToOneField(
        BroadcastChannel, on_delete=models.CASCADE, related_name="monetization_settings"
    )
    tips_enabled = models.BooleanField(default=False)
    membership_enabled = models.BooleanField(default=False)
    ad_revenue_enabled = models.BooleanField(default=False)
    revenue_share_pct = models.DecimalField(max_digits=5, decimal_places=2, default=70)
    payout_threshold_cents = models.PositiveIntegerField(default=10000)
    payout_schedule = models.CharField(
        max_length=16, choices=PayoutSchedule.choices, default=PayoutSchedule.ON_REQUEST
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_monetization_settings"


class ChannelPayoutRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        REJECTED = "rejected", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.ForeignKey(BroadcastChannel, on_delete=models.CASCADE, related_name="payout_requests")
    requested_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="channel_payout_requests")
    amount_cents = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="USD")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    payment_method_ref = models.CharField(max_length=256, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    processed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_payout_request"
        indexes = [
            models.Index(fields=["channel", "status"]),
            models.Index(fields=["requested_by", "status"]),
        ]


# ─── Watch events + heatmap ───────────────────────────────────────────────────
class ChannelContentWatchEvent(models.Model):
    """Aggregate watch event per viewing session — powers recommendations."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    content = models.ForeignKey(ChannelContent, on_delete=models.CASCADE, related_name="watch_events")
    user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="content_watch_events"
    )
    session_id = models.CharField(max_length=64, blank=True, default="")
    watch_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    duration_watched_seconds = models.PositiveIntegerField(default=0)
    source = models.CharField(max_length=32, default="direct", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "channel_content_watch_event"
        indexes = [
            models.Index(fields=["content", "user"]),
            models.Index(fields=["content", "created_at"]),
            models.Index(fields=["user", "source"]),
        ]


class ChannelContentWatchSegment(models.Model):
    """Per-second segment view counts — powers the most replayed heatmap."""
    content = models.ForeignKey(ChannelContent, on_delete=models.CASCADE, related_name="watch_segments")
    segment_start_seconds = models.PositiveIntegerField()
    segment_end_seconds = models.PositiveIntegerField()
    view_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "channel_content_watch_segment"
        unique_together = [("content", "segment_start_seconds")]
        indexes = [
            models.Index(fields=["content", "view_count"]),
        ]


# ─── Multi-language audio tracks ─────────────────────────────────────────────
class ChannelContentAudioTrack(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    content = models.ForeignKey(ChannelContent, on_delete=models.CASCADE, related_name="audio_tracks")
    language_code = models.CharField(max_length=10)
    label = models.CharField(max_length=80)
    url = models.URLField()
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "channel_content_audio_track"
        unique_together = [("content", "language_code")]


# ─── Geo-restrictions ─────────────────────────────────────────────────────────
class ChannelContentGeoRestriction(models.Model):
    class RestrictionType(models.TextChoices):
        ALLOW = "allow", "Allow only listed countries"
        BLOCK = "block", "Block listed countries"

    content = models.OneToOneField(
        ChannelContent, on_delete=models.CASCADE, related_name="geo_restriction"
    )
    restriction_type = models.CharField(
        max_length=8, choices=RestrictionType.choices, default=RestrictionType.BLOCK
    )
    countries = models.JSONField(
        default=list, blank=True, help_text="List of ISO 3166-1 alpha-2 country codes."
    )

    class Meta:
        db_table = "channel_content_geo_restriction"


# ─── Premieres ────────────────────────────────────────────────────────────────
class ChannelContentPremiere(models.Model):
    """Premiere: scheduled video with a countdown page and pre-chat lobby."""
    content = models.OneToOneField(ChannelContent, on_delete=models.CASCADE, related_name="premiere")
    trailer_url = models.URLField(blank=True, default="")
    pre_chat_opens_at = models.DateTimeField(null=True, blank=True)
    lobby_conversation = models.ForeignKey(
        "chat.Conversation",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="premiere_lobby",
    )
    viewer_count = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_content_premiere"


# ─── Co-streaming guests ──────────────────────────────────────────────────────
class ChannelLiveStreamGuest(models.Model):
    class Role(models.TextChoices):
        COHOST = "cohost", "Co-host"
        GUEST = "guest", "Guest"

    class Status(models.TextChoices):
        INVITED = "invited", "Invited"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        ACTIVE = "active", "Active"
        REMOVED = "removed", "Removed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    live_stream = models.ForeignKey(ChannelLiveStream, on_delete=models.CASCADE, related_name="guests")
    user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="live_stream_guest_slots"
    )
    email = models.EmailField(blank=True, default="")
    invite_token = models.CharField(max_length=64, unique=True)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.GUEST)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.INVITED, db_index=True)
    invited_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="live_stream_guest_invitations")
    accepted_at = models.DateTimeField(null=True, blank=True)
    removed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_live_stream_guest"
        indexes = [
            models.Index(fields=["live_stream", "status"]),
            models.Index(fields=["user", "status"]),
        ]

    def save(self, *args, **kwargs):
        if not self.invite_token:
            import secrets as _secrets
            self.invite_token = _secrets.token_urlsafe(48)
        super().save(*args, **kwargs)


# ─── Transcripts / auto-captions ─────────────────────────────────────────────
class ChannelContentTranscript(models.Model):
    class Source(models.TextChoices):
        MANUAL = "manual", "Manual"
        AUTO = "auto", "Auto-generated"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    content = models.ForeignKey(ChannelContent, on_delete=models.CASCADE, related_name="transcripts")
    language_code = models.CharField(max_length=10, default="en")
    source = models.CharField(max_length=8, choices=Source.choices, default=Source.AUTO)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    text_plain = models.TextField(blank=True, default="")
    vtt_url = models.URLField(blank=True, default="")
    provider = models.CharField(max_length=32, blank=True, default="")
    provider_job_id = models.CharField(max_length=128, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_content_transcript"
        unique_together = [("content", "language_code", "source")]
        indexes = [
            models.Index(fields=["content", "status"]),
        ]


# ─── Product tagging on videos ────────────────────────────────────────────────
class ChannelContentProduct(models.Model):
    """Tag a commerce product at a specific timestamp inside a video."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    content = models.ForeignKey(ChannelContent, on_delete=models.CASCADE, related_name="product_tags")
    product_id = models.CharField(max_length=128, db_index=True)
    product_url = models.URLField(blank=True, default="")
    product_title = models.CharField(max_length=220, blank=True, default="")
    thumbnail_url = models.URLField(blank=True, default="")
    price_display = models.CharField(max_length=32, blank=True, default="")
    timestamp_seconds = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "channel_content_product"
        indexes = [
            models.Index(fields=["content", "timestamp_seconds"]),
            models.Index(fields=["product_id"]),
        ]


# ─── SimulCast RTMP targets ───────────────────────────────────────────────────
class ChannelLiveStreamTarget(models.Model):
    """Additional RTMP destinations for a live stream (simulcast)."""
    class Platform(models.TextChoices):
        YOUTUBE = "youtube", "YouTube"
        TWITCH = "twitch", "Twitch"
        FACEBOOK = "facebook", "Facebook"
        INSTAGRAM = "instagram", "Instagram"
        TIKTOK = "tiktok", "TikTok"
        CUSTOM = "custom", "Custom"

    class Status(models.TextChoices):
        IDLE = "idle", "Idle"
        STREAMING = "streaming", "Streaming"
        ERROR = "error", "Error"
        ENDED = "ended", "Ended"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    live_stream = models.ForeignKey(ChannelLiveStream, on_delete=models.CASCADE, related_name="simulcast_targets")
    platform = models.CharField(max_length=16, choices=Platform.choices, default=Platform.CUSTOM)
    label = models.CharField(max_length=80, blank=True, default="")
    rtmp_url = models.CharField(max_length=512)
    stream_key = models.CharField(max_length=256, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.IDLE, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_live_stream_target"
        indexes = [
            models.Index(fields=["live_stream", "status"]),
        ]


# ─── Gift Memberships ─────────────────────────────────────────────────────────
class ChannelMembershipGift(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        REDEEMED = "redeemed", "Redeemed"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tier = models.ForeignKey(ChannelMembershipTier, on_delete=models.CASCADE, related_name="gifts")
    gifter = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sent_membership_gifts")
    recipient = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="received_membership_gifts"
    )
    recipient_email = models.EmailField(blank=True, default="")
    message = models.CharField(max_length=300, blank=True, default="")
    redeem_token = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    redeemed_at = models.DateTimeField(null=True, blank=True)
    payment_reference = models.CharField(max_length=256, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_membership_gift"
        indexes = [
            models.Index(fields=["tier", "status"]),
            models.Index(fields=["gifter", "status"]),
            models.Index(fields=["recipient", "status"]),
        ]

    def save(self, *args, **kwargs):
        if not self.redeem_token:
            import secrets as _s
            self.redeem_token = _s.token_urlsafe(48)
        super().save(*args, **kwargs)


# ─── Content ID / Copyright Claims ───────────────────────────────────────────
class ChannelContentCopyrightClaim(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        MATCHED = "matched", "Matched"
        DISPUTED = "disputed", "Under dispute"
        RESOLVED = "resolved", "Resolved"
        DISMISSED = "dismissed", "Dismissed"

    class ClaimType(models.TextChoices):
        AUDIO = "audio", "Audio match"
        VIDEO = "video", "Video match"
        MANUAL = "manual", "Manual claim"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    content = models.ForeignKey(ChannelContent, on_delete=models.CASCADE, related_name="copyright_claims")
    claimant_channel = models.ForeignKey(
        BroadcastChannel, null=True, blank=True, on_delete=models.SET_NULL, related_name="filed_copyright_claims"
    )
    claimant_name = models.CharField(max_length=220, blank=True, default="")
    claim_type = models.CharField(max_length=8, choices=ClaimType.choices, default=ClaimType.MANUAL)
    asset_fingerprint = models.CharField(max_length=128, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    dispute_reason = models.TextField(blank=True, default="")
    resolution_notes = models.TextField(blank=True, default="")
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_content_copyright_claim"
        indexes = [
            models.Index(fields=["content", "status"]),
            models.Index(fields=["claimant_channel", "status"]),
        ]


# ─── Audience Demographics ────────────────────────────────────────────────────
class ChannelContentDemographicSnapshot(models.Model):
    """Daily demographic aggregation — populated by analytics pipeline or watch events."""
    class AgeBucket(models.TextChoices):
        AGE_13_17 = "13-17", "13–17"
        AGE_18_24 = "18-24", "18–24"
        AGE_25_34 = "25-34", "25–34"
        AGE_35_44 = "35-44", "35–44"
        AGE_45_54 = "45-54", "45–54"
        AGE_55_PLUS = "55+", "55+"
        UNKNOWN = "unknown", "Unknown"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.ForeignKey(
        BroadcastChannel, null=True, blank=True, on_delete=models.CASCADE, related_name="demographic_snapshots"
    )
    content = models.ForeignKey(
        ChannelContent, null=True, blank=True, on_delete=models.CASCADE, related_name="demographic_snapshots"
    )
    snapshot_date = models.DateField(db_index=True)
    age_bucket = models.CharField(max_length=8, choices=AgeBucket.choices, default=AgeBucket.UNKNOWN)
    country_code = models.CharField(max_length=2, blank=True, default="")
    view_count = models.PositiveIntegerField(default=0)
    watch_time_seconds = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "channel_content_demographic_snapshot"
        indexes = [
            models.Index(fields=["channel", "snapshot_date"]),
            models.Index(fields=["content", "snapshot_date"]),
            models.Index(fields=["snapshot_date", "country_code"]),
        ]


# ─── Ad Delivery ─────────────────────────────────────────────────────────────
class ChannelAdCampaign(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        ENDED = "ended", "Ended"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    advertiser_channel = models.ForeignKey(
        BroadcastChannel, on_delete=models.CASCADE, related_name="ad_campaigns"
    )
    title = models.CharField(max_length=220)
    budget_cents = models.PositiveIntegerField(default=0)
    spent_cents = models.PositiveIntegerField(default=0)
    currency = models.CharField(max_length=3, default="USD")
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    target_content_types = models.JSONField(default=list, blank=True)
    target_countries = models.JSONField(default=list, blank=True)
    target_age_buckets = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.DRAFT, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_ad_campaign"
        indexes = [
            models.Index(fields=["advertiser_channel", "status"]),
            models.Index(fields=["status", "start_date", "end_date"]),
        ]


class ChannelAdSlot(models.Model):
    """Ad placement configuration on a piece of content."""
    class Placement(models.TextChoices):
        PRE_ROLL = "pre_roll", "Pre-roll"
        MID_ROLL = "mid_roll", "Mid-roll"
        POST_ROLL = "post_roll", "Post-roll"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    content = models.ForeignKey(ChannelContent, on_delete=models.CASCADE, related_name="ad_slots")
    campaign = models.ForeignKey(
        ChannelAdCampaign, null=True, blank=True, on_delete=models.SET_NULL, related_name="ad_slots"
    )
    placement = models.CharField(max_length=12, choices=Placement.choices, default=Placement.PRE_ROLL)
    timestamp_seconds = models.PositiveIntegerField(default=0)
    is_skippable = models.BooleanField(default=True)
    skip_after_seconds = models.PositiveIntegerField(default=5)
    ad_media_url = models.URLField(blank=True, default="")
    click_url = models.URLField(blank=True, default="")
    impression_count = models.PositiveIntegerField(default=0)
    skip_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "channel_ad_slot"
        indexes = [
            models.Index(fields=["content", "placement"]),
            models.Index(fields=["campaign", "placement"]),
        ]


class ChannelAdImpression(models.Model):
    """Records a single ad impression/view event."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slot = models.ForeignKey(ChannelAdSlot, on_delete=models.CASCADE, related_name="impressions")
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="ad_impressions")
    watched_seconds = models.PositiveIntegerField(default=0)
    skipped = models.BooleanField(default=False)
    clicked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "channel_ad_impression"
        indexes = [
            models.Index(fields=["slot", "created_at"]),
            models.Index(fields=["user", "created_at"]),
        ]


# ─── Watch Queue / Up Next ────────────────────────────────────────────────────
class ChannelContentQueue(models.Model):
    """Per-user watch queue (Up Next list)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="content_queue")
    content = models.ForeignKey(ChannelContent, on_delete=models.CASCADE, related_name="queued_by")
    position = models.PositiveIntegerField(default=0)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "channel_content_queue"
        unique_together = [("user", "content")]
        ordering = ["position", "added_at"]
        indexes = [
            models.Index(fields=["user", "position"]),
        ]


# ─── Auto-chapter Suggestions ─────────────────────────────────────────────────
class ChannelContentAutoChapterSuggestion(models.Model):
    """AI/NLP-generated chapter timestamp suggestions for a video."""
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"
        APPLIED = "applied", "Applied to chapters"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    content = models.ForeignKey(ChannelContent, on_delete=models.CASCADE, related_name="auto_chapter_suggestions")
    transcript = models.ForeignKey(
        ChannelContentTranscript, null=True, blank=True, on_delete=models.SET_NULL, related_name="auto_chapter_suggestions"
    )
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PENDING, db_index=True)
    suggestions = models.JSONField(
        default=list, blank=True, help_text='[{"title": str, "start_seconds": int}]'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_content_auto_chapter_suggestion"
        indexes = [
            models.Index(fields=["content", "status"]),
        ]


class ChannelContentTrafficSource(models.Model):
    class SourceType(models.TextChoices):
        SEARCH = "search", "Search"
        BROWSE = "browse", "Browse"
        EXTERNAL = "external", "External"
        DIRECT = "direct", "Direct"
        RECOMMENDED = "recommended", "Recommended"
        NOTIFICATION = "notification", "Notification"
        PLAYLIST = "playlist", "Playlist"
        CHANNEL_PAGE = "channel_page", "Channel Page"
        UNKNOWN = "unknown", "Unknown"

    id = models.BigAutoField(primary_key=True)
    content = models.ForeignKey(ChannelContent, on_delete=models.CASCADE, related_name="traffic_sources")
    date = models.DateField(db_index=True)
    source_type = models.CharField(max_length=24, choices=SourceType.choices, default=SourceType.UNKNOWN)
    view_count = models.PositiveIntegerField(default=0)
    watch_time_seconds = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_content_traffic_source"
        unique_together = [("content", "date", "source_type")]
        indexes = [
            models.Index(fields=["content", "date"]),
        ]


class ChannelKeywordFilter(models.Model):
    class FilterType(models.TextChoices):
        BLOCK = "block", "Block (auto-remove)"
        HOLD = "hold", "Hold for review"
        FLAG = "flag", "Flag only"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.ForeignKey(BroadcastChannel, on_delete=models.CASCADE, related_name="keyword_filters")
    keyword = models.CharField(max_length=100)
    filter_type = models.CharField(max_length=8, choices=FilterType.choices, default=FilterType.HOLD)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_keyword_filters")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_keyword_filter"
        unique_together = [("channel", "keyword")]
        indexes = [
            models.Index(fields=["channel", "is_active"]),
        ]


class ChannelHomepageShelf(models.Model):
    class ShelfType(models.TextChoices):
        UPLOADS = "uploads", "Uploads"
        PLAYLISTS = "playlists", "Playlists"
        LIVE = "live", "Live"
        SHORTS = "shorts", "Shorts"
        FEATURED = "featured", "Featured"
        CUSTOM = "custom", "Custom"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.ForeignKey(BroadcastChannel, on_delete=models.CASCADE, related_name="homepage_shelves")
    title = models.CharField(max_length=120)
    shelf_type = models.CharField(max_length=16, choices=ShelfType.choices, default=ShelfType.UPLOADS)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_homepage_shelf"
        ordering = ["sort_order"]
        indexes = [
            models.Index(fields=["channel", "is_active"]),
        ]


class ChannelHomepageShelfItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shelf = models.ForeignKey(ChannelHomepageShelf, on_delete=models.CASCADE, related_name="items")
    content = models.ForeignKey(ChannelContent, null=True, blank=True, on_delete=models.CASCADE, related_name="shelf_items")
    playlist = models.ForeignKey(BroadcastPlaylist, null=True, blank=True, on_delete=models.CASCADE, related_name="shelf_items")
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "channel_homepage_shelf_item"
        ordering = ["sort_order"]
        indexes = [
            models.Index(fields=["shelf", "sort_order"]),
        ]


class ChannelCategory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=80, unique=True)
    description = models.TextField(blank=True, default="")
    icon_name = models.CharField(max_length=80, blank=True, default="")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="subcategories")
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "channel_category"
        ordering = ["sort_order", "name"]


class ChannelContentFingerprint(models.Model):
    class Algorithm(models.TextChoices):
        SHA256 = "sha256", "SHA-256 (file hash)"
        PERCEPTUAL = "perceptual", "Perceptual (visual)"
        AUDIO = "audio", "Audio fingerprint"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        INDEXED = "indexed", "Indexed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    content = models.ForeignKey(ChannelContent, on_delete=models.CASCADE, related_name="fingerprints")
    algorithm = models.CharField(max_length=16, choices=Algorithm.choices, default=Algorithm.SHA256)
    fingerprint_hash = models.CharField(max_length=256, db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_content_fingerprint"
        unique_together = [("content", "algorithm")]
        indexes = [
            models.Index(fields=["fingerprint_hash", "algorithm"]),
        ]


class ChannelFingerprintMatch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_fingerprint = models.ForeignKey(ChannelContentFingerprint, on_delete=models.CASCADE, related_name="matches_as_source")
    matched_fingerprint = models.ForeignKey(ChannelContentFingerprint, on_delete=models.CASCADE, related_name="matches_as_match")
    similarity_score = models.FloatField(default=1.0)
    claim = models.ForeignKey(ChannelContentCopyrightClaim, null=True, blank=True, on_delete=models.SET_NULL, related_name="fingerprint_matches")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "channel_fingerprint_match"
        unique_together = [("source_fingerprint", "matched_fingerprint")]
