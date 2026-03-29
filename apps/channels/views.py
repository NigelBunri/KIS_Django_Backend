# apps/channels/views.py
from django.db import models
from rest_framework import viewsets, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError

from apps.channels.models import Channel
from apps.channels.serializers import (
    ChannelListSerializer,
    ChannelDetailSerializer,
    ChannelCreateSerializer,
)
from apps.chat.models import BaseConversationRole, ConversationMember


class ChannelViewSet(viewsets.ModelViewSet):
    """
    /api/v1/channels/channels/

    - list:       GET    /api/v1/channels/channels/
    - create:     POST   /api/v1/channels/channels/
    - retrieve:   GET    /api/v1/channels/channels/{id}/
    - update:     PUT/PATCH /api/v1/channels/channels/{id}/
    - archive:    POST   /api/v1/channels/channels/{id}/archive/
    """
    permission_classes = [IsAuthenticated]
    queryset = Channel.objects.select_related("conversation", "owner", "partner", "community")

    def get_serializer_class(self):
        if self.action == "list":
            return ChannelListSerializer
        if self.action == "create":
            return ChannelCreateSerializer
        return ChannelDetailSerializer

    def get_queryset(self):
        """
        Public list:
        - Return all non-archived channels.
        - Allow optional search by ?q=
        - Randomize order to avoid ranking bias.
        """
        qs = Channel.objects.select_related("conversation", "owner", "partner", "community").filter(
            is_archived=False,
        )

        partner_id = (self.request.query_params.get("partner") or "").strip()
        if partner_id:
            qs = qs.filter(partner_id=partner_id)

        q = (self.request.query_params.get("q") or "").strip()
        if q:
            qs = qs.filter(
                models.Q(name__icontains=q)
                | models.Q(description__icontains=q)
                | models.Q(slug__icontains=q)
            )
        owner_id = (self.request.query_params.get("owner") or "").strip()
        if owner_id:
            qs = qs.filter(owner_id=owner_id)

        return qs.order_by("?")

    def perform_create(self, serializer):
        from apps.accounts.tiers import get_user_tier_features, normalize_limit_value

        user = self.request.user
        features = get_user_tier_features(user)
        limit = normalize_limit_value(features.get("channels_create"), default=None)
        if limit is not None:
            count = Channel.objects.filter(owner=user).count()
            if count >= limit:
                raise ValidationError({"detail": "Channel limit reached for your plan."})
        serializer.save()  # ChannelCreateSerializer handles owner + conversation

    @action(detail=True, methods=["post"], url_path="archive")
    def archive(self, request, pk=None):
        """
        Archive the channel (and its conversation).

        For now: only the channel owner can archive.
        Later, plug in RBAC (partner/community-level admins, etc.).
        """
        channel = self.get_object()

        if channel.owner != request.user:
            return Response(
                {"detail": "Only the channel owner can archive this channel (for now)."},
                status=status.HTTP_403_FORBIDDEN,
            )

        channel.is_archived = True
        channel.save()

        conv = channel.conversation
        conv.is_archived = True
        conv.save()

        return Response({"detail": "Channel archived."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="subscribe")
    def subscribe(self, request, pk=None):
        """
        Subscribe the current user to this channel (read access).
        """
        from apps.accounts.tiers import get_user_tier_features

        features = get_user_tier_features(request.user)
        if features.get("channels_follow") is False:
            return Response(
                {"detail": "Your current tier does not allow channel follows."},
                status=status.HTTP_403_FORBIDDEN,
            )

        channel = self.get_object()
        member = ConversationMember.objects.filter(
            conversation=channel.conversation,
            user=request.user,
            left_at__isnull=True,
        ).first()

        if member:
            return Response(
                {
                    "subscribed": True,
                    "role": member.base_role,
                },
                status=status.HTTP_200_OK,
            )

        member = ConversationMember.objects.create(
            conversation=channel.conversation,
            user=request.user,
            base_role=BaseConversationRole.MEMBER,
        )

        return Response(
            {
                "subscribed": True,
                "role": member.base_role,
            },
            status=status.HTTP_201_CREATED,
        )
