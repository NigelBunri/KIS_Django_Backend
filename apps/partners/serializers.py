# apps/partners/serializers.py
from django.db import models
from rest_framework import serializers
from apps.media.safety import validate_attachment_metadata_for_safe_messaging

from apps.partners.models import (
    Partner,
    PartnerPost,
    PartnerPostComment,
    PartnerPostReaction,
    PartnerJoinConfig,
    PartnerMembership,
    PartnerApplication,
    PartnerInvite,
    PartnerJobPost,
    PartnerOnboardingProgress,
    PartnerPolicy,
    PartnerRole,
    PartnerRoleAssignment,
    PartnerAuditEvent,
    PartnerIntegration,
    PartnerWebhook,
    PartnerWebhookDelivery,
    PartnerAutomationRule,
    PartnerReportSnapshot,
    PartnerExportJob,
    PartnerAccessRequest,
    PartnerAccessReview,
    PartnerExportSchedule,
    PartnerSetting,
    PartnerOrganizationProfile,
    PartnerOrganizationApp,
    PartnerOrganizationAppTab,
    PartnerOrganizationAppContentBlock,
    PartnerOrganizationAppAccessLog,
    PartnerOrganizationAppStatus,
    PartnerProfileLink,
    PartnerServerCategory,
    PartnerChannelPermissionOverwrite,
    PartnerModerationAction,
    PARTNER_ORG_APP_VISIBILITY_ROLES,
    default_app_visibility,
)
from apps.chat.models import ConversationType
from apps.chat.discussion import get_discussion_count
from apps.chat.models import ConversationMember, BaseConversationRole
from common.media_urls import absolutize_backend_media, normalize_image_payload
from common.rich_text import build_plain_text_document, process_rich_text_document


class PartnerImageUrlSerializerMixin:
    image_url_fields = ("avatar_url", "logo_url")

    def to_representation(self, instance):
        payload = super().to_representation(instance)
        request = self.context.get("request")
        for field in self.image_url_fields:
            if field in payload:
                payload[field] = absolutize_backend_media(payload.get(field), request=request)
        return payload

    def validate_avatar_url(self, value):
        return normalize_image_payload(value)

    def validate_logo_url(self, value):
        return normalize_image_payload(value)


