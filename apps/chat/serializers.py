# chat/serializers.py
from apps.accounts.serializers import UserSerializer
from rest_framework import serializers
from common.media_urls import absolutize_backend_media, normalize_image_payload

from .models import (
    Conversation,
    ConversationMember,
    ConversationSettings,
    MessageThreadLink,
    BaseConversationRole,
    ConversationType,
)


class ConversationImageUrlSerializerMixin:
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


# ---------------------------------------------------------------------------
#  SETTINGS & MEMBERS
# ---------------------------------------------------------------------------

class ConversationSettingsSerializer(serializers.ModelSerializer):
    """Per-conversation settings (policies, retention, feature flags)."""

    class Meta:
        model = ConversationSettings
        fields = [
            "send_policy",
            "join_policy",
            "info_edit_policy",
            "subroom_policy",
            "max_subroom_depth",
            "message_retention_days",
            "allow_reactions",
            "allow_stickers",
            "allow_attachments",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]


class ConversationMemberSerializer(serializers.ModelSerializer):
    """
    Member view: exposes full user info + membership metadata.
    All user information is now nested under the `user` field.
    """

    user = UserSerializer(read_only=True)

    class Meta:
        model = ConversationMember
        fields = [
            "id",
            "user",  # nested user info
            "base_role",
            "display_name",
            "notification_level",
            "color",
            "is_muted",
            "is_pinned",
            "is_hidden",
            "is_blocked",
            "joined_at",
            "left_at",
            "is_active",
        ]
        read_only_fields = ["joined_at", "left_at", "is_active"]


# ---------------------------------------------------------------------------
#  CONVERSATION LIST & DETAIL
# ---------------------------------------------------------------------------

