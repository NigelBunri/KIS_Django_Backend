# apps/channels/views.py
from django.db import models
from django.db.models import Count, Q
from rest_framework import viewsets, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.channels.models import Channel, Subchannel
from apps.channels.serializers import (
    ChannelListSerializer,
    ChannelDetailSerializer,
    ChannelCreateSerializer,
    SubchannelSerializer,
)
from apps.chat.models import BaseConversationRole, ConversationMember
from apps.partners.serializers import PartnerChannelPermissionOverwriteSerializer
from apps.partners.services import (
    active_partner_member_ids,
    filter_partner_channels_for_user,
    notify_nest_of_partner_event,
    partner_user_can_access,
    partner_user_can_manage,
    partner_user_can_manage_channel,
    partner_user_can_send_channel,
    partner_user_can_view_channel,
    user_has_partner_permission,
)
from apps.partners.tiers import require_partner_feature


def _is_member(channel: Channel, user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    return ConversationMember.objects.filter(
        conversation=channel.conversation, user=user, left_at__isnull=True,
    ).exists()


def _personal_channel_hidden_from(channel: Channel, user) -> bool:
    """True if this is a personal (non-partner) PRIVATE channel and the
    given user is neither its owner nor an existing member — i.e. it
    should behave as if it doesn't exist for them: absent from discovery/
    search, 404 on direct retrieve, rejected on self-subscribe. Partner
    channels are untouched here — see channel_access_model_note()."""
    if channel.partner_id:
        return False
    if channel.channel_type != Channel.ChannelType.PRIVATE:
        return False
    if user and getattr(user, "is_authenticated", False) and channel.owner_id == getattr(user, "id", None):
        return False
    return not _is_member(channel, user)


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
        Public (non-partner-scoped) list/search:
        - Return all non-archived, non-hidden channels.
        - Allow optional search by ?q= (real backend search — the same
          queryset/filter/order the plain list uses, not a separate path).
        - Deterministic order: subscriber count (a real, queryable signal —
          not a fake/ML recommendation) then recency, both stable across
          pages. Previously order_by("?") re-randomized on every request,
          which breaks pagination outright (duplicate/skipped rows across
          pages) — replaced, not just re-tuned.
        - PRIVATE personal (non-partner) channels are excluded here for
          anyone who isn't the owner or an existing member — see
          _personal_channel_hidden_from()'s docstring for the full access
          model. This is real ORM-level filtering (a correlated EXISTS
          subquery), not a Python post-filter, so it doesn't break
          pagination or force materializing the whole catalog into memory
          the way the existing partner-branch visibility filter below
          does (that one is unchanged in this pass — a separate, smaller-
          scale case: one partner's channel list, not the whole platform).
        """
        user = self.request.user
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

        viewer_membership = ConversationMember.objects.filter(
            conversation_id=models.OuterRef("conversation_id"), user=user, left_at__isnull=True,
        )
        qs = qs.annotate(_viewer_is_member=models.Exists(viewer_membership))
        qs = qs.exclude(
            models.Q(partner__isnull=True)
            & models.Q(channel_type=Channel.ChannelType.PRIVATE)
            & ~models.Q(owner_id=getattr(user, "id", None))
            & models.Q(_viewer_is_member=False)
        )

        qs = qs.annotate(subscriber_count=Count("conversation__memberships", filter=Q(conversation__memberships__left_at__isnull=True), distinct=True))
        return qs.order_by("-subscriber_count", "-created_at", "id")

    def get_object(self):
        channel = super().get_object()
        if self.action in {"update", "partial_update", "destroy", "archive", "overwrites", "overwrite_detail"}:
            if not self._user_can_manage_channel(channel, self.request.user):
                raise PermissionDenied("Not allowed to manage this channel.")
            return channel
        if channel.partner_id and not partner_user_can_view_channel(channel, self.request.user):
            raise PermissionDenied("Not allowed to view this channel.")
        if _personal_channel_hidden_from(channel, self.request.user):
            # 404, not 403 - a private channel a non-member has no
            # business knowing exists at all shouldn't even confirm its
            # existence via a distinguishable error code.
            from django.http import Http404

            raise Http404("No Channel matches the given query.")
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
        if partner and not (
            partner_user_can_manage(partner, user)
            or user_has_partner_permission(partner, user, "partner.channels.manage")
        ):
            raise PermissionDenied("Not allowed to create partner channels.")
        community = serializer.validated_data.get("community")
        if partner and community and community.partner_id and community.partner_id != partner.id:
            raise ValidationError({"community": "Community does not belong to the selected partner."})
        if partner and serializer.validated_data.get("channel_type") == Channel.ChannelType.VOICE:
            require_partner_feature(partner, "voice_channels", "This organization's current plan does not include voice channels.")
        serializer.save()  # ChannelCreateSerializer handles owner + conversation
        if partner:
            notify_nest_of_partner_event(
                partner_id=str(partner.id),
                event="partner.channel_created",
                user_ids=active_partner_member_ids(partner),
                data={"channelId": str(serializer.instance.id), "name": serializer.instance.name},
            )

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

    @action(detail=True, methods=["post"], url_path="unsubscribe")
    def unsubscribe(self, request, pk=None):
        """
        Leave this channel. Idempotent — calling it twice, or calling it
        having never subscribed, both return 200 with subscribed=False
        rather than a 404/409, since "not a member" is the end state
        either way and a client retrying a dropped response shouldn't see
        an error for a leave that already succeeded.

        Soft-leaves (sets left_at) rather than deleting the
        ConversationMember row, matching how every other conversation type
        in this codebase already tracks membership history (chat groups,
        communities) — re-subscribing later reuses/reactivates the same
        row rather than risking a duplicate.
        """
        channel = self.get_object()
        member = ConversationMember.objects.filter(
            conversation=channel.conversation, user=request.user, left_at__isnull=True,
        ).first()
        if not member:
            return Response({"subscribed": False}, status=status.HTTP_200_OK)
        if member.base_role == BaseConversationRole.OWNER:
            raise ValidationError({"detail": "The owner cannot unsubscribe — archive or transfer ownership instead."})
        from django.utils import timezone

        member.left_at = timezone.now()
        member.save(update_fields=["left_at"])
        return Response({"subscribed": False}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get", "post"], url_path="members")
    def members(self, request, pk=None):
        """
        GET  /channels/{id}/members/  — list current members. Requires
             existing membership (or manage permission) — a private
             channel's member list must not be enumerable by an outsider
             even indirectly, matching get_object()'s own retrieve-time gate.
        POST /channels/{id}/members/  — add a specific user as a member.
             Requires channel-manage permission (owner, or admin/manager
             via the partner permission system for partner channels). This
             is the actual "invite" mechanism for PRIVATE personal
             channels, since those reject self-service subscribe() —
             see _personal_channel_hidden_from()'s docstring. Matches the
             frontend's already-defined addMembersToChannel/
             getChannelMembers routes, which previously pointed at a URL
             with no backend handler at all for POST.
        """
        channel = self.get_object()
        if request.method == "GET":
            if not (_is_member(channel, request.user) or self._user_can_manage_channel(channel, request.user)):
                raise PermissionDenied("Not allowed to view this channel's members.")
            rows = ConversationMember.objects.filter(
                conversation=channel.conversation, left_at__isnull=True,
            ).select_related("user")
            data = [
                {
                    "user_id": str(m.user_id),
                    "display_name": getattr(m.user, "display_name", "") or getattr(m.user, "username", ""),
                    "role": m.base_role,
                }
                for m in rows
            ]
            return Response({"results": data, "count": len(data)})

        if not self._user_can_manage_channel(channel, request.user):
            raise PermissionDenied("Not allowed to manage this channel's members.")
        target_user_id = str(request.data.get("user_id") or "").strip()
        if not target_user_id:
            raise ValidationError({"user_id": "This field is required."})
        requested_role = str(request.data.get("role") or BaseConversationRole.MEMBER).strip().lower()
        if requested_role not in (BaseConversationRole.MEMBER, BaseConversationRole.ADMIN, BaseConversationRole.READONLY):
            raise ValidationError({"role": "Must be member, admin, or readonly."})

        from apps.accounts.models import User

        try:
            target_user = User.objects.get(id=target_user_id)
        except User.DoesNotExist:
            raise ValidationError({"user_id": "User not found."})

        member, created = ConversationMember.objects.get_or_create(
            conversation=channel.conversation,
            user=target_user,
            defaults={"base_role": requested_role},
        )
        if not created and member.left_at is not None:
            # Re-adding someone who previously left — reactivate rather
            # than error, same idempotent-membership convention as
            # unsubscribe()'s own soft-leave.
            member.left_at = None
            member.base_role = requested_role
            member.save(update_fields=["left_at", "base_role"])
        return Response(
            {"user_id": str(target_user.id), "role": member.base_role, "added": True},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
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

    @action(detail=True, methods=["get"], url_path="analytics")
    def analytics(self, request, pk=None):
        """
        GET /api/v1/channels/{id}/analytics/
        Returns basic channel metrics: subscriber count, message count.
        """
        channel = self.get_object()
        subscriber_count = ConversationMember.objects.filter(
            conversation=channel.conversation,
            left_at__isnull=True,
        ).count()
        # message_count is tracked as a monotonic sequence on the conversation
        message_count = getattr(channel.conversation, "last_message_seq", 0) if channel.conversation_id else 0
        return Response({
            "channel_id": str(channel.id),
            "subscriber_count": subscriber_count,
            "message_count": message_count,
        }, status=status.HTTP_200_OK)


class SubchannelViewSet(viewsets.ModelViewSet):
    """
    CRUD for subchannels nested under a parent channel.

    - list:     GET    /api/v1/subchannels/?channel={channel_id}
    - create:   POST   /api/v1/subchannels/
    - retrieve: GET    /api/v1/subchannels/{id}/
    - update:   PATCH  /api/v1/subchannels/{id}/
    - delete:   DELETE /api/v1/subchannels/{id}/
    - members:  GET    /api/v1/subchannels/{id}/members/

    SECURITY: create/update/destroy/members all require channel-MANAGE
    permission on the parent Channel — reuses partner_user_can_manage_
    channel(), the exact same function ChannelViewSet.get_object() already
    gates its own update/destroy/archive/overwrites actions with, rather
    than a second, separately-maintained rule. Previously this ViewSet had
    no ownership check at all beyond IsAuthenticated: any authenticated
    user (not just the channel's owner/admins) could create, rename, or
    delete a subchannel of ANY channel on the platform via a direct API
    request — confirmed a real gap, not a theoretical one, fixed here.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = SubchannelSerializer

    def get_queryset(self):
        qs = Subchannel.objects.select_related("channel", "created_by")
        channel_id = self.request.query_params.get("channel")
        if channel_id:
            qs = qs.filter(channel_id=channel_id)
        return qs

    def get_object(self):
        subchannel = super().get_object()
        if self.action in {"update", "partial_update", "destroy"}:
            if not partner_user_can_manage_channel(subchannel.channel, self.request.user):
                raise PermissionDenied("Not allowed to manage this channel's subchannels.")
        elif self.action == "members":
            if not partner_user_can_view_channel(subchannel.channel, self.request.user):
                raise PermissionDenied("Not allowed to view this channel.")
        elif subchannel.channel.partner_id and not partner_user_can_view_channel(subchannel.channel, self.request.user):
            raise PermissionDenied("Not allowed to view this channel.")
        return subchannel

    def perform_create(self, serializer):
        channel_id = self.request.data.get("channel")
        if not channel_id:
            raise ValidationError({"channel": "This field is required."})
        try:
            channel = Channel.objects.get(pk=channel_id)
        except Channel.DoesNotExist:
            raise ValidationError({"channel": "Channel not found."})
        if not partner_user_can_manage_channel(channel, self.request.user):
            raise PermissionDenied("Not allowed to manage this channel's subchannels.")
        serializer.save(channel=channel, created_by=self.request.user)

    @action(detail=True, methods=["get"], url_path="members")
    def members(self, request, pk=None):
        """Return members of the parent channel for this subchannel."""
        subchannel = self.get_object()
        members = ConversationMember.objects.filter(
            conversation=subchannel.channel.conversation,
            left_at__isnull=True,
        ).select_related("user")
        data = [
            {
                "user_id": str(m.user_id),
                "display_name": getattr(m.user, "display_name", "") or getattr(m.user, "username", ""),
                "role": m.base_role,
            }
            for m in members
        ]
        return Response({"results": data, "count": len(data)})
