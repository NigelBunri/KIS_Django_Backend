from decimal import Decimal

from django.urls import reverse
from rest_framework import serializers

from apps.broadcasts.media_utils import build_media_url, ensure_local_thumbnail
from apps.broadcasts.health_engine_policy import is_service_medium_allowed
from apps.core.money import parse_decimal_amount
from common.media_urls import absolutize_backend_media

from .models import (
    BroadcastChannel,
    BroadcastChannelRole,
    BroadcastChannelSubscription,
    BroadcastFeature,
    BroadcastItem,
    BroadcastPlaylist,
    BroadcastPlaylistItem,
    BroadcastSourceType,
    ChannelAnalyticsDailyRollup,
    BroadcastVideo,
    ChannelLiveStream,
    ChannelContent,
    ChannelContentAsset,
    ChannelContentComment,
    ChannelCommentReaction,
    ChannelContentClip,
    ChannelContentChapter,
    ChannelContentSubtitle,
    ChannelContentEndScreen,
    ChannelContentCard,
    ChannelActivityEvent,
    ChannelLivePoll,
    ChannelLivePollVote,
    ChannelLiveQA,
    ChannelLiveQAQuestion,
    ChannelLiveQAQuestionUpvote,
    CommentCreatorHeart,
    ChannelWatchHistorySettings,
    ChannelModerationRecord,
    BroadcastLesson,
    LessonEnrollment,
    EducationInstitutionAssessment,
    EducationInstitutionAssessmentQuestion,
    EducationInstitutionAssessmentOption,
    EducationInstitutionAssessmentSubmission,
    EducationInstitutionAssessmentResponse,
    EducationInstitutionAssessmentResponseOption,
    EducationInstitutionBroadcast,
    EducationInstitutionBooking,
    EducationInstitutionEnrollment,
    EducationCourseQuestion,
    EducationCourseReview,
    EducationInstitutionEvent,
    EducationInstitutionStaffAssignment,
    EducationInstitutionProgram,
    EducationInstitutionCourse,
    EducationInstitutionCourseAccessRequest,
    EducationInstitutionCourseModule,
    EducationInstitutionCourseModuleItem,
    EducationInstitutionLesson,
    EducationInstitutionClassSession,
    EducationInstitutionMaterial,
    EducationInstitution,
    EducationInstitutionMembership,
    EducationProfile,
    EducationProfileCourse,
    EducationProfileModule,
    EducationProfileRole,
    EducationProfileRoleAssignment,
    Medium,
    Service,
    ServiceMediumMap,
    ChannelContentTip,
    ChannelLiveStreamTip,
    ChannelMonetizationSettings,
    ChannelPayoutRequest,
    ChannelContentWatchEvent,
    ChannelContentWatchSegment,
    ChannelContentAudioTrack,
    ChannelContentGeoRestriction,
    ChannelContentPremiere,
    ChannelLiveStreamGuest,
    ChannelContentTranscript,
    ChannelContentProduct,
    ChannelLiveStreamTarget,
    ChannelMembershipGift,
    ChannelContentCopyrightClaim,
    ChannelContentDemographicSnapshot,
    ChannelAdCampaign,
    ChannelAdSlot,
    ChannelAdImpression,
    ChannelContentQueue,
    ChannelContentAutoChapterSuggestion,
    ChannelContentTrafficSource,
    ChannelKeywordFilter,
    ChannelHomepageShelf,
    ChannelHomepageShelfItem,
    ChannelCategory,
    ChannelContentFingerprint,
    ChannelFingerprintMatch,
)
from apps.partners.models import Partner
from apps.communities.models import Community
from apps.accounts.models import User


class LenientDecimalField(serializers.DecimalField):
    def to_internal_value(self, data):
        if data in (None, "") and self.allow_null:
            return None
        parsed = parse_decimal_amount(data)
        if parsed is None:
            self.fail("invalid")
        return super().to_internal_value(str(parsed))


def _viewer_channel_role(channel: BroadcastChannel, user) -> str:
    if not getattr(user, "is_authenticated", False):
        return ""
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return "staff"
    if getattr(channel, "owner_user_id", None) and str(channel.owner_user_id) == str(getattr(user, "id", "")):
        return "owner"
    role = (
        BroadcastChannelRole.objects.filter(channel=channel, user=user)
        .order_by("created_at")
        .values_list("role", flat=True)
        .first()
    )
    return str(role or "")


