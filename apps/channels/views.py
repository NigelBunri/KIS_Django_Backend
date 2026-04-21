# apps/channels/views.py
from django.db import models
from rest_framework import viewsets, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.channels.models import Channel
from apps.channels.serializers import (
    ChannelListSerializer,
    ChannelDetailSerializer,
    ChannelCreateSerializer,
)
from apps.chat.models import BaseConversationRole, ConversationMember
from apps.partners.serializers import PartnerChannelPermissionOverwriteSerializer
from apps.partners.services import (
    filter_partner_channels_for_user,
    partner_user_can_access,
    partner_user_can_manage,
    partner_user_can_manage_channel,
    partner_user_can_send_channel,
    partner_user_can_view_channel,
)


class ChannelViewSet(viewsets.ModelViewSet):
    """
    /api/v1/partner-channels/channels/

    - list:       GET    /api/v1/partner-channels/channels/
    - create:     POST   /api/v1/partner-channels/channels/
    - retrieve:   GET    /api/v1/partner-channels/channels/{id}/
    - update:     PUT/PATCH /api/v1/partner-channels/channels/{id}/
    - archive:    POST   /api/v1/partner-channels/channels/{id}/archive/
    """
    permission_classes = [IsAuthenticated]
    queryset = Channel.objects.select_related(
        "conversation",
        "owner",
        "partner",
        "community",
        "category",
    ).prefetch_related("permission_overwrites__role", "permission_overwrites__user")

    def get_serializer_class(self):
        if self.action == "list":
            return ChannelListSerializer
        if self.action in {"create", "update", "partial_update"}:
            return ChannelCreateSerializer
        return ChannelDetailSerializer

    def get_queryset(self):
        """
        Public list:
        - Return all non-archived channels.
        - Allow optional search by ?q=
        - Randomize order to avoid ranking bias.
        """
        qs = Channel.objects.select_related(
            "conversation",
            "owner",
            "partner",
            "community",
            "category",
        ).prefetch_related("permission_overwrites__role", "permission_overwrites__user").filter(is_archived=False)

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

        if partner_id:
            ordered_channels = list(qs.order_by("category__order", "category__name", "order", "name"))
            visible_ids = [channel.id for channel in filter_partner_channels_for_user(ordered_channels, self.request.user)]
            return qs.filter(id__in=visible_ids).order_by("category__order", "category__name", "order", "name")

        return qs.order_by("?")

    def get_object(self):
        channel = super().get_object()
        if self.action in {"update", "partial_update", "destroy", "archive", "overwrites", "overwrite_detail"}:
            if not self._user_can_manage_channel(channel, self.request.user):
                raise PermissionDenied("Not allowed to manage this channel.")
            return channel
        if channel.partner_id and not partner_user_can_view_channel(channel, self.request.user):
            raise PermissionDenied("Not allowed to view this channel.")
        return channel

    def _user_can_manage_channel(self, channel: Channel, user) -> bool:
        return partner_user_can_manage_channel(channel, user)

    def perform_create(self, serializer):
        from apps.accounts.tiers import get_user_tier_features, normalize_limit_value

        user = self.request.user
        features = get_user_tier_features(user)
        limit = normalize_limit_value(features.get("channels_create"), default=None)
        if limit is not None:
            count = Channel.objects.filter(owner=user).count()
            if count >= limit:
                raise ValidationError({"detail": "Channel limit reached for your plan."})
        partner = serializer.validated_data.get("partner")
        if partner and not partner_user_can_manage(partner, user):
            raise PermissionDenied("Not allowed to create partner channels.")
        community = serializer.validated_data.get("community")
        if partner and community and community.partner_id and community.partner_id != partner.id:
            raise ValidationError({"community": "Community does not belong to the selected partner."})
        serializer.save()  # ChannelCreateSerializer handles owner + conversation

    def perform_update(self, serializer):
        channel = self.get_object()
        if not self._user_can_manage_channel(channel, self.request.user):
            raise PermissionDenied("Not allowed to update this channel.")
        serializer.save()

    @action(detail=True, methods=["post"], url_path="archive")
    def archive(self, request, pk=None):
        """
        Archive the channel (and its conversation).
        """
        channel = self.get_object()

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
        if channel.partner_id and not partner_user_can_view_channel(channel, request.user):
            raise PermissionDenied("Not allowed to view this channel.")
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
            base_role=(
                BaseConversationRole.MEMBER
                if partner_user_can_send_channel(channel, request.user)
                else BaseConversationRole.READONLY
            ),
        )

        return Response(
            {
                "subscribed": True,
                "role": member.base_role,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get", "post"], url_path="overwrites")
    def overwrites(self, request, pk=None):
        channel = self.get_object()
        if request.method == "POST":
            serializer = PartnerChannelPermissionOverwriteSerializer(
                data=request.data,
                context={"partner": channel.partner, "request": request},
            )
            serializer.is_valid(raise_exception=True)
            serializer.save(partner=channel.partner, channel=channel)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        serializer = PartnerChannelPermissionOverwriteSerializer(
            channel.permission_overwrites.select_related("role", "user").order_by("subject_type", "id"),
            many=True,
        )
        return Response({"overwrites": serializer.data}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["patch", "delete"], url_path=r"overwrites/(?P<overwrite_id>[^/.]+)")
    def overwrite_detail(self, request, pk=None, overwrite_id=None):
        channel = self.get_object()
        overwrite = channel.permission_overwrites.filter(id=overwrite_id).select_related("role", "user").first()
        if not overwrite:
            return Response({"detail": "Overwrite not found."}, status=status.HTTP_404_NOT_FOUND)

        if request.method == "DELETE":
            overwrite.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        serializer = PartnerChannelPermissionOverwriteSerializer(
            overwrite,
            data=request.data,
            partial=True,
            context={"partner": channel.partner, "request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)
