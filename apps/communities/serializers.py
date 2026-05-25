# apps/communities/serializers.py
from django.db import models
from rest_framework import serializers

from apps.chat.discussion import get_discussion_count
from apps.communities.models import (
    Community,
    CommunityMembership,
    CommunityJoinRequest,
    CommunityBan,
    CommunityPost,
    CommunityPostComment,
    CommunityPostReaction,
    CommunityCommentReaction,
    CommunityRole,
)
from apps.chat.models import ConversationType  # must include POST
from apps.accounts.models import User
from common.rich_text import build_plain_text_document, process_rich_text_document
from common.media_urls import absolutize_backend_media, normalize_image_payload
from django.utils.text import slugify


class CommunityImageUrlSerializerMixin:
    def to_representation(self, instance):
        payload = super().to_representation(instance)
        if "avatar_url" in payload:
            payload["avatar_url"] = absolutize_backend_media(
                payload.get("avatar_url"),
                request=self.context.get("request"),
            )
        return payload

    def validate_avatar_url(self, value):
        return normalize_image_payload(value)


class CommunityUserSerializer(serializers.ModelSerializer):
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "display_name", "phone", "avatar_url"]

    def get_avatar_url(self, obj):
        profile = getattr(obj, "profile", None)
        return absolutize_backend_media(
            getattr(profile, "avatar_url", None) if profile else None,
            request=self.context.get("request"),
        )


class CommunityListSerializer(CommunityImageUrlSerializerMixin, serializers.ModelSerializer):
    main_conversation_id = serializers.UUIDField(
        source="main_conversation.id",
        read_only=True,
    )
    posts_conversation_id = serializers.UUIDField(
        source="posts_conversation.id",
        read_only=True,
        required=False,
    )

    class Meta:
        model = Community
        fields = [
            "id",
            "name",
            "slug",
            "avatar_url",
            "is_active",
            "allow_broadcasts",
            "partner",
            "main_conversation_id",
            "posts_conversation_id",
            "created_at",
            "updated_at",
        ]


class CommunityDetailSerializer(CommunityImageUrlSerializerMixin, serializers.ModelSerializer):
    main_conversation_id = serializers.UUIDField(
        source="main_conversation.id",
        read_only=True,
    )
    posts_conversation_id = serializers.UUIDField(
        source="posts_conversation.id",
        read_only=True,
        required=False,
    )

    class Meta:
        model = Community
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "avatar_url",
            "partner",
            "owner",
            "visibility",
            "join_policy",
            "post_policy",
            "allow_comments",
            "allow_reactions",
            "allow_media",
            "allow_polls",
            "allow_events",
            "allow_links",
            "require_post_approval",
            "allow_post_link_copy",
            "allow_join_link",
            "require_join_survey",
            "allow_broadcasts",
            "is_active",
            "main_conversation_id",
            "posts_conversation_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "owner",
            "main_conversation_id",
            "posts_conversation_id",
            "created_at",
            "updated_at",
        ]


class CommunityCreateSerializer(CommunityImageUrlSerializerMixin, serializers.ModelSerializer):
    """
    For creating a community under a partner.

    Payload example:
        {
          "partner": "<partner_id>",
          "name": "KIS Dev Community",
          "slug": "kis-dev",
          "description": "Community for devs",
          "avatar_url": "https://...",
          "create_main_conversation": true,
          "create_posts_conversation": true
        }
    """

    create_main_conversation = serializers.BooleanField(
        default=True,
        write_only=True,
        help_text="If true, create a main (chat) conversation for this community.",
    )
    create_posts_conversation = serializers.BooleanField(
        default=True,
        write_only=True,
        help_text="If true, create a posts conversation (feed) for this community.",
    )
    main_conversation_id = serializers.UUIDField(
        source="main_conversation.id",
        read_only=True,
    )
    posts_conversation_id = serializers.UUIDField(
        source="posts_conversation.id",
        read_only=True,
        required=False,
    )

    class Meta:
        model = Community
        fields = [
            "id",
            "partner",
            "name",
            "slug",
            "description",
            "avatar_url",
            "create_main_conversation",
            "create_posts_conversation",
            "main_conversation_id",
            "posts_conversation_id",
        ]

    def validate(self, attrs):
        name = (attrs.get("name") or "").strip()
        raw_slug = (attrs.get("slug") or "").strip()
        base = slugify(raw_slug or name or "community")
        attrs["slug"] = self._ensure_unique_slug(base)
        return attrs

    def _ensure_unique_slug(self, base_slug: str) -> str:
        candidate = base_slug or "community"
        suffix = 0
        while Community.objects.filter(slug=candidate).exists():
            suffix += 1
            candidate = f"{base_slug}-{suffix}"
        return candidate

    def create(self, validated_data):
        request = self.context["request"]
        user = request.user

        create_main_conversation = validated_data.pop(
            "create_main_conversation",
            True,
        )
        create_posts_conversation = validated_data.pop(
            "create_posts_conversation",
            True,
        )

        from apps.chat.models import (
            Conversation,
            ConversationSettings,
            ConversationMember,
            BaseConversationRole,
        )

        name = validated_data.get("name", "").strip() or "Community"

        main_conversation = None
        posts_conversation = None

        # --- Main chat / lobby (group-style) ---
        if create_main_conversation:
            main_conversation = Conversation.objects.create(
                type=ConversationType.GROUP,
                title=name,
                created_by=user,
            )

            ConversationMember.objects.create(
                conversation=main_conversation,
                user=user,
                base_role=BaseConversationRole.OWNER,
            )

            ConversationSettings.objects.create(conversation=main_conversation)

        # --- Posts feed conversation ---
        if create_posts_conversation:
            posts_conversation = Conversation.objects.create(
                type=ConversationType.POST,
                title=f"{name} posts",
                created_by=user,
            )

            ConversationMember.objects.create(
                conversation=posts_conversation,
                user=user,
                base_role=BaseConversationRole.OWNER,
            )

            ConversationSettings.objects.create(conversation=posts_conversation)

        community = Community.objects.create(
            owner=user,
            main_conversation=main_conversation,
            posts_conversation=posts_conversation,
            **validated_data,
        )

        CommunityMembership.objects.create(
            community=community,
            user=user,
            role=CommunityRole.OWNER,
        )

        return community


