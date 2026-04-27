# apps/channels/serializers.py
from rest_framework import serializers

from apps.channels.models import Channel
from apps.partners.services import partner_user_can_send_channel
from apps.chat.models import (
    BaseConversationRole,
    ConversationMember,
    ConversationSettings,
    ConversationSendPolicy,
    ConversationType,
)
from common.media_urls import absolutize_backend_media, normalize_image_payload


class ChannelImageUrlSerializerMixin:
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


def _member_for(channel: Channel, user):
    if not user or not user.is_authenticated:
        return None
    return ConversationMember.objects.filter(
        conversation=channel.conversation,
        user=user,
        left_at__isnull=True,
    ).first()


def _can_send(channel: Channel, member: ConversationMember | None, user=None) -> bool:
    if channel.partner_id:
        return partner_user_can_send_channel(channel, user)
    if not member or member.base_role == BaseConversationRole.READONLY:
        return False
    settings = ConversationSettings.objects.filter(conversation=channel.conversation).first()
    if settings and settings.send_policy == ConversationSendPolicy.ADMINS_ONLY:
        return member.base_role in (BaseConversationRole.OWNER, BaseConversationRole.ADMIN)
    return True


class ChannelListSerializer(ChannelImageUrlSerializerMixin, serializers.ModelSerializer):
    conversation_id = serializers.UUIDField(source="conversation.id", read_only=True)
    is_subscribed = serializers.SerializerMethodField()
    member_role = serializers.SerializerMethodField()
    can_post = serializers.SerializerMethodField()
    category_id = serializers.UUIDField(source="category.id", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = Channel
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "channel_type",
            "order",
            "avatar_url",
            "invite_messages",
            "is_archived",
            "partner",
            "community",
            "category",
            "category_id",
            "category_name",
            "is_subscribed",
            "member_role",
            "can_post",
            "conversation_id",
            "created_at",
            "updated_at",
        ]

    def get_is_subscribed(self, obj):
        member = _member_for(obj, self.context["request"].user)
        return bool(member)

    def get_member_role(self, obj):
        member = _member_for(obj, self.context["request"].user)
        return member.base_role if member else None

    def get_can_post(self, obj):
        member = _member_for(obj, self.context["request"].user)
        return _can_send(obj, member, self.context["request"].user)


class ChannelDetailSerializer(ChannelImageUrlSerializerMixin, serializers.ModelSerializer):
    conversation_id = serializers.UUIDField(source="conversation.id", read_only=True)
    is_subscribed = serializers.SerializerMethodField()
    member_role = serializers.SerializerMethodField()
    can_post = serializers.SerializerMethodField()
    category_id = serializers.UUIDField(source="category.id", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = Channel
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "channel_type",
            "order",
            "avatar_url",
            "invite_messages",
            "partner",
            "community",
            "category",
            "category_id",
            "category_name",
            "owner",
            "is_archived",
            "is_subscribed",
            "member_role",
            "can_post",
            "conversation_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "owner",
            "conversation_id",
            "created_at",
            "updated_at",
        ]

    def get_is_subscribed(self, obj):
        member = _member_for(obj, self.context["request"].user)
        return bool(member)

    def get_member_role(self, obj):
        member = _member_for(obj, self.context["request"].user)
        return member.base_role if member else None

    def get_can_post(self, obj):
        member = _member_for(obj, self.context["request"].user)
        return _can_send(obj, member, self.context["request"].user)


class ChannelCreateSerializer(ChannelImageUrlSerializerMixin, serializers.ModelSerializer):
    """
    For creating a channel; automatically creates the backing Conversation
    and membership for the owner.

    For channels, we usually want send_policy=ADMINS_ONLY by default
    (announcement / broadcast style), but you can tweak it.
    """

    class Meta:
        model = Channel
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "channel_type",
            "order",
            "avatar_url",
            "invite_messages",
            "partner",
            "community",
            "category",
        ]

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        partner = attrs.get("partner", getattr(instance, "partner", None))
        community = attrs.get("community", getattr(instance, "community", None))
        category = attrs.get("category", getattr(instance, "category", None))

        if category:
            if not partner:
                raise serializers.ValidationError({"category": "A categorized channel must belong to a partner."})
            if category.partner_id != partner.id:
                raise serializers.ValidationError({"category": "Category does not belong to the selected partner."})

        if partner and community and community.partner_id and community.partner_id != partner.id:
            raise serializers.ValidationError({"community": "Community does not belong to the selected partner."})

        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        user = request.user

        from apps.chat.models import (
            Conversation,
            ConversationSettings,
            BaseConversationRole,
            ConversationMember,
            ConversationSendPolicy,
        )

        # 1. Create Conversation of type CHANNEL
        conversation = Conversation.objects.create(
            type=ConversationType.CHANNEL,
            title=validated_data.get("name", ""),
            description=validated_data.get("description", ""),
            avatar_url=validated_data.get("avatar_url", ""),
            created_by=user,
        )

        # 2. Add owner as conversation member with OWNER role
        ConversationMember.objects.create(
            conversation=conversation,
            user=user,
            base_role=BaseConversationRole.OWNER,
        )

        # 3. Create default settings for the conversation, but for channels
        #    we can default send_policy to ADMINS_ONLY (broadcast style).
        ConversationSettings.objects.create(
            conversation=conversation,
            send_policy=ConversationSendPolicy.ADMINS_ONLY,
        )

        # 4. Create the Channel linked to this conversation
        channel = Channel.objects.create(
            owner=user,
            conversation=conversation,
            **validated_data,
        )
        return channel