class ConversationListSerializer(ConversationImageUrlSerializerMixin, serializers.ModelSerializer):
    """
    Lightweight serializer for listing conversations in the chat list.

    - `participants` now includes full user info via ConversationMemberSerializer.
    """

    last_message_at = serializers.DateTimeField(allow_null=True)
    participants = ConversationMemberSerializer(
        source="memberships",
        many=True,
        read_only=True,
    )

    # DM request initiator / recipient as nested users
    request_initiator = UserSerializer(read_only=True)
    request_recipient = UserSerializer(read_only=True)
    group_id = serializers.SerializerMethodField()
    community_id = serializers.SerializerMethodField()
    is_community_group = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()
    has_mention = serializers.SerializerMethodField()
    last_read_seq = serializers.SerializerMethodField()
    read_state_authoritative = serializers.SerializerMethodField()
    is_muted = serializers.SerializerMethodField()
    is_pinned = serializers.SerializerMethodField()
    is_hidden = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            "id",
            "type",
            "title",
            "description",
            "avatar_url",
            "is_archived",
            "is_locked",
            "is_muted",
            "is_pinned",
            "is_hidden",
            "last_message_at",
            "last_message_preview",
            "created_at",
            "updated_at",
            # Request/lock state for DM flows
            "request_state",
            "request_initiator",
            "request_recipient",
            "participants",
            "group_id",
            "community_id",
            "is_community_group",
            "unread_count",
            "has_mention",
            "last_read_seq",
            "read_state_authoritative",
        ]

    def _current_member(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        user_id = getattr(user, "id", None)
        if not user_id:
            return None
        memberships = getattr(obj, "memberships", None)
        if memberships is None:
            return None
        for member in memberships.all():
            if member.user_id == user_id and member.left_at is None:
                return member
        return None

    def get_group_id(self, obj):
        group = getattr(obj, "group", None)
        return str(group.id) if group else None

    def get_community_id(self, obj):
        group = getattr(obj, "group", None)
        if group and group.community_id:
            return str(group.community_id)
        community_main = getattr(obj, "community_main", None)
        if community_main and community_main.id:
            return str(community_main.id)
        community_posts = getattr(obj, "community_posts", None)
        if community_posts and community_posts.id:
            return str(community_posts.id)
        return None

    def get_is_community_group(self, obj):
        group = getattr(obj, "group", None)
        return bool(group and group.community_id)

    def get_unread_count(self, obj):
        member = self._current_member(obj)
        if not member:
            return 0
        last_seq = max(int(getattr(obj, "last_message_seq", 0) or 0), 0)
        read_seq = max(int(getattr(member, "last_read_seq", 0) or 0), 0)
        return max(last_seq - read_seq, 0)

    def get_has_mention(self, obj):
        return False

    def get_last_read_seq(self, obj):
        member = self._current_member(obj)
        return int(getattr(member, "last_read_seq", 0) or 0) if member else 0

    def get_read_state_authoritative(self, obj):
        return True

    def get_is_muted(self, obj):
        member = self._current_member(obj)
        return bool(getattr(member, "is_muted", False)) if member else False

    def get_is_pinned(self, obj):
        member = self._current_member(obj)
        return bool(getattr(member, "is_pinned", False)) if member else False

    def get_is_hidden(self, obj):
        member = self._current_member(obj)
        return bool(getattr(member, "is_hidden", False)) if member else False


class ConversationDetailSerializer(ConversationImageUrlSerializerMixin, serializers.ModelSerializer):
    """
    Detailed view including settings and member list.

    `members` uses ConversationMemberSerializer, so each member
    also includes full nested user info.
    """

    settings = ConversationSettingsSerializer(read_only=True)
    members = ConversationMemberSerializer(
        source="memberships",
        many=True,
        read_only=True,
    )

    request_initiator = UserSerializer(read_only=True)
    request_recipient = UserSerializer(read_only=True)

    class Meta:
        model = Conversation
        fields = [
            "id",
            "type",
            "title",
            "description",
            "avatar_url",
            "created_by",
            "is_archived",
            "is_locked",
            "last_message_at",
            "last_message_preview",
            "created_at",
            "updated_at",
            "settings",
            "members",
            # DM request state
            "request_state",
            "request_initiator",
            "request_recipient",
            "request_accepted_at",
            "request_rejected_at",
        ]
        read_only_fields = [
            "created_by",
            "is_archived",
            "last_message_at",
            "last_message_preview",
            "created_at",
            "updated_at",
            # Request fields are backend-driven
            "request_state",
            "request_initiator",
            "request_recipient",
            "request_accepted_at",
            "request_rejected_at",
        ]


# ---------------------------------------------------------------------------
#  CONVERSATION CREATE (groups / channels / threads)
# ---------------------------------------------------------------------------

class ConversationCreateSerializer(ConversationImageUrlSerializerMixin, serializers.ModelSerializer):
    """
    Used for creating new group/channel/thread conversations from Django side.

    Direct conversations (1:1) use a separate serializer below.
    """

    # Accept flexible participant payload so DRF doesn’t reject the request
    participants = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        write_only=True,
        allow_empty=True,
    )
    user_id = serializers.DictField(required=False, write_only=True)

    class Meta:
        model = Conversation
        fields = [
            "id",
            "type",
            "title",
            "description",
            "avatar_url",
            "is_locked",
            # NOTE: participants & user_id are write-only extras, not model fields
            "participants",
            "user_id",
        ]

    def create(self, validated_data):
        from apps.accounts.models import User  # local to avoid circulars
        from .services import get_or_create_direct_conversation

        request = self.context["request"]
        user = request.user

        # pop write-only extras so they are not passed into Conversation()
        validated_data.pop("participants", None)
        validated_data.pop("user_id", None)

        # 1) Create the conversation itself
        conversation = Conversation.objects.create(
            created_by=user,
            **validated_data,
        )

        # 2) Ensure creator is a member (OWNER) by default
        ConversationMember.objects.create(
            conversation=conversation,
            user=user,
            base_role=BaseConversationRole.OWNER,
        )

        # 3) Extract participants (phone strings) from raw request.data
        raw_data = getattr(request, "data", {}) or {}

        # Preferred:
        #   raw_data["user_id"]["participant"] -> ["+2376...", "+2376..."]
        # Fallbacks:
        #   raw_data["user_id"]["participants"]
        #   raw_data["participants"]
        participants = []

        user_id_block = raw_data.get("user_id") or {}
        if isinstance(user_id_block, dict):
            participants = user_id_block.get("participant") or []
            if not participants:
                participants = user_id_block.get("participants") or []

        if not participants:
            participants = raw_data.get("participants") or []

        if not isinstance(participants, (list, tuple)):
            participants = []

        phone_numbers = [
            str(p).strip()
            for p in participants
            if p is not None and str(p).strip() != ""
        ]
        phone_numbers = list(dict.fromkeys(phone_numbers))  # unique

        conversation_type = validated_data.get("type") or ConversationType.DIRECT
        if conversation_type == ConversationType.DIRECT:
            if len(phone_numbers) != 1:
                raise serializers.ValidationError(
                    {"participants": "Direct conversations require exactly one peer participant."}
                )
            normalized_phone = User.objects.normalize_phone(phone_numbers[0])
            try:
                peer_user = User.objects.get(phone=normalized_phone)
            except User.DoesNotExist:
                raise serializers.ValidationError(
                    {"participants": f"User with phone number {normalized_phone} does not exist."}
                )
            if peer_user.id == user.id:
                raise serializers.ValidationError(
                    {"participants": "Cannot create a direct chat with yourself."}
                )
            conversation, _created = get_or_create_direct_conversation(
                user,
                peer_user,
                initiator=user,
                use_request_flow=True,
            )
            return conversation

        # 4) Look up users by phone and create memberships
        if phone_numbers:
            # IMPORTANT: your User model uses `phone`, not `phone_number`
            users = User.objects.filter(phone__in=phone_numbers).distinct()

            for participant_user in users:
                if participant_user == user:
                    continue

                ConversationMember.objects.get_or_create(
                    conversation=conversation,
                    user=participant_user,
                    defaults={"base_role": BaseConversationRole.MEMBER},
                )

        # 5) Create default settings row
        ConversationSettings.objects.create(conversation=conversation)

        return conversation