class CommunityMembershipSerializer(serializers.ModelSerializer):
    user = CommunityUserSerializer(read_only=True)

    class Meta:
        model = CommunityMembership
        fields = [
            "id",
            "community",
            "user",
            "role",
            "joined_at",
            "left_at",
            "is_muted",
            "is_banned",
            "can_access_all_groups",
        ]
        read_only_fields = ["joined_at", "left_at", "is_banned"]


def prepare_rich_text_attrs(attrs):
    styled = attrs.pop("styled_text", None)
    text_payload = attrs.pop("text_doc", None) or attrs.get("text")
    if not text_payload and styled:
        text_payload = build_plain_text_document(styled.get("text", ""))
    if not text_payload:
        text_payload = build_plain_text_document("")

    doc, plain, preview = process_rich_text_document(text_payload)
    attrs["text"] = doc
    attrs["text_plain"] = plain
    attrs["text_preview"] = preview
    return attrs


class CommunityJoinRequestSerializer(serializers.ModelSerializer):
    user = CommunityUserSerializer(read_only=True)
    reviewed_by = CommunityUserSerializer(read_only=True)

    class Meta:
        model = CommunityJoinRequest
        fields = [
            "id",
            "community",
            "user",
            "message",
            "status",
            "reviewed_by",
            "reviewed_at",
            "created_at",
        ]
        read_only_fields = ["status", "reviewed_by", "reviewed_at", "created_at"]


class CommunityBanSerializer(serializers.ModelSerializer):
    user = CommunityUserSerializer(read_only=True)
    banned_by = CommunityUserSerializer(read_only=True)

    class Meta:
        model = CommunityBan
        fields = [
            "id",
            "community",
            "user",
            "reason",
            "banned_by",
            "banned_at",
            "expires_at",
        ]
        read_only_fields = ["banned_at"]


class CommunityPostSerializer(serializers.ModelSerializer):
    author = CommunityUserSerializer(read_only=True)
    reactions = serializers.SerializerMethodField()
    comments_count = serializers.SerializerMethodField()
    comment_conversation_id = serializers.UUIDField(read_only=True)
    has_reacted = serializers.SerializerMethodField()

    class Meta:
        model = CommunityPost
        fields = [
            "id",
            "community",
            "author",
            "text",
            "text_plain",
            "text_preview",
            "attachments",
            "poll",
            "event",
            "link",
            "status",
            "is_pinned",
            "is_broadcast",
            "comment_conversation_id",
            "pinned_by",
            "pinned_at",
            "is_deleted",
            "created_at",
            "updated_at",
            "reactions",
            "comments_count",
            "has_reacted",
        ]
        read_only_fields = [
            "author",
            "status",
            "is_pinned",
            "is_broadcast",
            "pinned_by",
            "pinned_at",
            "is_deleted",
            "created_at",
            "updated_at",
            "reactions",
            "comments_count",
            "text_plain",
            "text_preview",
        ]

    def validate(self, attrs):
        if "text" in attrs or "styled_text" in attrs:
            attrs = prepare_rich_text_attrs(attrs)
        return super().validate(attrs)

    def get_reactions(self, obj):
        qs = obj.reactions.values("emoji").annotate(count=models.Count("id"))
        return list(qs)

    def get_comments_count(self, obj):
        return get_discussion_count(
            obj,
            legacy_comment_queryset=obj.comments.filter(is_deleted=False),
        )

    def get_has_reacted(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or user.is_anonymous:
            return False
        return obj.reactions.filter(user=user).exists()


class CommunityPostCreateSerializer(serializers.ModelSerializer):
    text = serializers.JSONField(required=False)

    class Meta:
        model = CommunityPost
        fields = [
            "id",
            "community",
            "text",
            "attachments",
            "poll",
            "event",
            "link",
        ]

    def validate(self, attrs):
        attrs = prepare_rich_text_attrs(attrs)
        return super().validate(attrs)

    def create(self, validated_data):
        return CommunityPost.objects.create(**validated_data)


class CommunityPostCommentSerializer(serializers.ModelSerializer):
    author = CommunityUserSerializer(read_only=True)

    class Meta:
        model = CommunityPostComment
        fields = [
            "id",
            "post",
            "author",
            "text",
            "is_deleted",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["author", "is_deleted", "created_at", "updated_at"]


class CommunityPostReactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = CommunityPostReaction
        fields = ["id", "post", "user", "emoji", "created_at"]
        read_only_fields = ["created_at", "user"]


class CommunityCommentReactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = CommunityCommentReaction
        fields = ["id", "comment", "user", "emoji", "created_at"]
        read_only_fields = ["created_at", "user"]