class PartnerListSerializer(PartnerImageUrlSerializerMixin, serializers.ModelSerializer):
    main_conversation_id = serializers.UUIDField(
        source="main_conversation.id",
        read_only=True,
    )
    member_role = serializers.SerializerMethodField()
    deactivation_source = serializers.CharField(read_only=True)
    deactivated_at = serializers.DateTimeField(read_only=True)
    grace_expires_at = serializers.DateTimeField(read_only=True)
    can_reactivate = serializers.SerializerMethodField()
    verification_summary = serializers.SerializerMethodField()

    class Meta:
        model = Partner
        fields = [
            "id",
            "name",
            "slug",
            "avatar_url",
            "is_active",
            "deactivation_source",
            "deactivated_at",
            "grace_expires_at",
            "can_reactivate",
            "verification_summary",
            "main_conversation_id",
            "member_role",
            "created_at",
            "updated_at",
        ]

    def get_member_role(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or user.is_anonymous:
            return None
        if obj.owner_id == user.id:
            return BaseConversationRole.OWNER
        if not obj.main_conversation_id:
            return None
        member = ConversationMember.objects.filter(
            conversation_id=obj.main_conversation_id,
            user=user,
            left_at__isnull=True,
        ).first()
        return member.base_role if member else None

    def get_can_reactivate(self, obj):
        if obj.is_active:
            return False
        return obj.deactivation_source != Partner.DeactivationSource.SYSTEM

    def get_verification_summary(self, obj):
        from apps.verification.services import current_partner_verification_status

        return current_partner_verification_status(obj)


class PartnerJoinConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartnerJoinConfig
        fields = [
            "allow_public_listing",
            "allow_apply",
            "allow_subscribe",
            "auto_approve",
            "require_profile",
            "methods",
            "criteria",
            "updated_at",
        ]


class PartnerDiscoverSerializer(PartnerImageUrlSerializerMixin, serializers.ModelSerializer):
    main_conversation_id = serializers.UUIDField(
        source="main_conversation.id",
        read_only=True,
    )
    join_config = PartnerJoinConfigSerializer(read_only=True)
    membership_status = serializers.SerializerMethodField()
    application_status = serializers.SerializerMethodField()
    verification_summary = serializers.SerializerMethodField()

    class Meta:
        model = Partner
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "avatar_url",
            "is_active",
            "main_conversation_id",
            "join_config",
            "membership_status",
            "application_status",
            "verification_summary",
            "created_at",
            "updated_at",
        ]

    def get_membership_status(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or user.is_anonymous:
            return None
        membership = PartnerMembership.objects.filter(partner=obj, user=user).first()
        return membership.status if membership else None

    def get_application_status(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or user.is_anonymous:
            return None
        application = PartnerApplication.objects.filter(partner=obj, user=user).order_by("-created_at").first()
        return application.status if application else None

    def get_verification_summary(self, obj):
        from apps.verification.services import current_partner_verification_status

        return current_partner_verification_status(obj)


class PartnerDetailSerializer(PartnerImageUrlSerializerMixin, serializers.ModelSerializer):
    main_conversation_id = serializers.UUIDField(
        source="main_conversation.id",
        read_only=True,
    )
    admins = serializers.SerializerMethodField()
    member_role = serializers.SerializerMethodField()
    verification_summary = serializers.SerializerMethodField()

    class Meta:
        model = Partner
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "avatar_url",
            "owner",
            "is_active",
            "deactivation_source",
            "deactivated_at",
            "grace_expires_at",
            "main_conversation_id",
            "admins",
            "member_role",
            "verification_summary",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "owner",
            "main_conversation_id",
            "created_at",
            "updated_at",
        ]

    def get_admins(self, obj):
        if not obj.main_conversation_id:
            return []
        members = (
            ConversationMember.objects
            .select_related("user", "user__profile")
            .filter(
                conversation_id=obj.main_conversation_id,
                left_at__isnull=True,
                base_role__in=[BaseConversationRole.OWNER, BaseConversationRole.ADMIN],
            )
        )
        admins = []
        for member in members:
            user = member.user
            profile = getattr(user, "profile", None)
            name = getattr(user, "display_name", None) or getattr(user, "username", None) or str(user.id)
            initials = "".join([part[0].upper() for part in str(name).split()[:2] if part]) or "??"
            admins.append(
                {
                    "id": str(user.id),
                    "name": name,
                    "initials": initials,
                    "position": member.base_role,
                    "avatarUrl": getattr(profile, "avatar_url", None) if profile else None,
                }
            )
        return admins

    def get_member_role(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or user.is_anonymous:
            return None
        if obj.owner_id == user.id:
            return BaseConversationRole.OWNER
        if not obj.main_conversation_id:
            return None
        member = ConversationMember.objects.filter(
            conversation_id=obj.main_conversation_id,
            user=user,
            left_at__isnull=True,
        ).first()
        return member.base_role if member else None

    def get_verification_summary(self, obj):
        from apps.verification.services import current_partner_verification_status

        return current_partner_verification_status(obj)


class PartnerCreateSerializer(PartnerImageUrlSerializerMixin, serializers.ModelSerializer):
    """
    Used for creating a Partner. Optionally also creates a main POST Conversation.

    Payload example:
        {
          "name": "Kingdom Impact Global",
          "slug": "kingdom-impact-global",
          "description": "...",
          "avatar_url": "https://...",
          "create_main_conversation": true
        }
    """

    create_main_conversation = serializers.BooleanField(
        default=True,
        required=False,          # 👈 important: don't force client to send it
        write_only=True,
        help_text="If true, create a main POST conversation for this partner.",
    )

    class Meta:
        model = Partner
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "avatar_url",
            "create_main_conversation",
        ]

    def validate_slug(self, value):
        # Optionally add custom slug rules here
        return value

    def create(self, validated_data):
        request = self.context["request"]
        user = request.user

        # Default to True if not provided
        create_main_conversation = validated_data.pop("create_main_conversation", True)

        from apps.chat.models import (
            Conversation,
            ConversationSettings,
            ConversationMember,
            BaseConversationRole,
        )

        main_conversation = None

        if create_main_conversation:
            # Create a POST-style conversation for this partner
            main_conversation = Conversation.objects.create(
                type=ConversationType.POST,  # 👈 POST conversation as requested
                title=validated_data.get("name", ""),
                description=f"Post space for partner {validated_data.get('name', '')}",
                created_by=user,
            )

            # Make the creator the owner/primary member
            ConversationMember.objects.create(
                conversation=main_conversation,
                user=user,
                base_role=BaseConversationRole.OWNER,
            )

            # Default settings for this conversation
            ConversationSettings.objects.create(conversation=main_conversation)

        # Create the Partner linked to this main_conversation
        partner = Partner.objects.create(
            owner=user,
            main_conversation=main_conversation,
            **validated_data,
        )
        PartnerJoinConfig.objects.get_or_create(partner=partner)
        from apps.partners.services import (
            ensure_partner_policy,
            ensure_default_partner_roles,
            apply_partner_policy,
        )
        ensure_partner_policy(partner)
        ensure_default_partner_roles(partner)
        apply_partner_policy(partner)
        PartnerOrganizationProfile.objects.get_or_create(
            partner=partner,
            defaults={
                "display_name": partner.name,
                "updated_by": user,
            },
        )

        return partner


def prepare_partner_rich_text_attrs(attrs):
    styled = attrs.pop("styled_text", None)
    text_payload = attrs.get("text")
    if not text_payload and styled:
        text_payload = build_plain_text_document(styled.get("text", ""))
    if not text_payload:
        text_payload = build_plain_text_document("")

    doc, plain, preview = process_rich_text_document(text_payload)
    attrs["text"] = doc
    attrs["text_plain"] = plain
    attrs["text_preview"] = preview
    return attrs


class PartnerPostSerializer(serializers.ModelSerializer):
    author = serializers.SerializerMethodField()
    reactions = serializers.SerializerMethodField()
    comments_count = serializers.SerializerMethodField()
    comment_conversation_id = serializers.UUIDField(read_only=True)
    has_reacted = serializers.SerializerMethodField()

    class Meta:
        model = PartnerPost
        fields = [
            "id",
            "partner",
            "author",
            "text",
            "text_plain",
            "text_preview",
            "attachments",
            "poll",
            "event",
            "link",
            "is_broadcast",
            "comment_conversation_id",
            "is_deleted",
            "created_at",
            "updated_at",
            "reactions",
            "comments_count",
            "has_reacted",
        ]
        read_only_fields = [
            "is_broadcast",
            "is_deleted",
            "comment_conversation_id",
            "created_at",
            "updated_at",
            "reactions",
            "comments_count",
            "text_plain",
            "text_preview",
        ]

    def get_author(self, obj):
        author = obj.author
        profile = getattr(author, "profile", None)
        return {
            "id": str(author.id),
            "display_name": getattr(author, "display_name", None),
            "phone": getattr(author, "phone", None),
            "avatar_url": getattr(profile, "avatar_url", None) if profile else None,
        }

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

    def validate(self, attrs):
        if "text" in attrs or "styled_text" in attrs:
            attrs = prepare_partner_rich_text_attrs(attrs)
        validate_attachment_metadata_for_safe_messaging(attrs.get("attachments"))
        return super().validate(attrs)


class PartnerPostCreateSerializer(serializers.ModelSerializer):
    text = serializers.JSONField(required=False)

    class Meta:
        model = PartnerPost
        fields = [
            "id",
            "partner",
            "text",
            "attachments",
            "poll",
            "event",
            "link",
        ]

    def validate(self, attrs):
        attrs = prepare_partner_rich_text_attrs(attrs)
        validate_attachment_metadata_for_safe_messaging(attrs.get("attachments"))
        return super().validate(attrs)

    def create(self, validated_data):
        request = self.context["request"]
        user = request.user
        return PartnerPost.objects.create(author=user, **validated_data)


class PartnerPostCommentSerializer(serializers.ModelSerializer):
    author = serializers.SerializerMethodField()

    class Meta:
        model = PartnerPostComment
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

    def get_author(self, obj):
        author = obj.author
        profile = getattr(author, "profile", None)
        return {
            "id": str(author.id),
            "display_name": getattr(author, "display_name", None),
            "phone": getattr(author, "phone", None),
            "avatar_url": getattr(profile, "avatar_url", None) if profile else None,
        }


class PartnerPostReactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartnerPostReaction
        fields = ["id", "post", "user", "emoji", "created_at"]
        read_only_fields = ["created_at", "user"]


class PartnerApplicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartnerApplication
        fields = [
            "id",
            "partner",
            "job_post",
            "user",
            "method",
            "message",
            "answers",
            "profile_visible",
            "status",
            "stage_index",
            "stage_state",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["partner", "user", "status", "created_at", "updated_at"]


class PartnerApplicationDetailSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()

    class Meta:
        model = PartnerApplication
        fields = [
            "id",
            "partner",
            "job_post",
            "user",
            "method",
            "message",
            "answers",
            "profile_visible",
            "status",
            "stage_index",
            "stage_state",
            "created_at",
            "updated_at",
        ]

    def get_user(self, obj):
        if not obj.profile_visible:
            return {"id": str(obj.user_id)}
        user = obj.user
        profile = getattr(user, "profile", None)
        return {
            "id": str(user.id),
            "display_name": getattr(user, "display_name", None),
            "phone": getattr(user, "phone", None),
            "avatar_url": getattr(profile, "avatar_url", None) if profile else None,
        }


class PartnerInviteSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()
    is_expired = serializers.BooleanField(read_only=True)
    has_uses_remaining = serializers.BooleanField(read_only=True)
    is_redeemable = serializers.SerializerMethodField()

    class Meta:
        model = PartnerInvite
        fields = [
            "id",
            "partner",
            "code",
            "label",
            "created_by",
            "created_by_name",
            "max_uses",
            "use_count",
            "expires_at",
            "is_active",
            "membership_role",
            "auto_assign",
            "metadata",
            "is_expired",
            "has_uses_remaining",
            "is_redeemable",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "partner",
            "code",
            "created_by",
            "created_by_name",
            "use_count",
            "is_expired",
            "has_uses_remaining",
            "is_redeemable",
            "created_at",
            "updated_at",
        ]

    def get_created_by_name(self, obj):
        user = obj.created_by
        if not user:
            return None
        return getattr(user, "display_name", None) or getattr(user, "username", None) or str(user.id)

    def get_is_redeemable(self, obj):
        return obj.is_redeemable()


class PartnerOnboardingProgressSerializer(serializers.ModelSerializer):
    invite_code = serializers.CharField(source="invite.code", read_only=True)

    class Meta:
        model = PartnerOnboardingProgress
        fields = [
            "id",
            "partner",
            "user",
            "invite",
            "invite_code",
            "rules_accepted_at",
            "selected_role_ids",
            "selected_channel_ids",
            "onboarding_snapshot",
            "completed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "partner",
            "user",
            "invite_code",
            "created_at",
            "updated_at",
        ]


class PartnerModerationActionSerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()
    user_name = serializers.SerializerMethodField()

    class Meta:
        model = PartnerModerationAction
        fields = [
            "id",
            "partner",
            "user",
            "user_name",
            "actor",
            "actor_name",
            "membership",
            "action_type",
            "reason",
            "expires_at",
            "metadata",
            "revoked_at",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "partner",
            "actor",
            "actor_name",
            "user_name",
            "membership",
            "revoked_at",
            "created_at",
        ]

    def get_actor_name(self, obj):
        actor = obj.actor
        if not actor:
            return None
        return getattr(actor, "display_name", None) or getattr(actor, "username", None) or str(actor.id)

    def get_user_name(self, obj):
        user = obj.user
        return getattr(user, "display_name", None) or getattr(user, "username", None) or str(user.id)


class PartnerMemberDirectoryEntrySerializer(serializers.Serializer):
    user_id = serializers.CharField()
    display_name = serializers.CharField(allow_blank=True, allow_null=True)
    username = serializers.CharField(allow_blank=True, allow_null=True)
    avatar_url = serializers.CharField(allow_blank=True, allow_null=True)
    membership_status = serializers.CharField()
    membership_role = serializers.CharField()
    role_names = serializers.ListField(child=serializers.CharField(), default=list)
    is_muted = serializers.BooleanField()
    is_banned = serializers.BooleanField()
    timed_out_until = serializers.DateTimeField(allow_null=True)
    joined_at = serializers.DateTimeField(allow_null=True)


class PartnerJobPostSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartnerJobPost
        fields = [
            "id",
            "partner",
            "title",
            "description",
            "requirements",
            "steps",
            "auto_assign",
            "is_active",
            "created_at",
            "updated_at",
        ]


class PartnerPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = PartnerPolicy
        fields = ["id", "partner", "settings", "updated_at"]
        read_only_fields = ["id", "partner", "updated_at"]


class PartnerRoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartnerRole
        fields = [
            "id",
            "partner",
            "name",
            "description",
            "permissions",
            "parent_role",
            "is_default",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "partner", "created_at", "updated_at"]


class PartnerRoleAssignmentSerializer(serializers.ModelSerializer):
    role_detail = PartnerRoleSerializer(source="role", read_only=True)

    class Meta:
        model = PartnerRoleAssignment
        fields = [
            "id",
            "partner",
            "role",
            "role_detail",
            "user",
            "scope_type",
            "scope_id",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class PartnerAuditEventSerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = PartnerAuditEvent
        fields = [
            "id",
            "partner",
            "actor",
            "actor_name",
            "action",
            "target_type",
            "target_id",
            "ip_address",
            "user_agent",
            "metadata",
            "created_at",
        ]

    def get_actor_name(self, obj):
        actor = obj.actor
        if not actor:
            return None
        return getattr(actor, "display_name", None) or getattr(actor, "username", None) or str(actor.id)


class PartnerIntegrationSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartnerIntegration
        fields = [
            "id",
            "partner",
            "kind",
            "provider",
            "config",
            "is_enabled",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "partner", "created_at", "updated_at"]


class PartnerWebhookSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartnerWebhook
        fields = [
            "id",
            "partner",
            "name",
            "url",
            "events",
            "secret",
            "is_active",
            "retry_limit",
            "retry_backoff_seconds",
            "last_sent_at",
            "last_error",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "partner", "last_sent_at", "last_error", "created_at", "updated_at"]


class PartnerWebhookDeliverySerializer(serializers.ModelSerializer):
    class Meta:
        model = PartnerWebhookDelivery
        fields = [
            "id",
            "webhook",
            "event",
            "payload",
            "status",
            "attempt_count",
            "next_retry_at",
            "response_code",
            "error_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class PartnerAutomationRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartnerAutomationRule
        fields = [
            "id",
            "partner",
            "name",
            "description",
            "trigger",
            "conditions",
            "actions",
            "is_active",
            "created_by",
            "created_at",
            "updated_at",
            "last_run_at",
            "last_run_status",
            "last_run_message",
        ]
        read_only_fields = [
            "id",
            "partner",
            "created_by",
            "created_at",
            "updated_at",
            "last_run_at",
            "last_run_status",
            "last_run_message",
        ]


class PartnerReportSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartnerReportSnapshot
        fields = [
            "id",
            "partner",
            "kind",
            "data",
            "created_by",
            "created_at",
        ]
        read_only_fields = ["id", "partner", "created_by", "created_at"]


class PartnerExportJobSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = PartnerExportJob
        fields = [
            "id",
            "partner",
            "kind",
            "export_format",
            "status",
            "file_path",
            "file_url",
            "metadata",
            "error_message",
            "created_by",
            "created_at",
            "finished_at",
        ]
        read_only_fields = [
            "id",
            "partner",
            "status",
            "file_path",
            "file_url",
            "error_message",
            "created_by",
            "created_at",
            "finished_at",
        ]

    def get_file_url(self, obj):
        request = self.context.get("request")
        if not obj.file_path:
            return None
        if request:
            return request.build_absolute_uri(f"/media/{obj.file_path}")
        return f"/media/{obj.file_path}"


class PartnerAccessRequestSerializer(serializers.ModelSerializer):
    requester_name = serializers.SerializerMethodField()
    target_name = serializers.SerializerMethodField()

    class Meta:
        model = PartnerAccessRequest
        fields = [
            "id",
            "partner",
            "requester",
            "requester_name",
            "target_user",
            "target_name",
            "requested_role",
            "scope_type",
            "scope_id",
            "justification",
            "status",
            "decided_by",
            "decided_at",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "partner",
            "requester",
            "status",
            "decided_by",
            "decided_at",
            "created_at",
        ]

    def get_requester_name(self, obj):
        user = obj.requester
        return getattr(user, "display_name", None) or getattr(user, "username", None) or str(user.id)

    def get_target_name(self, obj):
        user = obj.target_user
        if not user:
            return None
        return getattr(user, "display_name", None) or getattr(user, "username", None) or str(user.id)


class PartnerAccessReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartnerAccessReview
        fields = [
            "id",
            "partner",
            "name",
            "scope_type",
            "scope_id",
            "findings",
            "status",
            "created_by",
            "created_at",
            "closed_at",
        ]
        read_only_fields = ["id", "partner", "created_by", "created_at", "closed_at"]


class PartnerExportScheduleSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartnerExportSchedule
        fields = [
            "id",
            "partner",
            "kind",
            "export_format",
            "frequency",
            "is_active",
            "last_run_at",
            "next_run_at",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "partner",
            "created_by",
            "created_at",
            "updated_at",
            "last_run_at",
            "next_run_at",
        ]


class PartnerSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartnerSetting
        fields = [
            "id",
            "partner",
            "key",
            "config",
            "updated_by",
            "updated_at",
        ]
        read_only_fields = ["id", "partner", "updated_by", "updated_at"]


class PartnerOrganizationProfileSerializer(PartnerImageUrlSerializerMixin, serializers.ModelSerializer):
    verification_summary = serializers.SerializerMethodField()

    class Meta:
        model = PartnerOrganizationProfile
        fields = [
            "partner",
            "display_name",
            "legal_name",
            "tagline",
            "mission",
            "vision",
            "website",
            "email",
            "phone",
            "industry",
            "size",
            "founded_year",
            "headquarters",
            "logo_url",
            "brand_colors",
            "social_links",
            "public_fields",
            "verification_summary",
            "updated_by",
            "updated_at",
        ]
        read_only_fields = ["partner", "updated_by", "updated_at"]

    def get_verification_summary(self, obj):
        from apps.verification.services import current_partner_verification_status

        return current_partner_verification_status(obj.partner)


class PartnerOrganizationAppSerializer(serializers.ModelSerializer):
    partner_id = serializers.UUIDField(source="partner.id", read_only=True)
    tabs = serializers.SerializerMethodField()
    visible_to = serializers.ListField(
        child=serializers.ChoiceField(choices=[(role, role.capitalize()) for role in PARTNER_ORG_APP_VISIBILITY_ROLES]),
        required=False,
        default=default_app_visibility,
    )
    link = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = PartnerOrganizationApp
        fields = [
            "id",
            "partner_id",
            "name",
            "slug",
            "type",
            "description",
            "link",
            "module",
            "icon",
            "config",
            "metadata",
            "status",
            "is_promoted_global",
            "promoted_order",
            "published_at",
            "is_active",
            "visible_to",
            "tabs",
            "order",
            "group",
            "badge_label",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "partner_id", "is_promoted_global", "published_at", "tabs", "created_at", "updated_at"]

    def get_tabs(self, obj):
        tabs = getattr(obj, "prefetched_tabs", None)
        if tabs is None:
            tabs = obj.tabs.filter(is_active=True).order_by("order", "title")
        return PartnerOrganizationAppTabSerializer(tabs, many=True, context=self.context).data


class PartnerOrganizationAppContentBlockSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartnerOrganizationAppContentBlock
        fields = [
            "id",
            "tab",
            "block_type",
            "title",
            "body",
            "media_url",
            "payload",
            "order",
            "status",
            "is_active",
            "published_at",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_by", "published_at", "created_at", "updated_at"]


class PartnerOrganizationAppTabSerializer(serializers.ModelSerializer):
    content_blocks = serializers.SerializerMethodField()
    visible_to = serializers.ListField(
        child=serializers.ChoiceField(choices=[(role, role.capitalize()) for role in PARTNER_ORG_APP_VISIBILITY_ROLES]),
        required=False,
        default=default_app_visibility,
    )

    class Meta:
        model = PartnerOrganizationAppTab
        fields = [
            "id",
            "app",
            "title",
            "slug",
            "description",
            "icon",
            "order",
            "is_active",
            "visible_to",
            "config",
            "content_blocks",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "content_blocks", "created_at", "updated_at"]

    def get_content_blocks(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        can_manage = bool(
            user
            and getattr(user, "is_authenticated", False)
            and (obj.app.partner.owner_id == user.id or getattr(user, "is_staff", False))
        )
        blocks = obj.content_blocks.filter(is_active=True).order_by("order", "created_at")
        if not can_manage:
            blocks = blocks.filter(status=PartnerOrganizationAppStatus.PUBLISHED)
        return PartnerOrganizationAppContentBlockSerializer(blocks, many=True, context=self.context).data


class PartnerOrganizationAppAccessLogSerializer(serializers.ModelSerializer):
    user_display = serializers.CharField(source="user.display_name", read_only=True)

    class Meta:
        model = PartnerOrganizationAppAccessLog
        fields = [
            "id",
            "app",
            "user",
            "user_display",
            "action",
            "data_scope",
            "consent",
            "created_at",
        ]
        read_only_fields = ["id", "user_display", "created_at"]


class PartnerProfileLinkSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartnerProfileLink
        fields = [
            "id",
            "partner",
            "profile_key",
            "linked",
            "role",
            "analytics",
            "last_synced_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "partner", "created_at", "updated_at"]


class PartnerServerCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = PartnerServerCategory
        fields = [
            "id",
            "partner",
            "name",
            "slug",
            "order",
            "is_private",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "partner", "created_at", "updated_at"]


class PartnerChannelPermissionOverwriteSerializer(serializers.ModelSerializer):
    role_name = serializers.CharField(source="role.name", read_only=True)
    user_name = serializers.SerializerMethodField()

    class Meta:
        model = PartnerChannelPermissionOverwrite
        fields = [
            "id",
            "partner",
            "channel",
            "subject_type",
            "role",
            "role_name",
            "user",
            "user_name",
            "allow_permissions",
            "deny_permissions",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "partner", "channel", "created_at", "updated_at"]

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        subject_type = attrs.get("subject_type", getattr(instance, "subject_type", None))
        role = attrs.get("role", getattr(instance, "role", None))
        user = attrs.get("user", getattr(instance, "user", None))
        partner = self.context.get("partner") or getattr(instance, "partner", None)

        if subject_type == PartnerChannelPermissionOverwrite.SubjectType.ROLE:
            if not role or user:
                raise serializers.ValidationError("Role overwrites must target exactly one role.")
            if partner and role.partner_id != partner.id:
                raise serializers.ValidationError({"role": "Role does not belong to the same partner."})
        elif subject_type == PartnerChannelPermissionOverwrite.SubjectType.MEMBER:
            if not user or role:
                raise serializers.ValidationError("Member overwrites must target exactly one user.")
        else:
            raise serializers.ValidationError({"subject_type": "Invalid overwrite subject type."})

        for field_name in ("allow_permissions", "deny_permissions"):
            values = attrs.get(field_name, getattr(instance, field_name, []))
            invalid = [
                value for value in values
                if value not in PartnerChannelPermissionOverwrite.PermissionCode.values
            ]
            if invalid:
                raise serializers.ValidationError({field_name: f"Unsupported permission codes: {invalid}"})

        return attrs

    def get_user_name(self, obj):
        user = obj.user
        if not user:
            return None
        return getattr(user, "display_name", None) or getattr(user, "username", None) or str(user.id)