# ---------------------------------------------------------------------------
#  DIRECT CONVERSATION CREATE (1:1)
# ---------------------------------------------------------------------------

class DirectConversationCreateSerializer(serializers.Serializer):
    """
    Payload for creating/fetching a direct 1:1 conversation.
    """

    # Optional, for legacy / direct ID usage
    peer_user_id = serializers.CharField(required=False)

    # New fields to match mobile payload, but we keep them optional
    type = serializers.CharField(required=False, allow_blank=True)
    title = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    last_message_preview = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    participants = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )
    user_id = serializers.DictField(required=False)
    client_context = serializers.DictField(required=False)

    # ---- helpers -----------------------------------------------------------

    def _extract_phone_participants(self, raw_data):
        """
        Extract a list of phone numbers from the flexible request shape.
        Mirrors the logic in ConversationCreateSerializer.
        """
        participants = []

        user_id_block = raw_data.get("user_id") or {}
        if isinstance(user_id_block, dict):
            participants = user_id_block.get("participant") or []
            if not participants:
                participants = user_id_block.get("participants") or []

        if not participants:
            participants = raw_data.get("participants") or []

        if not isinstance(participants, (list, tuple)):
            participants = []

        phone_numbers = [
            str(p).strip()
            for p in participants
            if p is not None and str(p).strip() != ""
        ]

        phone_numbers = list(dict.fromkeys(phone_numbers))  # unique
        return phone_numbers

    # ---- main validation ---------------------------------------------------

    def validate(self, attrs):
        from apps.accounts.models import User

        request = self.context["request"]
        raw_data = getattr(request, "data", {}) or {}

        # 1) If peer_user_id is given directly, validate that path
        peer_id = raw_data.get("peer_user_id") or attrs.get("peer_user_id")
        if peer_id is not None:
            peer_id = str(peer_id).strip()

            if peer_id == str(request.user.id):
                raise serializers.ValidationError(
                    {"peer_user_id": "Cannot create a direct chat with yourself."}
                )

            try:
                User.objects.get(id=peer_id)
            except (User.DoesNotExist, ValueError, TypeError):
                raise serializers.ValidationError(
                    {"peer_user_id": "Peer user does not exist."}
                )

            attrs["peer_user_id"] = peer_id
            return attrs

        # 2) Otherwise, derive peer from phone numbers
        phone_numbers = self._extract_phone_participants(raw_data)
        if not phone_numbers:
            raise serializers.ValidationError(
                "Either 'peer_user_id' or at least one participant phone number is required."
            )

        # For a direct chat we only consider the first phone number
        first_phone = User.objects.normalize_phone(phone_numbers[0])

        try:
            # IMPORTANT: your User model uses `phone`, not `phone_number`
            peer_user = User.objects.get(phone=first_phone)
        except User.DoesNotExist:
            raise serializers.ValidationError(
                {"participants": f"User with phone number {first_phone} does not exist."}
            )

        if peer_user.id == request.user.id:
            raise serializers.ValidationError(
                {"participants": "Cannot create a direct chat with yourself."}
            )

        attrs["peer_user_id"] = peer_user.id
        return attrs

    def validate_peer_user_id(self, value):
        """
        Kept for compatibility in case DRF calls this when peer_user_id
        is present in the incoming payload.
        """
        from apps.accounts.models import User

        request = self.context["request"]

        value = str(value).strip()
        if value == str(request.user.id):
            raise serializers.ValidationError("Cannot create a direct chat with yourself.")

        try:
            User.objects.get(id=value)
        except (User.DoesNotExist, ValueError, TypeError):
            raise serializers.ValidationError("Peer user does not exist.")

        return value
    
    def create(self, validated_data):
        from apps.accounts.models import User
        from .services import get_or_create_direct_conversation

        request = self.context["request"]
        user: User = request.user
        peer_user_id = validated_data["peer_user_id"]
        peer_user = User.objects.get(id=peer_user_id)

        conversation, _created = get_or_create_direct_conversation(
            user,
            peer_user,
            initiator=user,
            use_request_flow=True,
        )
        return conversation


# ---------------------------------------------------------------------------
#  MESSAGE THREAD LINKS
# ---------------------------------------------------------------------------

class MessageThreadLinkSerializer(serializers.ModelSerializer):
    """Link between a parent message and a child conversation (sub-room)."""

    child_conversation = ConversationListSerializer(read_only=True)
    child_conversation_id = serializers.UUIDField(source="child_conversation.id", read_only=True)
    child_title = serializers.CharField(source="child_conversation.title", read_only=True)

    class Meta:
        model = MessageThreadLink
        fields = [
            "id",
            "parent_conversation",
            "parent_message_key",
            "child_conversation",
            "child_conversation_id",
            "child_title",
            "parent_thread",
            "depth",
            "created_by",
            "created_at",
        ]
        read_only_fields = [
            "child_conversation",
            "child_conversation_id",
            "child_title",
            "created_by",
            "created_at",
        ]
        validators = []