class BroadcastChannelSummarySerializer(serializers.ModelSerializer):
    is_subscribed = serializers.SerializerMethodField()
    viewer_role = serializers.SerializerMethodField()
    is_broadcast = serializers.SerializerMethodField()
    broadcast_id = serializers.SerializerMethodField()

    class Meta:
        model = BroadcastChannel
        fields = [
            "id",
            "handle",
            "display_name",
            "description",
            "avatar_url",
            "banner_url",
            "country",
            "language",
            "category",
            "verification_badges",
            "is_public",
            "is_verified",
            "is_broadcast",
            "broadcast_id",
            "subscriber_count",
            "content_count",
            "is_subscribed",
            "viewer_role",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_is_subscribed(self, obj: BroadcastChannel) -> bool:
        user = self.context.get("user") or getattr(self.context.get("request"), "user", None)
        if not getattr(user, "is_authenticated", False):
            return False
        return BroadcastChannelSubscription.objects.filter(channel=obj, user=user).exists()

    def get_viewer_role(self, obj: BroadcastChannel) -> str:
        user = self.context.get("user") or getattr(self.context.get("request"), "user", None)
        return _viewer_channel_role(obj, user)

    def _active_broadcast(self, obj: BroadcastChannel):
        return BroadcastItem.objects.filter(
            source_type=BroadcastSourceType.BROADCAST_CHANNEL,
            source_id=str(obj.id),
            is_deleted=False,
        ).only("id").first()

    def get_is_broadcast(self, obj: BroadcastChannel) -> bool:
        return self._active_broadcast(obj) is not None

    def get_broadcast_id(self, obj: BroadcastChannel) -> str:
        item = self._active_broadcast(obj)
        return str(item.id) if item else ""


class BroadcastChannelDetailSerializer(BroadcastChannelSummarySerializer):
    links = serializers.JSONField(read_only=True)
    branding = serializers.JSONField(read_only=True)
    settings = serializers.SerializerMethodField()

    class Meta(BroadcastChannelSummarySerializer.Meta):
        fields = BroadcastChannelSummarySerializer.Meta.fields + [
            "links",
            "branding",
            "settings",
        ]

    def get_settings(self, obj: BroadcastChannel) -> dict:
        user = self.context.get("user") or getattr(self.context.get("request"), "user", None)
        role = _viewer_channel_role(obj, user)
        if role in {"owner", "manager", "staff"}:
            return obj.settings or {}
        return {}


class BroadcastChannelSubscriptionSerializer(serializers.ModelSerializer):
    channel = BroadcastChannelSummarySerializer(read_only=True)
    channel_id = serializers.UUIDField(write_only=True, required=False)

    class Meta:
        model = BroadcastChannelSubscription
        fields = [
            "id",
            "channel",
            "channel_id",
            "notifications",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "channel", "created_at", "updated_at"]


class BroadcastPlaylistSerializer(serializers.ModelSerializer):
    channel = BroadcastChannelSummarySerializer(read_only=True)
    channel_id = serializers.UUIDField(write_only=True, required=False)

    class Meta:
        model = BroadcastPlaylist
        fields = [
            "id",
            "channel",
            "channel_id",
            "title",
            "description",
            "visibility",
            "sort_order",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "channel", "created_at", "updated_at"]


class ChannelContentAssetSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelContentAsset
        fields = [
            "id",
            "asset_type",
            "url",
            "mime_type",
            "size_bytes",
            "width",
            "height",
            "duration_seconds",
            "thumbnail_url",
            "caption",
            "sort_order",
            "processing_status",
            "metadata",
            "created_at",
        ]
        read_only_fields = fields


class ChannelContentListSerializer(serializers.ModelSerializer):
    channel = BroadcastChannelSummarySerializer(read_only=True)
    first_asset = serializers.SerializerMethodField()
    description_preview = serializers.SerializerMethodField()
    text_plain_preview = serializers.SerializerMethodField()
    engagement_counts = serializers.SerializerMethodField()
    is_broadcast = serializers.SerializerMethodField()
    broadcast_id = serializers.SerializerMethodField()

    class Meta:
        model = ChannelContent
        fields = [
            "id",
            "channel",
            "content_type",
            "title",
            "description_preview",
            "text_plain_preview",
            "thumbnail_url",
            "first_asset",
            "visibility",
            "status",
            "is_broadcast",
            "broadcast_id",
            "published_at",
            "duration_seconds",
            "stats",
            "engagement_counts",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_first_asset(self, obj: ChannelContent):
        asset = obj.assets.order_by("sort_order", "created_at").first()
        return ChannelContentAssetSerializer(asset).data if asset else None

    def get_description_preview(self, obj: ChannelContent) -> str:
        return str(obj.description or "")[:240]

    def get_text_plain_preview(self, obj: ChannelContent) -> str:
        return str(obj.text_plain or "")[:240]

    def get_engagement_counts(self, obj: ChannelContent) -> dict:
        stats = obj.stats if isinstance(obj.stats, dict) else {}
        return {
            "views": int(stats.get("views") or 0),
            "shares": int(stats.get("shares") or 0),
            "comments": int(stats.get("comments") or 0),
            "reactions": int(stats.get("reactions") or 0),
        }

    def _active_broadcast(self, obj: ChannelContent):
        if obj.legacy_broadcast_item_id:
            return obj.legacy_broadcast_item if obj.legacy_broadcast_item and not obj.legacy_broadcast_item.is_deleted else None
        return BroadcastItem.objects.filter(
            source_type=BroadcastSourceType.CHANNEL_CONTENT,
            source_id=str(obj.id),
            is_deleted=False,
        ).only("id").first()

    def get_is_broadcast(self, obj: ChannelContent) -> bool:
        return self._active_broadcast(obj) is not None

    def get_broadcast_id(self, obj: ChannelContent) -> str:
        item = self._active_broadcast(obj)
        return str(item.id) if item else ""


class ChannelContentDetailSerializer(ChannelContentListSerializer):
    assets = ChannelContentAssetSerializer(many=True, read_only=True)
    geo_restricted = serializers.SerializerMethodField()

    class Meta(ChannelContentListSerializer.Meta):
        fields = ChannelContentListSerializer.Meta.fields + [
            "description",
            "text_plain",
            "text_doc",
            "assets",
            "metadata",
            "tags",
            "scheduled_at",
            "age_restriction",
            "geo_restricted",
        ]

    def get_geo_restricted(self, obj: ChannelContent) -> bool:
        return hasattr(obj, "geo_restriction") and obj.geo_restriction is not None


class ChannelContentCommentSerializer(serializers.ModelSerializer):
    user_display = serializers.SerializerMethodField()
    like_count = serializers.SerializerMethodField()
    is_liked = serializers.SerializerMethodField()
    reply_count = serializers.SerializerMethodField()

    class Meta:
        model = ChannelContentComment
        fields = [
            "id",
            "content",
            "user",
            "user_display",
            "body",
            "parent",
            "is_pinned",
            "like_count",
            "is_liked",
            "reply_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "content", "user", "user_display", "is_pinned", "like_count", "is_liked", "reply_count", "created_at", "updated_at"]

    def get_user_display(self, obj: ChannelContentComment) -> str:
        user = obj.user
        return (
            getattr(user, "full_name", "")
            or getattr(user, "username", "")
            or getattr(user, "phone", "")
            or "KIS user"
        )

    def get_like_count(self, obj: ChannelContentComment) -> int:
        return obj.reactions.count()

    def get_is_liked(self, obj: ChannelContentComment) -> bool:
        request = self.context.get("request")
        if not request or not getattr(request.user, "is_authenticated", False):
            return False
        return obj.reactions.filter(user=request.user).exists()

    def get_reply_count(self, obj: ChannelContentComment) -> int:
        return obj.replies.filter(is_deleted=False).count()


class ChannelContentChapterSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelContentChapter
        fields = ["id", "content", "title", "start_seconds", "sort_order", "created_at", "updated_at"]
        read_only_fields = ["id", "content", "created_at", "updated_at"]


class BroadcastPlaylistItemSerializer(serializers.ModelSerializer):
    content = ChannelContentListSerializer(read_only=True)

    class Meta:
        model = BroadcastPlaylistItem
        fields = ["id", "playlist", "content", "sort_order", "added_at"]
        read_only_fields = fields


class ChannelModerationRecordSerializer(serializers.ModelSerializer):
    reporter_display = serializers.SerializerMethodField()
    actor_display = serializers.SerializerMethodField()
    content_title = serializers.SerializerMethodField()
    comment_body = serializers.SerializerMethodField()

    class Meta:
        model = ChannelModerationRecord
        fields = [
            "id",
            "channel",
            "content",
            "comment",
            "target_type",
            "target_id",
            "reporter",
            "reporter_display",
            "actor",
            "actor_display",
            "reason",
            "status",
            "action",
            "notes",
            "metadata",
            "content_title",
            "comment_body",
            "resolved_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def _display(self, user) -> str:
        if not user:
            return ""
        return (
            getattr(user, "full_name", "")
            or getattr(user, "username", "")
            or getattr(user, "phone", "")
            or str(getattr(user, "id", ""))
        )

    def get_reporter_display(self, obj: ChannelModerationRecord) -> str:
        return self._display(obj.reporter)

    def get_actor_display(self, obj: ChannelModerationRecord) -> str:
        return self._display(obj.actor)

    def get_content_title(self, obj: ChannelModerationRecord) -> str:
        return str(getattr(obj.content, "title", "") or "")

    def get_comment_body(self, obj: ChannelModerationRecord) -> str:
        return str(getattr(obj.comment, "body", "") or "")[:240]


class ChannelAnalyticsDailyRollupSerializer(serializers.ModelSerializer):
    content_title = serializers.SerializerMethodField()

    class Meta:
        model = ChannelAnalyticsDailyRollup
        fields = [
            "id",
            "channel",
            "content",
            "content_title",
            "date",
            "views",
            "unique_viewers",
            "impressions",
            "watch_time_seconds",
            "average_duration_seconds",
            "subscribers_gained",
            "subscribers_lost",
            "shares",
            "saves",
            "comments",
            "reactions",
            "embed_impressions",
            "live_peak_viewers",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_content_title(self, obj: ChannelAnalyticsDailyRollup) -> str:
        return str(getattr(obj.content, "title", "") or "")


class ChannelLiveStreamSerializer(serializers.ModelSerializer):
    channel = BroadcastChannelSummarySerializer(read_only=True)
    content_id = serializers.UUIDField(source="content.id", read_only=True)
    ingest_url = serializers.SerializerMethodField()
    stream_key_available = serializers.SerializerMethodField()

    class Meta:
        model = ChannelLiveStream
        fields = [
            "id",
            "channel",
            "content_id",
            "title",
            "description",
            "status",
            "scheduled_start_at",
            "started_at",
            "ended_at",
            "provider",
            "provider_stream_id",
            "ingest_url",
            "stream_key_available",
            "playback_url",
            "replay_url",
            "thumbnail_url",
            "viewer_count",
            "peak_viewer_count",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def _can_view_ingest_details(self, obj: ChannelLiveStream) -> bool:
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False):
            return False
        if obj.channel.owner_user_id and obj.channel.owner_user_id == user.id:
            return True
        return BroadcastChannelRole.objects.filter(
            channel=obj.channel,
            user=user,
            role__in=[
                BroadcastChannelRole.Role.OWNER,
                BroadcastChannelRole.Role.MANAGER,
                BroadcastChannelRole.Role.EDITOR,
            ],
        ).exists()

    def get_ingest_url(self, obj: ChannelLiveStream) -> str:
        return obj.ingest_url if self._can_view_ingest_details(obj) else ""

    def get_stream_key_available(self, obj: ChannelLiveStream) -> bool:
        return bool(obj.stream_key_hash and self._can_view_ingest_details(obj))


def _education_humanize(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.replace("_", " ").replace("-", " ").title()


def _education_present_value(value):
    if value in (None, ""):
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item not in (None, ""))
    return str(value)


def _education_detail_item(label: str, value, key: str = "") -> dict:
    return {
        "key": key or label.lower().replace(" ", "_"),
        "label": label,
        "value": _education_present_value(value),
    }


def _education_detail_section(title: str, items: list[dict]) -> dict:
    visible_items = [item for item in items if item.get("value") not in (None, "")]
    return {"title": title, "items": visible_items}


def _education_detail_summary(
    *,
    module: str,
    title: str,
    subtitle: str = "",
    description: str = "",
    status: str = "",
    highlights: list[dict] | None = None,
    sections: list[dict] | None = None,
) -> dict:
    visible_sections = [section for section in (sections or []) if section.get("items")]
    return {
        "variant": "education-module-detail",
        "eyebrow": module,
        "module": module,
        "title": title or module,
        "subtitle": subtitle,
        "description": description,
        "status": {
            "value": status or "",
            "label": _education_humanize(status),
        },
        "highlights": [item for item in (highlights or []) if item.get("value") not in (None, "")],
        "sections": visible_sections,
    }


def _attach_education_detail_summary(payload: dict, summary: dict) -> dict:
    payload["detail_summary"] = summary
    payload["detailSummary"] = summary
    return payload


def _resolve_education_media_display_url(value: str, request=None) -> str:
    """`value` here is one of three shapes: a client-pasted external URL, an
    already-relative path from a legacy/pasted same-host URL that
    normalize_media_reference (strip_backend_origin) stripped down to e.g.
    "/media/institutions/institution.jpg", or one of our own S3 object keys
    stored verbatim by _education_cover_image_from_payload/
    _education_material_media_payload - always "private/<key_prefix>/<user>/
    <uuid>.<ext>" (see apps.media.upload_intent._generate_object_key), never
    leading-slash. Only that third shape was ever broken: string-joined onto
    the API host via absolutize_backend_media alone, producing a URL with no
    matching Django route to a private, unsigned bucket key that always
    404ed. build_media_url is the same resolver every other broadcasts media
    field already uses for that shape specifically (and, via
    default_storage.url(), the exact mechanism ProfileSerializer's
    avatar_file/cover_file rely on for private S3 objects — S3MediaStorage.url()
    returns a real presigned GET automatically); everything else keeps using
    absolutize_backend_media exactly as before."""
    text = str(value or "").strip()
    if not text.startswith("private/"):
        return absolutize_backend_media(text, request)
    try:
        return build_media_url(request, text)
    except Exception:
        # Storage couldn't resolve this key (e.g. a stale/pre-migration
        # value) - fall back rather than crashing; still broken, but no
        # worse than before.
        return absolutize_backend_media(text, request)


def _attach_education_cover_image(payload: dict, cover_image_url: str, context: dict | None = None) -> dict:
    absolute_url = _resolve_education_media_display_url(cover_image_url, (context or {}).get("request"))
    payload["cover_image_url"] = absolute_url
    payload["cover_url"] = absolute_url
    payload["coverUrl"] = absolute_url
    return payload


def _education_effective_broadcast_cover_image(instance: EducationInstitutionBroadcast) -> str:
    explicit_cover = str(instance.cover_image_url or "").strip()
    metadata = instance.metadata if isinstance(instance.metadata, dict) else {}
    manual_cover = bool(metadata.get("manual_cover_image"))
    if explicit_cover and manual_cover:
        return explicit_cover

    prioritized_entities: list[object | None]
    if instance.broadcast_kind == "program":
        prioritized_entities = [instance.program, instance.course, instance.lesson, instance.class_session, instance.event]
    elif instance.broadcast_kind == "course":
        prioritized_entities = [instance.course, instance.lesson, instance.class_session, instance.event, instance.program]
    elif instance.broadcast_kind == "lesson":
        prioritized_entities = [instance.lesson, instance.class_session, instance.course, instance.event, instance.program]
    elif instance.broadcast_kind == "class_session":
        prioritized_entities = [instance.class_session, instance.lesson, instance.course, instance.event, instance.program]
    elif instance.broadcast_kind in {"event", "training_session"}:
        prioritized_entities = [instance.event, instance.class_session, instance.lesson, instance.course, instance.program]
    else:
        prioritized_entities = [instance.program, instance.course, instance.lesson, instance.class_session, instance.event]

    for entity in prioritized_entities:
        value = str(getattr(entity, "cover_image_url", "") or "").strip()
        if value:
            return value
    return explicit_cover or ""



class MediumSerializer(serializers.ModelSerializer):
    class Meta:
        model = Medium
        fields = ["id", "name", "description", "system_flag", "created_at", "updated_at"]



class ServiceMediumMapSerializer(serializers.ModelSerializer):
    medium = MediumSerializer(read_only=True)

    class Meta:
        model = ServiceMediumMap
        fields = ["id", "medium", "created_at"]


class ServiceSerializer(serializers.ModelSerializer):
    medium_links = serializers.SerializerMethodField()
    medium_ids = serializers.SerializerMethodField()

    class Meta:
        model = Service
        fields = [
            "id",
            "name",
            "description",
            "is_default",
            "created_by",
            "medium_links",
            "medium_ids",
            "created_at",
            "updated_at",
        ]

    def get_medium_ids(self, obj: Service):
        return [
            str(link.medium_id)
            for link in obj.medium_links.all()
            if is_service_medium_allowed(link.medium_id, getattr(link.medium, "name", ""))
        ]

    def get_medium_links(self, obj: Service):
        rows = [
            link
            for link in obj.medium_links.all()
            if is_service_medium_allowed(link.medium_id, getattr(link.medium, "name", ""))
        ]
        return ServiceMediumMapSerializer(rows, many=True).data

class BroadcastFeatureSerializer(serializers.ModelSerializer):
    class Meta:
        model = BroadcastFeature
        fields = ["slug", "name", "description", "category", "default_enabled"]


class BroadcastFeatureStatusSerializer(BroadcastFeatureSerializer):
    enabled = serializers.BooleanField()

    class Meta(BroadcastFeatureSerializer.Meta):
        fields = BroadcastFeatureSerializer.Meta.fields + ["enabled"]


class BroadcastVideoSerializer(serializers.ModelSerializer):
    creator_name = serializers.SerializerMethodField()
    channel_name = serializers.SerializerMethodField()
    video_category = serializers.SerializerMethodField()
    stream_url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()
    media_file_path = serializers.SerializerMethodField()

    class Meta:
        model = BroadcastVideo
        fields = [
            "id",
            "title",
            "description",
            "video_url",
            "thumbnail_url",
            "type",
            "duration_seconds",
            "mime_type",
            "storage_path",
            "stream_url",
            "media_file_path",
            "transcript_segments",
            "video_category",
            "creator_name",
            "channel_name",
            "created_at",
        ]

    def get_creator_name(self, obj: BroadcastVideo):
        return obj.creator.display_name if obj.creator else None

    def get_channel_name(self, obj: BroadcastVideo):
        return obj.channel.name if obj.channel else None

    def get_video_category(self, obj: BroadcastVideo):
        if obj.duration_seconds < 4 * 60:
            return "shorts"
        return "videos"

    def get_stream_url(self, obj: BroadcastVideo):
        request = self.context.get("request")
        if not request:
            return obj.video_url
        return request.build_absolute_uri(
            reverse("broadcasts:video-stream", kwargs={"video_id": str(obj.id)})
        )

    def get_thumbnail_url(self, obj: BroadcastVideo):
        request = self.context.get("request")
        if obj.thumbnail_url and obj.thumbnail_url.startswith("http"):
            return obj.thumbnail_url
        rel = ensure_local_thumbnail(obj)
        if not rel:
            return obj.thumbnail_url or None
        if not request:
            return rel
        return build_media_url(request, rel)

    def get_media_file_path(self, obj: BroadcastVideo):
        # `storage_path` is a default_storage-relative key, not a local
        # filesystem path — it may live on S3/Supabase, not MEDIA_ROOT.
        return obj.storage_path


class BroadcastLessonSerializer(serializers.ModelSerializer):
    partner_name = serializers.SerializerMethodField()
    community_name = serializers.SerializerMethodField()
    enrollment_count = serializers.IntegerField(read_only=True)
    is_enrolled = serializers.SerializerMethodField()

    class Meta:
        model = BroadcastLesson
        fields = [
            "id",
            "title",
            "summary",
            "lesson_url",
            "lesson_type",
            "partner_name",
            "community_name",
            "starts_at",
            "ends_at",
            "price_cents",
            "currency",
            "is_public",
            "public_info",
            "enrollment_count",
            "is_enrolled",
        ]

    def get_partner_name(self, obj: BroadcastLesson):
        return obj.partner.name if obj.partner else None

    def get_community_name(self, obj: BroadcastLesson):
        return obj.community.name if obj.community else None

    def get_is_enrolled(self, obj: BroadcastLesson):
        user = self.context.get("request").user if self.context.get("request") else None
        if not user or not getattr(user, "is_authenticated", False):
            return False
        return LessonEnrollment.objects.filter(lesson=obj, user=user).exists()


class LessonEnrollmentSerializer(serializers.ModelSerializer):
    lesson = BroadcastLessonSerializer(read_only=True)

    class Meta:
        model = LessonEnrollment
        fields = ["id", "lesson", "status", "enrolled_at"]


class EducationInstitutionMembershipSerializer(serializers.ModelSerializer):
    user_id = serializers.UUIDField(source="user.id", read_only=True)
    display_name = serializers.SerializerMethodField()
    phone = serializers.CharField(source="user.phone", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True, allow_null=True)

    class Meta:
        model = EducationInstitutionMembership
        fields = [
            "id",
            "user_id",
            "display_name",
            "phone",
            "email",
            "role",
            "status",
            "title",
            "permissions",
            "metadata",
            "created_at",
            "updated_at",
            "decided_at",
        ]

    def get_display_name(self, obj: EducationInstitutionMembership):
        user = getattr(obj, "user", None)
        return getattr(user, "display_name", "") or getattr(user, "username", "") or ""


class EducationInstitutionCourseAccessRequestSerializer(serializers.ModelSerializer):
    user_id = serializers.UUIDField(source="user.id", read_only=True)
    display_name = serializers.SerializerMethodField()
    phone = serializers.CharField(source="user.phone", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True, allow_null=True)
    course_id = serializers.UUIDField(source="course.id", read_only=True)
    course_title = serializers.CharField(source="course.title", read_only=True)

    class Meta:
        model = EducationInstitutionCourseAccessRequest
        fields = [
            "id",
            "course_id",
            "course_title",
            "user_id",
            "display_name",
            "phone",
            "email",
            "status",
            "created_at",
            "updated_at",
            "decided_at",
        ]

    def get_display_name(self, obj: EducationInstitutionCourseAccessRequest):
        user = getattr(obj, "user", None)
        return getattr(user, "display_name", "") or getattr(user, "username", "") or ""


class EducationInstitutionSerializer(serializers.ModelSerializer):
    memberships = EducationInstitutionMembershipSerializer(many=True, read_only=True)
    owner = serializers.PrimaryKeyRelatedField(read_only=True)
    owner_user_id = serializers.SerializerMethodField()
    ownerUserId = serializers.SerializerMethodField()
    active_member_count = serializers.SerializerMethodField()
    pending_application_count = serializers.SerializerMethodField()
    current_membership = serializers.SerializerMethodField()
    can_manage = serializers.SerializerMethodField()
    verification_summary = serializers.SerializerMethodField()
    logo_url = serializers.SerializerMethodField()
    image_url = serializers.SerializerMethodField()
    banner_image_url = serializers.SerializerMethodField()
    logoUrl = serializers.SerializerMethodField()
    imageUrl = serializers.SerializerMethodField()
    bannerImageUrl = serializers.SerializerMethodField()

    class Meta:
        model = EducationInstitution
        fields = [
            "id",
            "owner",
            "owner_user_id",
            "ownerUserId",
            "name",
            "description",
            "institution_type",
            "membership_policy",
            "contact_email",
            "contact_phone",
            "branding",
            "logo_url",
            "image_url",
            "banner_image_url",
            "logoUrl",
            "imageUrl",
            "bannerImageUrl",
            "settings",
            "metadata",
            "is_active",
            "active_member_count",
            "pending_application_count",
            "current_membership",
            "can_manage",
            "verification_summary",
            "memberships",
            "payout_account_status",
            "payout_account_name",
            "payout_bank_last4",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "payout_account_status",
            "payout_account_name",
            "payout_bank_last4",
        ]

    def get_active_member_count(self, obj: EducationInstitution) -> int:
        prefetched = getattr(obj, "_prefetched_objects_cache", {})
        rows = prefetched.get("memberships")
        if rows is not None:
            return sum(1 for row in rows if row.status == "active")
        return obj.memberships.filter(status="active").count()

    def get_pending_application_count(self, obj: EducationInstitution) -> int:
        prefetched = getattr(obj, "_prefetched_objects_cache", {})
        rows = prefetched.get("memberships")
        if rows is not None:
            return sum(1 for row in rows if row.status == "pending")
        return obj.memberships.filter(status="pending").count()

    def get_owner_user_id(self, obj: EducationInstitution) -> str:
        return str(obj.owner_id) if obj.owner_id else ""

    def get_ownerUserId(self, obj: EducationInstitution) -> str:
        return self.get_owner_user_id(obj)

    def get_current_membership(self, obj: EducationInstitution):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or not getattr(user, "is_authenticated", False):
            return None
        if obj.owner_id == user.id:
            membership = obj.memberships.filter(user=user).select_related("user").first()
            if membership:
                return EducationInstitutionMembershipSerializer(membership).data
            return {
                "id": None,
                "user_id": str(user.id),
                "display_name": getattr(user, "display_name", "") or getattr(user, "username", "") or "",
                "phone": getattr(user, "phone", ""),
                "email": getattr(user, "email", ""),
                "role": "owner",
                "status": "active",
                "title": "Owner",
                "permissions": ["manage_all"],
                "metadata": {"source": "owner_field"},
            }
        prefetched = getattr(obj, "_prefetched_objects_cache", {})
        rows = prefetched.get("memberships")
        if rows is not None:
            membership = next((row for row in rows if row.user_id == user.id), None)
        else:
            membership = obj.memberships.filter(user=user).select_related("user").first()
        if not membership:
            return None
        return EducationInstitutionMembershipSerializer(membership).data

    def get_can_manage(self, obj: EducationInstitution) -> bool:
        current = self.get_current_membership(obj) or {}
        return current.get("status") == "active" and current.get("role") in {
            "owner",
            "manager",
            "administrator",
        }

    def get_verification_summary(self, obj: EducationInstitution) -> dict:
        from apps.verification.services import current_education_institution_verification_status

        return current_education_institution_verification_status(obj)

    def get_logo_url(self, obj: EducationInstitution) -> str:
        branding = obj.branding or {}
        value = branding.get("logo_url") or branding.get("logoUrl") or branding.get("image_url") or branding.get("imageUrl") or ""
        return _resolve_education_media_display_url(value, self.context.get("request"))

    def get_image_url(self, obj: EducationInstitution) -> str:
        branding = obj.branding or {}
        value = branding.get("image_url") or branding.get("imageUrl") or branding.get("logo_url") or branding.get("logoUrl") or ""
        return _resolve_education_media_display_url(value, self.context.get("request"))

    def get_banner_image_url(self, obj: EducationInstitution) -> str:
        branding = obj.branding or {}
        value = (
            branding.get("banner_image_url")
            or branding.get("bannerImageUrl")
            or branding.get("cover_image_url")
            or branding.get("coverImageUrl")
            or ""
        )
        return _resolve_education_media_display_url(value, self.context.get("request"))

    def get_logoUrl(self, obj: EducationInstitution) -> str:
        return self.get_logo_url(obj)

    def get_imageUrl(self, obj: EducationInstitution) -> str:
        return self.get_image_url(obj)

    def get_bannerImageUrl(self, obj: EducationInstitution) -> str:
        return self.get_banner_image_url(obj)

    def to_representation(self, instance):
        payload = super().to_representation(instance)
        branding = dict(payload.get("branding") or {})
        request = self.context.get("request")
        for key in (
            "logo_url",
            "logoUrl",
            "image_url",
            "imageUrl",
            "banner_image_url",
            "bannerImageUrl",
            "cover_image_url",
            "coverImageUrl",
        ):
            if branding.get(key):
                branding[key] = _resolve_education_media_display_url(branding.get(key), request)
        payload["branding"] = branding
        return _attach_education_detail_summary(
            payload,
            _education_detail_summary(
                module="Education Institution",
                title=payload.get("name") or "",
                subtitle=_education_humanize(payload.get("institution_type")),
                description=payload.get("description") or "",
                status="active" if payload.get("is_active") else "inactive",
                highlights=[
                    _education_detail_item("Membership", _education_humanize(payload.get("membership_policy"))),
                    _education_detail_item("Active members", payload.get("active_member_count")),
                    _education_detail_item("Pending applications", payload.get("pending_application_count")),
                ],
                sections=[
                    _education_detail_section(
                        "Contact",
                        [
                            _education_detail_item("Email", payload.get("contact_email")),
                            _education_detail_item("Phone", payload.get("contact_phone")),
                        ],
                    ),
                    _education_detail_section(
                        "Branding",
                        [
                            _education_detail_item("Logo", payload.get("logo_url")),
                            _education_detail_item("Image", payload.get("image_url")),
                            _education_detail_item("Banner", payload.get("banner_image_url")),
                        ],
                    ),
                ],
            ),
        )


class EducationInstitutionProgramSerializer(serializers.ModelSerializer):
    class Meta:
        model = EducationInstitutionProgram
        fields = [
            "id",
            "title",
            "code",
            "summary",
            "description",
            "cover_image_url",
            "status",
            "metadata",
            "created_at",
            "updated_at",
        ]

    def to_representation(self, instance):
        payload = super().to_representation(instance)
        payload = _attach_education_cover_image(payload, payload.get("cover_image_url") or "", self.context)
        return _attach_education_detail_summary(
            payload,
            _education_detail_summary(
                module="Program",
                title=payload.get("title") or "",
                subtitle=payload.get("code") or "",
                description=payload.get("description") or payload.get("summary") or "",
                status=payload.get("status") or "",
                highlights=[
                    _education_detail_item("Code", payload.get("code")),
                    _education_detail_item("Status", _education_humanize(payload.get("status"))),
                ],
                sections=[
                    _education_detail_section(
                        "Overview",
                        [
                            _education_detail_item("Summary", payload.get("summary")),
                            _education_detail_item("Description", payload.get("description")),
                        ],
                    ),
                ],
            ),
        )


class EducationInstitutionStaffAssignmentSerializer(serializers.ModelSerializer):
    membership_id = serializers.UUIDField(source="membership.id", read_only=True)
    user_id = serializers.UUIDField(source="membership.user.id", read_only=True)
    display_name = serializers.SerializerMethodField()
    program_id = serializers.UUIDField(source="program.id", read_only=True, allow_null=True)
    course_id = serializers.UUIDField(source="course.id", read_only=True, allow_null=True)
    class_session_id = serializers.UUIDField(source="class_session.id", read_only=True, allow_null=True)
    event_id = serializers.UUIDField(source="event.id", read_only=True, allow_null=True)
    assessment_id = serializers.UUIDField(source="assessment.id", read_only=True, allow_null=True)

    class Meta:
        model = EducationInstitutionStaffAssignment
        fields = [
            "id",
            "membership_id",
            "user_id",
            "display_name",
            "program_id",
            "course_id",
            "class_session_id",
            "event_id",
            "assessment_id",
            "role",
            "status",
            "notes",
            "metadata",
            "created_at",
            "updated_at",
        ]

    def get_display_name(self, obj: EducationInstitutionStaffAssignment):
        user = getattr(getattr(obj, "membership", None), "user", None)
        return getattr(user, "display_name", "") or getattr(user, "username", "") or ""


class EducationInstitutionCourseSerializer(serializers.ModelSerializer):
    program_id = serializers.UUIDField(source="program.id", read_only=True)
    is_free = serializers.BooleanField(read_only=True)

    class Meta:
        model = EducationInstitutionCourse
        fields = [
            "id",
            "program_id",
            "title",
            "code",
            "summary",
            "description",
            "cover_image_url",
            "status",
            "duration_minutes",
            "seat_limit",
            "price_amount",
            "price_currency",
            "is_free",
            "visibility",
            "metadata",
            "settings",
            "created_at",
            "updated_at",
        ]

    def to_representation(self, instance):
        payload = super().to_representation(instance)
        payload = _attach_education_cover_image(payload, payload.get("cover_image_url") or "", self.context)
        return _attach_education_detail_summary(
            payload,
            _education_detail_summary(
                module="Course",
                title=payload.get("title") or "",
                subtitle=payload.get("code") or "",
                description=payload.get("description") or payload.get("summary") or "",
                status=payload.get("status") or "",
                highlights=[
                    _education_detail_item("Duration", f"{payload.get('duration_minutes')} min" if payload.get("duration_minutes") else ""),
                    _education_detail_item("Seats", payload.get("seat_limit")),
                    _education_detail_item("Status", _education_humanize(payload.get("status"))),
                ],
                sections=[
                    _education_detail_section(
                        "Overview",
                        [
                            _education_detail_item("Code", payload.get("code")),
                            _education_detail_item("Summary", payload.get("summary")),
                            _education_detail_item("Description", payload.get("description")),
                        ],
                    ),
                ],
            ),
        )


class EducationInstitutionCourseModuleItemSerializer(serializers.ModelSerializer):
    lesson_id = serializers.UUIDField(source="lesson.id", read_only=True, allow_null=True)
    material_id = serializers.UUIDField(source="material.id", read_only=True, allow_null=True)
    class_session_id = serializers.UUIDField(source="class_session.id", read_only=True, allow_null=True)
    assessment_id = serializers.UUIDField(source="assessment.id", read_only=True, allow_null=True)
    event_id = serializers.UUIDField(source="event.id", read_only=True, allow_null=True)
    broadcast_id = serializers.UUIDField(source="broadcast.id", read_only=True, allow_null=True)

    class Meta:
        model = EducationInstitutionCourseModuleItem
        fields = [
            "id",
            "item_type",
            "item_order",
            "title_override",
            "summary_override",
            "estimated_minutes",
            "lesson_id",
            "material_id",
            "class_session_id",
            "assessment_id",
            "event_id",
            "broadcast_id",
            "metadata",
            "created_at",
            "updated_at",
        ]

    def to_representation(self, instance):
        payload = super().to_representation(instance)
        title = payload.get("title_override") or _education_humanize(payload.get("item_type"))
        return _attach_education_detail_summary(
            payload,
            _education_detail_summary(
                module="Module Item",
                title=title,
                subtitle=_education_humanize(payload.get("item_type")),
                description=payload.get("summary_override") or "",
                status="",
                highlights=[
                    _education_detail_item("Order", payload.get("item_order")),
                    _education_detail_item("Estimated time", f"{payload.get('estimated_minutes')} min" if payload.get("estimated_minutes") else ""),
                    _education_detail_item("Type", _education_humanize(payload.get("item_type"))),
                ],
                sections=[
                    _education_detail_section(
                        "Linked record",
                        [
                            _education_detail_item("Lesson", payload.get("lesson_id")),
                            _education_detail_item("Material", payload.get("material_id")),
                            _education_detail_item("Class session", payload.get("class_session_id")),
                            _education_detail_item("Assessment", payload.get("assessment_id")),
                            _education_detail_item("Event", payload.get("event_id")),
                            _education_detail_item("Broadcast", payload.get("broadcast_id")),
                        ],
                    ),
                ],
            ),
        )


class EducationInstitutionCourseModuleSerializer(serializers.ModelSerializer):
    course_id = serializers.UUIDField(source="course.id", read_only=True)
    items = EducationInstitutionCourseModuleItemSerializer(many=True, read_only=True)

    class Meta:
        model = EducationInstitutionCourseModule
        fields = [
            "id",
            "course_id",
            "title",
            "summary",
            "module_order",
            "is_preview",
            "status",
            "metadata",
            "items",
            "created_at",
            "updated_at",
        ]

    def to_representation(self, instance):
        payload = super().to_representation(instance)
        return _attach_education_detail_summary(
            payload,
            _education_detail_summary(
                module="Course Module",
                title=payload.get("title") or "",
                subtitle=f"Module {payload.get('module_order')}" if payload.get("module_order") else "",
                description=payload.get("summary") or "",
                status=payload.get("status") or "",
                highlights=[
                    _education_detail_item("Order", payload.get("module_order")),
                    _education_detail_item("Preview", payload.get("is_preview")),
                    _education_detail_item("Items", len(payload.get("items") or [])),
                ],
                sections=[
                    _education_detail_section(
                        "Overview",
                        [
                            _education_detail_item("Summary", payload.get("summary")),
                            _education_detail_item("Status", _education_humanize(payload.get("status"))),
                        ],
                    ),
                ],
            ),
        )


class EducationInstitutionLessonSerializer(serializers.ModelSerializer):
    course_id = serializers.UUIDField(source="course.id", read_only=True)

    class Meta:
        model = EducationInstitutionLesson
        fields = [
            "id",
            "course_id",
            "title",
            "summary",
            "content",
            "cover_image_url",
            "lesson_order",
            "duration_minutes",
            "is_preview",
            "status",
            "metadata",
            "created_at",
            "updated_at",
        ]

    def to_representation(self, instance):
        payload = super().to_representation(instance)
        payload = _attach_education_cover_image(payload, payload.get("cover_image_url") or "", self.context)
        content = payload.get("content") or ""
        return _attach_education_detail_summary(
            payload,
            _education_detail_summary(
                module="Lesson",
                title=payload.get("title") or "",
                subtitle=f"Lesson {payload.get('lesson_order')}" if payload.get("lesson_order") else "",
                description=payload.get("summary") or content[:180],
                status=payload.get("status") or "",
                highlights=[
                    _education_detail_item("Duration", f"{payload.get('duration_minutes')} min" if payload.get("duration_minutes") else ""),
                    _education_detail_item("Preview", payload.get("is_preview")),
                    _education_detail_item("Order", payload.get("lesson_order")),
                ],
                sections=[
                    _education_detail_section(
                        "Lesson details",
                        [
                            _education_detail_item("Summary", payload.get("summary")),
                            _education_detail_item("Content preview", content[:240]),
                        ],
                    ),
                ],
            ),
        )


class EducationInstitutionClassSessionSerializer(serializers.ModelSerializer):
    course_id = serializers.UUIDField(source="course.id", read_only=True)
    lesson_id = serializers.UUIDField(source="lesson.id", read_only=True)

    class Meta:
        model = EducationInstitutionClassSession
        fields = [
            "id",
            "course_id",
            "lesson_id",
            "title",
            "summary",
            "cover_image_url",
            "starts_at",
            "ends_at",
            "timezone_name",
            "delivery_mode",
            "location_text",
            "meeting_url",
            "seat_limit",
            "status",
            "metadata",
            "created_at",
            "updated_at",
        ]

    def to_representation(self, instance):
        payload = super().to_representation(instance)
        payload = _attach_education_cover_image(payload, payload.get("cover_image_url") or "", self.context)
        return _attach_education_detail_summary(
            payload,
            _education_detail_summary(
                module="Class Session",
                title=payload.get("title") or "",
                subtitle=_education_humanize(payload.get("delivery_mode")),
                description=payload.get("summary") or "",
                status=payload.get("status") or "",
                highlights=[
                    _education_detail_item("Starts", payload.get("starts_at")),
                    _education_detail_item("Ends", payload.get("ends_at")),
                    _education_detail_item("Seats", payload.get("seat_limit")),
                ],
                sections=[
                    _education_detail_section(
                        "Schedule",
                        [
                            _education_detail_item("Starts at", payload.get("starts_at")),
                            _education_detail_item("Ends at", payload.get("ends_at")),
                            _education_detail_item("Timezone", payload.get("timezone_name")),
                        ],
                    ),
                    _education_detail_section(
                        "Delivery",
                        [
                            _education_detail_item("Mode", _education_humanize(payload.get("delivery_mode"))),
                            _education_detail_item("Location", payload.get("location_text")),
                            _education_detail_item("Meeting URL", payload.get("meeting_url")),
                        ],
                    ),
                ],
            ),
        )


class EducationInstitutionMaterialSerializer(serializers.ModelSerializer):
    program_id = serializers.UUIDField(source="program.id", read_only=True, allow_null=True)
    course_id = serializers.UUIDField(source="course.id", read_only=True)
    lesson_id = serializers.UUIDField(source="lesson.id", read_only=True)
    class_session_id = serializers.UUIDField(source="class_session.id", read_only=True, allow_null=True)
    assessment_id = serializers.UUIDField(source="assessment.id", read_only=True, allow_null=True)
    program_ids = serializers.SerializerMethodField()
    course_ids = serializers.SerializerMethodField()
    lesson_ids = serializers.SerializerMethodField()
    class_session_ids = serializers.SerializerMethodField()
    assessment_ids = serializers.SerializerMethodField()
    safe_resource_url = serializers.SerializerMethodField()
    private_media_ref = serializers.SerializerMethodField()
    media_safety_status = serializers.SerializerMethodField()
    media_review_required = serializers.SerializerMethodField()

    class Meta:
        model = EducationInstitutionMaterial
        fields = [
            "id",
            "program_id",
            "program_ids",
            "course_id",
            "course_ids",
            "lesson_id",
            "lesson_ids",
            "class_session_id",
            "class_session_ids",
            "assessment_id",
            "assessment_ids",
            "title",
            "summary",
            "cover_image_url",
            "kind",
            "resource_url",
            "resource_name",
            "resource_mime_type",
            "storage_path",
            "safe_resource_url",
            "private_media_ref",
            "media_safety_status",
            "media_review_required",
            "is_downloadable",
            "status",
            "metadata",
            "created_at",
            "updated_at",
        ]

    def to_representation(self, instance):
        payload = super().to_representation(instance)
        payload = _attach_education_cover_image(payload, payload.get("cover_image_url") or "", self.context)
        payload["storage_path"] = ""
        safe_resource_url = payload.get("safe_resource_url") or ""
        return _attach_education_detail_summary(
            payload,
            _education_detail_summary(
                module="Material",
                title=payload.get("title") or "",
                subtitle=_education_humanize(payload.get("kind")),
                description=payload.get("summary") or "",
                status=payload.get("status") or "",
                highlights=[
                    _education_detail_item("Kind", _education_humanize(payload.get("kind"))),
                    _education_detail_item("Downloadable", payload.get("is_downloadable")),
                    _education_detail_item("Resource", payload.get("resource_name") or safe_resource_url),
                    _education_detail_item("Safety", _education_humanize(payload.get("media_safety_status"))),
                ],
                sections=[
                    _education_detail_section(
                        "Resource",
                        [
                            _education_detail_item("Name", payload.get("resource_name")),
                            _education_detail_item("URL", safe_resource_url),
                            _education_detail_item("MIME type", payload.get("resource_mime_type")),
                            _education_detail_item("Safety status", _education_humanize(payload.get("media_safety_status"))),
                        ],
                    ),
                    _education_detail_section(
                        "Linked modules",
                        [
                            _education_detail_item("Programs", payload.get("program_ids")),
                            _education_detail_item("Courses", payload.get("course_ids")),
                            _education_detail_item("Lessons", payload.get("lesson_ids")),
                            _education_detail_item("Class sessions", payload.get("class_session_ids")),
                            _education_detail_item("Assessments", payload.get("assessment_ids")),
                        ],
                    ),
                ],
            ),
        )

    def _media_safety(self, obj):
        metadata = obj.metadata if isinstance(obj.metadata, dict) else {}
        safety = metadata.get("media_safety") if isinstance(metadata.get("media_safety"), dict) else {}
        return safety

    def get_safe_resource_url(self, obj):
        safety = self._media_safety(obj)
        if safety.get("quarantined") or safety.get("blocked"):
            return ""
        raw_url = getattr(obj, "resource_url", "") or ""
        return _resolve_education_media_display_url(raw_url, self.context.get("request"))

    def get_private_media_ref(self, obj):
        metadata = obj.metadata if isinstance(obj.metadata, dict) else {}
        return str(metadata.get("private_media_ref") or metadata.get("private_media_id") or "") or None

    def get_media_safety_status(self, obj):
        safety = self._media_safety(obj)
        return str(safety.get("status") or safety.get("scan_status") or "not_configured")

    def get_media_review_required(self, obj):
        safety = self._media_safety(obj)
        return bool(safety.get("requires_review") or safety.get("quarantined") or safety.get("blocked"))

    def get_program_ids(self, obj):
        ids = list(obj.program_links.values_list("id", flat=True))
        if obj.program_id and obj.program_id not in ids:
            ids.insert(0, obj.program_id)
        return [str(value) for value in ids]

    def get_course_ids(self, obj):
        ids = list(obj.course_links.values_list("id", flat=True))
        if obj.course_id and obj.course_id not in ids:
            ids.insert(0, obj.course_id)
        return [str(value) for value in ids]

    def get_lesson_ids(self, obj):
        ids = list(obj.lesson_links.values_list("id", flat=True))
        if obj.lesson_id and obj.lesson_id not in ids:
            ids.insert(0, obj.lesson_id)
        return [str(value) for value in ids]

    def get_class_session_ids(self, obj):
        ids = list(obj.class_session_links.values_list("id", flat=True))
        if obj.class_session_id and obj.class_session_id not in ids:
            ids.insert(0, obj.class_session_id)
        return [str(value) for value in ids]

    def get_assessment_ids(self, obj):
        ids = list(obj.assessment_links.values_list("id", flat=True))
        if obj.assessment_id and obj.assessment_id not in ids:
            ids.insert(0, obj.assessment_id)
        return [str(value) for value in ids]


class EducationInstitutionAssessmentOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = EducationInstitutionAssessmentOption
        fields = [
            "id",
            "option_text",
            "option_order",
            "is_correct",
            "explanation",
            "created_at",
            "updated_at",
        ]


class EducationInstitutionAssessmentQuestionSerializer(serializers.ModelSerializer):
    options = EducationInstitutionAssessmentOptionSerializer(many=True, read_only=True)
    points = LenientDecimalField(max_digits=8, decimal_places=2, required=False)

    class Meta:
        model = EducationInstitutionAssessmentQuestion
        fields = [
            "id",
            "prompt",
            "question_type",
            "question_order",
            "points",
            "is_required",
            "metadata",
            "options",
            "created_at",
            "updated_at",
        ]

    def to_representation(self, instance):
        payload = super().to_representation(instance)
        return _attach_education_detail_summary(
            payload,
            _education_detail_summary(
                module="Assessment Question",
                title=payload.get("prompt") or "",
                subtitle=_education_humanize(payload.get("question_type")),
                status="required" if payload.get("is_required") else "optional",
                highlights=[
                    _education_detail_item("Points", payload.get("points")),
                    _education_detail_item("Order", payload.get("question_order")),
                    _education_detail_item("Options", len(payload.get("options") or [])),
                ],
                sections=[
                    _education_detail_section(
                        "Question",
                        [
                            _education_detail_item("Prompt", payload.get("prompt")),
                            _education_detail_item("Type", _education_humanize(payload.get("question_type"))),
                            _education_detail_item("Required", payload.get("is_required")),
                        ],
                    ),
                ],
            ),
        )


class EducationInstitutionAssessmentSerializer(serializers.ModelSerializer):
    course_id = serializers.UUIDField(source="course.id", read_only=True)
    lesson_id = serializers.UUIDField(source="lesson.id", read_only=True)
    class_session_id = serializers.UUIDField(source="class_session.id", read_only=True)
    questions = EducationInstitutionAssessmentQuestionSerializer(many=True, read_only=True)
    total_points = LenientDecimalField(max_digits=8, decimal_places=2, read_only=True)

    class Meta:
        model = EducationInstitutionAssessment
        fields = [
            "id",
            "course_id",
            "lesson_id",
            "class_session_id",
            "title",
            "summary",
            "instructions",
            "cover_image_url",
            "assessment_type",
            "status",
            "starts_at",
            "ends_at",
            "duration_minutes",
            "max_attempts",
            "passing_score_percent",
            "total_points",
            "metadata",
            "settings",
            "questions",
            "created_at",
            "updated_at",
        ]

    def to_representation(self, instance):
        payload = super().to_representation(instance)
        payload = _attach_education_cover_image(payload, payload.get("cover_image_url") or "", self.context)
        return _attach_education_detail_summary(
            payload,
            _education_detail_summary(
                module="Assessment",
                title=payload.get("title") or "",
                subtitle=_education_humanize(payload.get("assessment_type")),
                description=payload.get("summary") or payload.get("instructions") or "",
                status=payload.get("status") or "",
                highlights=[
                    _education_detail_item("Duration", f"{payload.get('duration_minutes')} min" if payload.get("duration_minutes") else ""),
                    _education_detail_item("Passing score", f"{payload.get('passing_score_percent')}%" if payload.get("passing_score_percent") not in (None, "") else ""),
                    _education_detail_item("Total points", payload.get("total_points")),
                ],
                sections=[
                    _education_detail_section(
                        "Assessment rules",
                        [
                            _education_detail_item("Instructions", payload.get("instructions")),
                            _education_detail_item("Max attempts", payload.get("max_attempts")),
                            _education_detail_item("Starts at", payload.get("starts_at")),
                            _education_detail_item("Ends at", payload.get("ends_at")),
                        ],
                    ),
                ],
            ),
        )


class EducationInstitutionAssessmentResponseOptionSerializer(serializers.ModelSerializer):
    option_id = serializers.UUIDField(source="option.id", read_only=True)

    class Meta:
        model = EducationInstitutionAssessmentResponseOption
        fields = ["id", "option_id", "created_at"]


class EducationInstitutionAssessmentResponseSerializer(serializers.ModelSerializer):
    question_id = serializers.UUIDField(source="question.id", read_only=True)
    selected_options = EducationInstitutionAssessmentResponseOptionSerializer(many=True, read_only=True)
    earned_points = LenientDecimalField(max_digits=8, decimal_places=2, required=False)

    class Meta:
        model = EducationInstitutionAssessmentResponse
        fields = [
            "id",
            "question_id",
            "answer_text",
            "is_correct",
            "earned_points",
            "grader_feedback",
            "metadata",
            "selected_options",
            "created_at",
            "updated_at",
        ]


class EducationInstitutionAssessmentSubmissionSerializer(serializers.ModelSerializer):
    assessment_id = serializers.UUIDField(source="assessment.id", read_only=True)
    user_id = serializers.UUIDField(source="user.id", read_only=True)
    grader_id = serializers.UUIDField(source="grader.id", read_only=True, allow_null=True)
    responses = EducationInstitutionAssessmentResponseSerializer(many=True, read_only=True)
    earned_points = LenientDecimalField(max_digits=8, decimal_places=2, read_only=True)
    score_percent = LenientDecimalField(max_digits=6, decimal_places=2, read_only=True)

    class Meta:
        model = EducationInstitutionAssessmentSubmission
        fields = [
            "id",
            "assessment_id",
            "user_id",
            "attempt_number",
            "status",
            "earned_points",
            "score_percent",
            "grader_id",
            "grader_feedback",
            "metadata",
            "responses",
            "started_at",
            "submitted_at",
            "graded_at",
            "created_at",
            "updated_at",
        ]


class EducationInstitutionEventSerializer(serializers.ModelSerializer):
    program_id = serializers.UUIDField(source="program.id", read_only=True, allow_null=True)
    course_id = serializers.UUIDField(source="course.id", read_only=True, allow_null=True)
    class_session_id = serializers.UUIDField(source="class_session.id", read_only=True, allow_null=True)

    class Meta:
        model = EducationInstitutionEvent
        fields = [
            "id",
            "program_id",
            "course_id",
            "class_session_id",
            "event_type",
            "title",
            "summary",
            "description",
            "cover_image_url",
            "starts_at",
            "ends_at",
            "timezone_name",
            "delivery_mode",
            "location_text",
            "meeting_url",
            "seat_limit",
            "status",
            "metadata",
            "created_at",
            "updated_at",
        ]

    def to_representation(self, instance):
        payload = super().to_representation(instance)
        payload = _attach_education_cover_image(payload, payload.get("cover_image_url") or "", self.context)
        return _attach_education_detail_summary(
            payload,
            _education_detail_summary(
                module="Event",
                title=payload.get("title") or "",
                subtitle=_education_humanize(payload.get("event_type")),
                description=payload.get("description") or payload.get("summary") or "",
                status=payload.get("status") or "",
                highlights=[
                    _education_detail_item("Starts", payload.get("starts_at")),
                    _education_detail_item("Delivery", _education_humanize(payload.get("delivery_mode"))),
                    _education_detail_item("Seats", payload.get("seat_limit")),
                ],
                sections=[
                    _education_detail_section(
                        "Schedule",
                        [
                            _education_detail_item("Starts at", payload.get("starts_at")),
                            _education_detail_item("Ends at", payload.get("ends_at")),
                            _education_detail_item("Timezone", payload.get("timezone_name")),
                        ],
                    ),
                    _education_detail_section(
                        "Event details",
                        [
                            _education_detail_item("Summary", payload.get("summary")),
                            _education_detail_item("Description", payload.get("description")),
                            _education_detail_item("Location", payload.get("location_text")),
                            _education_detail_item("Meeting URL", payload.get("meeting_url")),
                        ],
                    ),
                ],
            ),
        )


class EducationInstitutionBroadcastSerializer(serializers.ModelSerializer):
    program_id = serializers.UUIDField(source="program.id", read_only=True, allow_null=True)
    course_id = serializers.UUIDField(source="course.id", read_only=True)
    lesson_id = serializers.UUIDField(source="lesson.id", read_only=True)
    class_session_id = serializers.UUIDField(source="class_session.id", read_only=True)
    event_id = serializers.UUIDField(source="event.id", read_only=True)
    institution_id = serializers.UUIDField(source="institution.id", read_only=True)
    created_by_id = serializers.UUIDField(source="created_by.id", read_only=True)
    membership_policy = serializers.CharField(source="institution.membership_policy", read_only=True)
    price_amount = LenientDecimalField(max_digits=10, decimal_places=2, allow_null=True, required=False)

    class Meta:
        model = EducationInstitutionBroadcast
        fields = [
            "id",
            "institution_id",
            "created_by_id",
            "broadcast_kind",
            "program_id",
            "course_id",
            "lesson_id",
            "class_session_id",
            "event_id",
            "title",
            "summary",
            "description",
            "cover_image_url",
            "starts_at",
            "ends_at",
            "timezone_name",
            "seat_limit",
            "booking_enabled",
            "price_amount",
            "price_currency",
            "membership_policy",
            "status",
            "published_at",
            "expires_at",
            "metadata",
            "created_at",
            "updated_at",
        ]

    def to_representation(self, instance):
        payload = super().to_representation(instance)
        payload = _attach_education_cover_image(
            payload,
            _education_effective_broadcast_cover_image(instance),
            self.context,
        )
        return _attach_education_detail_summary(
            payload,
            _education_detail_summary(
                module="Broadcast",
                title=payload.get("title") or "",
                subtitle=_education_humanize(payload.get("broadcast_kind")),
                description=payload.get("description") or payload.get("summary") or "",
                status=payload.get("status") or "",
                highlights=[
                    _education_detail_item("Booking", payload.get("booking_enabled")),
                    _education_detail_item("Price", payload.get("price_amount")),
                    _education_detail_item("Seats", payload.get("seat_limit")),
                ],
                sections=[
                    _education_detail_section(
                        "Broadcast details",
                        [
                            _education_detail_item("Summary", payload.get("summary")),
                            _education_detail_item("Description", payload.get("description")),
                            _education_detail_item("Cover image", payload.get("cover_image_url")),
                        ],
                    ),
                    _education_detail_section(
                        "Schedule and booking",
                        [
                            _education_detail_item("Starts at", payload.get("starts_at")),
                            _education_detail_item("Ends at", payload.get("ends_at")),
                            _education_detail_item("Timezone", payload.get("timezone_name")),
                            _education_detail_item("Price currency", payload.get("price_currency")),
                        ],
                    ),
                ],
            ),
        )


class EducationInstitutionEnrollmentSerializer(serializers.ModelSerializer):
    broadcast_id = serializers.UUIDField(source="broadcast.id", read_only=True)
    program_id = serializers.UUIDField(source="program.id", read_only=True, allow_null=True)
    user_id = serializers.UUIDField(source="user.id", read_only=True)
    course_id = serializers.UUIDField(source="course.id", read_only=True, allow_null=True)
    lesson_id = serializers.UUIDField(source="lesson.id", read_only=True, allow_null=True)
    class_session_id = serializers.UUIDField(source="class_session.id", read_only=True, allow_null=True)
    event_id = serializers.UUIDField(source="event.id", read_only=True, allow_null=True)

    class Meta:
        model = EducationInstitutionEnrollment
        fields = [
            "id",
            "broadcast_id",
            "program_id",
            "user_id",
            "course_id",
            "lesson_id",
            "class_session_id",
            "event_id",
            "status",
            "enrolled_at",
            "completed_at",
            "metadata",
            "created_at",
            "updated_at",
        ]


class EducationCourseReviewSerializer(serializers.ModelSerializer):
    user_id = serializers.UUIDField(source="user.id", read_only=True)
    author_name = serializers.SerializerMethodField()

    class Meta:
        model = EducationCourseReview
        fields = [
            "id",
            "broadcast_id",
            "course_id",
            "user_id",
            "author_name",
            "rating",
            "title",
            "comment",
            "status",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_author_name(self, obj: EducationCourseReview) -> str:
        user = getattr(obj, "user", None)
        return getattr(user, "display_name", "") or getattr(user, "username", "") or "Learner"


class EducationCourseQuestionSerializer(serializers.ModelSerializer):
    user_id = serializers.UUIDField(source="user.id", read_only=True)
    author_name = serializers.SerializerMethodField()
    answered_by_name = serializers.SerializerMethodField()

    class Meta:
        model = EducationCourseQuestion
        fields = [
            "id",
            "broadcast_id",
            "course_id",
            "user_id",
            "author_name",
            "question",
            "answer",
            "answered_by",
            "answered_by_name",
            "answered_at",
            "status",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_author_name(self, obj: EducationCourseQuestion) -> str:
        user = getattr(obj, "user", None)
        return getattr(user, "display_name", "") or getattr(user, "username", "") or "Learner"

    def get_answered_by_name(self, obj: EducationCourseQuestion) -> str:
        user = getattr(obj, "answered_by", None)
        return getattr(user, "display_name", "") or getattr(user, "username", "") or ""


class EducationInstitutionBookingSerializer(serializers.ModelSerializer):
    broadcast_id = serializers.UUIDField(source="broadcast.id", read_only=True)
    program_id = serializers.UUIDField(source="program.id", read_only=True, allow_null=True)
    course_id = serializers.UUIDField(source="course.id", read_only=True, allow_null=True)
    class_session_id = serializers.UUIDField(source="class_session.id", read_only=True, allow_null=True)
    event_id = serializers.UUIDField(source="event.id", read_only=True, allow_null=True)
    user_id = serializers.UUIDField(source="user.id", read_only=True)
    wallet_transaction_id = serializers.UUIDField(source="wallet_transaction.id", read_only=True, allow_null=True)
    provider_credit_transaction_id = serializers.UUIDField(
        source="provider_credit_transaction.id",
        read_only=True,
        allow_null=True,
    )
    booked_item_id = serializers.SerializerMethodField()
    booked_item_type = serializers.SerializerMethodField()
    booked_item_title = serializers.SerializerMethodField()
    booked_item_summary = serializers.SerializerMethodField()
    booked_item_starts_at = serializers.SerializerMethodField()
    booked_item_ends_at = serializers.SerializerMethodField()
    broadcast_title = serializers.CharField(source="broadcast.title", read_only=True, allow_null=True)
    learner_display_name = serializers.SerializerMethodField()
    amount_usd_label = serializers.SerializerMethodField()
    currency_label = serializers.SerializerMethodField()
    payment_provider = serializers.SerializerMethodField()
    payment_status = serializers.SerializerMethodField()
    payment_required = serializers.SerializerMethodField()
    payment_intent_id = serializers.SerializerMethodField()
    direct_payment_intent_id = serializers.SerializerMethodField()
    payment_reference = serializers.SerializerMethodField()
    payment_url = serializers.SerializerMethodField()
    receipt_url = serializers.SerializerMethodField()
    receipt_pdf_url = serializers.SerializerMethodField()

    class Meta:
        model = EducationInstitutionBooking
        fields = [
            "id",
            "broadcast_id",
            "program_id",
            "course_id",
            "class_session_id",
            "event_id",
            "user_id",
            "status",
            "seat_count",
            "amount_cents",
            "amount_usd_label",
            "currency",
            "currency_label",
            "payment_method",
            "payment_provider",
            "payment_status",
            "payment_required",
            "payment_intent_id",
            "direct_payment_intent_id",
            "payment_reference",
            "payment_url",
            "receipt_url",
            "receipt_pdf_url",
            "wallet_transaction_id",
            "provider_credit_transaction_id",
            "booked_item_id",
            "booked_item_type",
            "booked_item_title",
            "booked_item_summary",
            "booked_item_starts_at",
            "booked_item_ends_at",
            "broadcast_title",
            "learner_display_name",
            "reserved_at",
            "confirmed_at",
            "provider_completed_at",
            "payer_satisfied_at",
            "satisfaction_deadline",
            "metadata",
            "created_at",
            "updated_at",
        ]

    def get_amount_usd_label(self, obj):
        return f"${(Decimal(int(getattr(obj, 'amount_cents', 0) or 0)) / Decimal('100')).quantize(Decimal('0.01'))}"

    def get_currency_label(self, obj):
        code = str(getattr(obj, "currency", "") or "").strip().upper()
        if code == "USD":
            return "USD"
        if code in {"KISC", "KIS"}:
            return "Historical promotional-credit booking"
        return code or "USD"

    def get_payment_provider(self, obj):
        metadata = obj.metadata if isinstance(obj.metadata, dict) else {}
        return str(metadata.get("payment_provider") or obj.payment_method or ("legacy_wallet" if obj.wallet_transaction_id else "flutterwave"))

    def get_payment_status(self, obj):
        metadata = obj.metadata if isinstance(obj.metadata, dict) else {}
        return str(metadata.get("payment_status") or ("paid" if obj.wallet_transaction_id else "pending"))

    def get_payment_required(self, obj):
        metadata = obj.metadata if isinstance(obj.metadata, dict) else {}
        if "payment_required" in metadata:
            return bool(metadata.get("payment_required"))
        return bool(int(getattr(obj, "amount_cents", 0) or 0) > 0 and not obj.wallet_transaction_id)

    def get_payment_intent_id(self, obj):
        metadata = obj.metadata if isinstance(obj.metadata, dict) else {}
        return str(metadata.get("direct_payment_intent_id") or "") or None

    def get_direct_payment_intent_id(self, obj):
        return self.get_payment_intent_id(obj)

    def get_payment_reference(self, obj):
        metadata = obj.metadata if isinstance(obj.metadata, dict) else {}
        return str(metadata.get("payment_reference") or metadata.get("provider_reference") or "") or None

    def get_payment_url(self, obj):
        metadata = obj.metadata if isinstance(obj.metadata, dict) else {}
        return str(metadata.get("payment_url") or "") or None

    def _receipt_path_url(self, obj, key: str):
        metadata = obj.metadata if isinstance(obj.metadata, dict) else {}
        relative_path = metadata.get(key)
        if not relative_path:
            return None
        from apps.billing.documents import build_media_url

        try:
            return build_media_url(self.context.get("request"), relative_path)
        except Exception:
            return None

    def get_receipt_url(self, obj):
        return self._receipt_path_url(obj, "receipt_html_path")

    def get_receipt_pdf_url(self, obj):
        return self._receipt_path_url(obj, "receipt_pdf_path")

    def _target(self, obj: EducationInstitutionBooking):
        targets = (
            ("event", obj.event),
            ("class_session", obj.class_session),
            ("course", obj.course),
            ("program", obj.program),
            ("broadcast", obj.broadcast),
        )
        for target_type, target in targets:
            if target is not None:
                return target_type, target
        return None, None

    def get_booked_item_id(self, obj: EducationInstitutionBooking):
        _target_type, target = self._target(obj)
        return str(target.id) if target is not None else None

    def get_booked_item_type(self, obj: EducationInstitutionBooking):
        target_type, _target = self._target(obj)
        return target_type

    def get_booked_item_title(self, obj: EducationInstitutionBooking):
        _target_type, target = self._target(obj)
        if target is None:
            return ""
        return getattr(target, "title", "") or str(target)

    def get_booked_item_summary(self, obj: EducationInstitutionBooking):
        _target_type, target = self._target(obj)
        if target is None:
            return ""
        return getattr(target, "summary", "") or getattr(target, "description", "") or ""

    def get_booked_item_starts_at(self, obj: EducationInstitutionBooking):
        _target_type, target = self._target(obj)
        return getattr(target, "starts_at", None) if target is not None else None

    def get_booked_item_ends_at(self, obj: EducationInstitutionBooking):
        _target_type, target = self._target(obj)
        return getattr(target, "ends_at", None) if target is not None else None

    def get_learner_display_name(self, obj: EducationInstitutionBooking):
        user = obj.user
        return getattr(user, "display_name", "") or getattr(user, "username", "") or getattr(user, "email", "") or ""


class EducationProfileCourseSerializer(serializers.ModelSerializer):
    class Meta:
        model = EducationProfileCourse
        fields = ["id", "title", "summary", "metadata", "created_at"]


class EducationProfileModuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = EducationProfileModule
        fields = ["id", "title", "summary", "resource_url", "created_at"]


class EducationProfileRoleAssignmentSerializer(serializers.ModelSerializer):
    user_id = serializers.UUIDField(source="user.id")
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = EducationProfileRoleAssignment
        fields = ["id", "user_id", "display_name", "created_at"]

    def get_display_name(self, obj: EducationProfileRoleAssignment):
        return obj.user.display_name if obj.user else None


class EducationProfileRoleSerializer(serializers.ModelSerializer):
    assignments = EducationProfileRoleAssignmentSerializer(many=True, read_only=True)

    class Meta:
        model = EducationProfileRole
        fields = ["id", "name", "permissions", "assignments", "created_at"]


class EducationProfileSerializer(serializers.ModelSerializer):
    courses = EducationProfileCourseSerializer(many=True, read_only=True)
    modules = EducationProfileModuleSerializer(many=True, read_only=True)
    roles = EducationProfileRoleSerializer(many=True, read_only=True)
    owner_id = serializers.UUIDField(source="user.id")

    class Meta:
        model = EducationProfile
        fields = [
            "id",
            "owner_id",
            "name",
            "description",
            "profile_type",
            "is_default",
            "courses",
            "modules",
            "roles",
            "metadata",
            "created_at",
            "updated_at",
        ]


class ChannelContentSubtitleSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelContentSubtitle
        fields = [
            "id",
            "content",
            "language",
            "label",
            "vtt_url",
            "segments",
            "is_auto_generated",
            "created_at",
        ]
        read_only_fields = ["id", "content", "created_at"]


class ChannelContentEndScreenSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelContentEndScreen
        fields = ["id", "content", "config", "is_enabled", "created_at", "updated_at"]
        read_only_fields = ["id", "content", "created_at", "updated_at"]


class ChannelContentCardSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelContentCard
        fields = [
            "id",
            "content",
            "card_type",
            "title",
            "start_seconds",
            "end_seconds",
            "target_id",
            "url",
            "sort_order",
        ]
        read_only_fields = ["id", "content"]


class ChannelActivityEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelActivityEvent
        fields = [
            "id",
            "channel",
            "event_type",
            "actor_display",
            "target_type",
            "target_id",
            "metadata",
            "created_at",
        ]
        read_only_fields = fields


class ChannelLivePollSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelLivePoll
        fields = [
            "id",
            "stream",
            "question",
            "options",
            "status",
            "created_at",
            "ended_at",
        ]
        read_only_fields = ["id", "stream", "created_at"]


class ChannelLiveQASerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelLiveQA
        fields = ["id", "stream", "status", "created_at", "ended_at"]
        read_only_fields = ["id", "stream", "created_at"]


class ChannelLiveQAQuestionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelLiveQAQuestion
        fields = [
            "id",
            "session",
            "user",
            "user_display",
            "question_text",
            "upvote_count",
            "is_answered",
            "is_pinned",
            "is_hidden",
            "created_at",
        ]
        read_only_fields = ["id", "session", "user", "upvote_count", "created_at"]


class BroadcastChannelSubscriptionDetailSerializer(serializers.ModelSerializer):
    user_display = serializers.SerializerMethodField()
    user_id = serializers.UUIDField(source="user.id", read_only=True)

    class Meta:
        model = BroadcastChannelSubscription
        fields = ["id", "user_id", "user_display", "notifications", "created_at", "updated_at"]
        read_only_fields = fields

    def get_user_display(self, obj: BroadcastChannelSubscription) -> str:
        user = obj.user
        return (
            getattr(user, "full_name", "")
            or getattr(user, "username", "")
            or getattr(user, "phone", "")
            or "KIS user"
        )


class ChannelContentTipSerializer(serializers.ModelSerializer):
    user_display_name = serializers.SerializerMethodField()
    amount_display = serializers.SerializerMethodField()

    class Meta:
        model = ChannelContentTip
        fields = ["id", "content", "user", "user_display_name", "amount_cents", "currency",
                  "message", "status", "amount_display", "created_at"]
        read_only_fields = ["id", "user", "status", "created_at"]

    def get_user_display_name(self, obj):
        return (getattr(getattr(obj.user, "profile", None), "display_name", None)
                or getattr(obj.user, "display_name", str(obj.user_id)))

    def get_amount_display(self, obj):
        return f"{obj.currency} {obj.amount_cents / 100:.2f}"


class ChannelLiveStreamTipSerializer(serializers.ModelSerializer):
    user_display_name = serializers.SerializerMethodField()
    amount_display = serializers.SerializerMethodField()

    class Meta:
        model = ChannelLiveStreamTip
        fields = ["id", "live_stream", "user", "user_display_name", "amount_cents", "currency",
                  "message", "status", "is_pinned", "pinned_until", "amount_display", "created_at"]
        read_only_fields = ["id", "user", "status", "is_pinned", "pinned_until", "created_at"]

    def get_user_display_name(self, obj):
        return (getattr(getattr(obj.user, "profile", None), "display_name", None)
                or getattr(obj.user, "display_name", str(obj.user_id)))

    def get_amount_display(self, obj):
        return f"{obj.currency} {obj.amount_cents / 100:.2f}"


class ChannelMonetizationSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelMonetizationSettings
        fields = ["tips_enabled", "membership_enabled", "ad_revenue_enabled",
                  "revenue_share_pct", "payout_threshold_cents", "payout_schedule", "updated_at"]


class ChannelPayoutRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelPayoutRequest
        fields = ["id", "channel", "requested_by", "amount_cents", "currency", "status",
                  "period_start", "period_end", "payment_method_ref", "notes", "processed_at", "created_at"]
        read_only_fields = ["id", "requested_by", "status", "processed_at", "created_at"]


class ChannelContentWatchEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelContentWatchEvent
        fields = ["id", "content", "session_id", "watch_percent",
                  "duration_watched_seconds", "source", "created_at"]
        read_only_fields = ["id", "created_at"]


class ChannelContentWatchSegmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelContentWatchSegment
        fields = ["segment_start_seconds", "segment_end_seconds", "view_count"]


class ChannelContentAudioTrackSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelContentAudioTrack
        fields = ["id", "content", "language_code", "label", "url", "is_default", "created_at"]
        read_only_fields = ["id", "created_at"]


class ChannelContentGeoRestrictionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelContentGeoRestriction
        fields = ["content", "restriction_type", "countries"]


class ChannelContentPremiereSerializer(serializers.ModelSerializer):
    seconds_until_premiere = serializers.SerializerMethodField()
    is_live_now = serializers.SerializerMethodField()

    class Meta:
        model = ChannelContentPremiere
        fields = ["content", "trailer_url", "pre_chat_opens_at", "lobby_conversation",
                  "viewer_count", "metadata", "seconds_until_premiere", "is_live_now",
                  "created_at", "updated_at"]
        read_only_fields = ["viewer_count", "lobby_conversation", "created_at", "updated_at"]

    def get_seconds_until_premiere(self, obj):
        from django.utils import timezone as _tz
        scheduled_at = getattr(obj.content, "scheduled_at", None) or getattr(obj.content, "published_at", None)
        if not scheduled_at:
            return None
        delta = (scheduled_at - _tz.now()).total_seconds()
        return max(0, int(delta))

    def get_is_live_now(self, obj):
        from django.utils import timezone as _tz
        scheduled_at = getattr(obj.content, "scheduled_at", None) or getattr(obj.content, "published_at", None)
        if not scheduled_at:
            return False
        return scheduled_at <= _tz.now()


class ChannelLiveStreamGuestSerializer(serializers.ModelSerializer):
    user_display_name = serializers.SerializerMethodField()
    invite_url = serializers.SerializerMethodField()

    class Meta:
        model = ChannelLiveStreamGuest
        fields = ["id", "live_stream", "user", "user_display_name", "email", "role",
                  "status", "invite_url", "accepted_at", "created_at"]
        read_only_fields = ["id", "invite_token", "accepted_at", "created_at"]

    def get_user_display_name(self, obj):
        if not obj.user:
            return obj.email or "Guest"
        return (getattr(getattr(obj.user, "profile", None), "display_name", None)
                or getattr(obj.user, "display_name", str(obj.user_id)))

    def get_invite_url(self, obj):
        request = self.context.get("request")
        if not request:
            return None
        return request.build_absolute_uri(f"/broadcasts/live/join/{obj.invite_token}/")


class ChannelContentTranscriptSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelContentTranscript
        fields = ["id", "content", "language_code", "source", "status",
                  "text_plain", "vtt_url", "provider", "created_at", "updated_at"]
        read_only_fields = ["id", "status", "vtt_url", "provider", "provider_job_id",
                            "created_at", "updated_at"]


class ChannelContentProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelContentProduct
        fields = ["id", "content", "product_id", "product_url", "product_title",
                  "thumbnail_url", "price_display", "timestamp_seconds", "created_at"]
        read_only_fields = ["id", "created_at"]


class ChannelLiveStreamTargetSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelLiveStreamTarget
        fields = ["id", "live_stream", "platform", "label", "rtmp_url", "status", "created_at"]
        read_only_fields = ["id", "status", "created_at"]
        extra_kwargs = {"stream_key": {"write_only": True}}


class ChannelMembershipGiftSerializer(serializers.ModelSerializer):
    tier_title = serializers.SerializerMethodField()
    gifter_display = serializers.SerializerMethodField()

    class Meta:
        model = ChannelMembershipGift
        fields = ["id", "tier", "tier_title", "gifter", "gifter_display", "recipient",
                  "recipient_email", "message", "status", "expires_at", "redeemed_at", "created_at"]
        read_only_fields = ["id", "gifter", "status", "redeemed_at", "redeem_token", "created_at"]

    def get_tier_title(self, obj):
        return getattr(obj.tier, "title", "")

    def get_gifter_display(self, obj):
        return (getattr(getattr(obj.gifter, "profile", None), "display_name", None)
                or getattr(obj.gifter, "display_name", str(obj.gifter_id)))


class ChannelContentCopyrightClaimSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelContentCopyrightClaim
        fields = ["id", "content", "claimant_channel", "claimant_name", "claim_type",
                  "status", "dispute_reason", "resolution_notes", "resolved_at", "created_at"]
        read_only_fields = ["id", "status", "resolution_notes", "resolved_at", "created_at"]


class ChannelContentDemographicSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelContentDemographicSnapshot
        fields = ["id", "snapshot_date", "age_bucket", "country_code", "view_count", "watch_time_seconds"]
        read_only_fields = ["id"]


class ChannelAdCampaignSerializer(serializers.ModelSerializer):
    remaining_budget_cents = serializers.SerializerMethodField()

    class Meta:
        model = ChannelAdCampaign
        fields = ["id", "advertiser_channel", "title", "budget_cents", "spent_cents", "currency",
                  "start_date", "end_date", "target_content_types", "target_countries",
                  "target_age_buckets", "status", "remaining_budget_cents", "created_at"]
        read_only_fields = ["id", "spent_cents", "created_at"]

    def get_remaining_budget_cents(self, obj):
        return max(0, (obj.budget_cents or 0) - (obj.spent_cents or 0))


class ChannelAdSlotSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelAdSlot
        fields = ["id", "content", "campaign", "placement", "timestamp_seconds",
                  "is_skippable", "skip_after_seconds", "ad_media_url", "click_url",
                  "impression_count", "skip_count", "created_at"]
        read_only_fields = ["id", "impression_count", "skip_count", "created_at"]


class ChannelAdImpressionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelAdImpression
        fields = ["id", "slot", "watched_seconds", "skipped", "clicked", "created_at"]
        read_only_fields = ["id", "created_at"]


class ChannelContentQueueSerializer(serializers.ModelSerializer):
    content_title = serializers.SerializerMethodField()
    content_thumbnail = serializers.SerializerMethodField()
    channel_name = serializers.SerializerMethodField()

    class Meta:
        model = ChannelContentQueue
        fields = ["id", "content", "content_title", "content_thumbnail", "channel_name", "position", "added_at"]
        read_only_fields = ["id", "added_at"]

    def get_content_title(self, obj):
        return getattr(obj.content, "title", "")

    def get_content_thumbnail(self, obj):
        return getattr(obj.content, "thumbnail_url", "")

    def get_channel_name(self, obj):
        return getattr(getattr(obj.content, "channel", None), "name", "")


class ChannelContentAutoChapterSuggestionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelContentAutoChapterSuggestion
        fields = ["id", "content", "status", "suggestions", "created_at", "updated_at"]
        read_only_fields = ["id", "status", "created_at", "updated_at"]


# ─── Round-3 YouTube-parity serializers ──────────────────────────────────────

class ChannelLiveStreamLatencySerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelLiveStream
        fields = ['id', 'latency_mode', 'dvr_enabled', 'dvr_window_seconds', 'updated_at']
        read_only_fields = ['id', 'updated_at']


class ChannelContentTrafficSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelContentTrafficSource
        fields = ['id', 'content', 'date', 'source_type', 'view_count', 'watch_time_seconds', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class ChannelKeywordFilterSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelKeywordFilter
        fields = ['id', 'channel', 'keyword', 'filter_type', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'channel', 'created_at', 'updated_at']


class ChannelHomepageShelfSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelHomepageShelf
        fields = ['id', 'channel', 'title', 'shelf_type', 'sort_order', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'channel', 'created_at', 'updated_at']


class ChannelHomepageShelfItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelHomepageShelfItem
        fields = ['id', 'shelf', 'content', 'playlist', 'sort_order', 'created_at']
        read_only_fields = ['id', 'shelf', 'created_at']


class ChannelCategorySerializer(serializers.ModelSerializer):
    subcategories = serializers.SerializerMethodField()

    class Meta:
        model = ChannelCategory
        fields = ['id', 'name', 'slug', 'description', 'icon_name', 'parent', 'sort_order', 'is_active', 'created_at', 'subcategories']
        read_only_fields = ['id', 'created_at']

    def get_subcategories(self, obj):
        children = obj.subcategories.filter(is_active=True)
        return ChannelCategorySerializer(children, many=True).data


class ChannelContentFingerprintSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelContentFingerprint
        fields = ['id', 'content', 'algorithm', 'fingerprint_hash', 'status', 'created_at', 'updated_at']
        read_only_fields = ['id', 'content', 'status', 'created_at', 'updated_at']


class ChannelFingerprintMatchSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChannelFingerprintMatch
        fields = ['id', 'source_fingerprint', 'matched_fingerprint', 'similarity_score', 'claim', 'created_at']
        read_only_fields = ['id', 'created_at']
