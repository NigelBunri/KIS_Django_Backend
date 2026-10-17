# apps/groups/views.py
import secrets

from django.conf import settings
from django.db import models
from rest_framework import viewsets, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    OpenApiResponse,
)

from apps.groups.models import Group
from apps.groups.serializers import (
    GroupListSerializer,
    GroupDetailSerializer,
    GroupCreateSerializer,
    GroupMembershipSerializer,
    GroupJoinRequestSerializer,
    GroupBanSerializer,
)
from apps.groups.models import (
    GroupMembership,
    GroupJoinRequest,
    GroupJoinRequestStatus,
    GroupRole,
    GroupJoinPolicy,
    GroupBan,
)
from apps.accounts.models import User
from apps.partners.models import Partner as PartnerModel, PartnerMembership, PartnerMembershipStatus
from apps.communities.models import CommunityBan, CommunityMembership, CommunityRole
from apps.chat.models import ConversationMember, BaseConversationRole
from apps.groups.services import reactivate_group_membership


@extend_schema_view(
    list=extend_schema(
        summary="List groups",
        description=(
            "Return all groups where the authenticated user is either:\n"
            "- the group owner, or\n"
            "- an active member of the backing conversation."
        ),
        responses={200: GroupListSerializer},
    ),
    create=extend_schema(
        summary="Create a group",
        description=(
            "Create a new group and automatically:\n"
            "- create a backing Conversation of type `group`,\n"
            "- add the creator as OWNER in ConversationMember,\n"
            "- create ConversationSettings for that conversation."
        ),
        request=GroupCreateSerializer,
        responses={201: GroupDetailSerializer},
    ),
    retrieve=extend_schema(
        summary="Retrieve group details",
        description="Get full details for a single group, including conversation linkage.",
        responses={200: GroupDetailSerializer},
    ),
    update=extend_schema(
        summary="Update a group",
        description=(
            "Update a group. Only the group owner is allowed to update. "
            "Later, this can be extended to conversation admins via RBAC."
        ),
        request=GroupDetailSerializer,
        responses={200: GroupDetailSerializer},
    ),
    partial_update=extend_schema(
        summary="Partially update a group",
        description="Partially update group fields. Only the group owner is allowed.",
        request=GroupDetailSerializer,
        responses={200: GroupDetailSerializer},
    ),
    destroy=extend_schema(
        summary="Delete a group",
        description="Delete a group. Only the group owner is allowed to delete.",
        responses={204: OpenApiResponse(description="Group deleted")},
    ),
)
class GroupViewSet(viewsets.ModelViewSet):
    """
    Endpoints (assuming mounted at /api/v1/groups/):

      - GET    /api/v1/groups/groups/                 -> list
      - POST   /api/v1/groups/groups/                 -> create
      - GET    /api/v1/groups/groups/{id}/            -> retrieve
      - PUT    /api/v1/groups/groups/{id}/            -> update
      - PATCH  /api/v1/groups/groups/{id}/            -> partial_update
      - DELETE /api/v1/groups/groups/{id}/            -> destroy
      - POST   /api/v1/groups/groups/{id}/archive/    -> archive
    """
    permission_classes = [IsAuthenticated]
    queryset = Group.objects.select_related("conversation", "owner", "partner", "community", "channel")

    # Explicitly allow POST etc.
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

    def get_serializer_class(self):
        if self.action == "list":
            return GroupListSerializer
        if self.action == "create":
            return GroupCreateSerializer
        return GroupDetailSerializer

    def perform_create(self, serializer):
        """
        GroupCreateSerializer.create() auto-creates:
        - Conversation(type=GROUP)
        - ConversationMember for the owner (base_role=OWNER)
        - ConversationSettings for that conversation
        """
        from apps.accounts.tiers import get_user_tier_features, normalize_limit_value

        user = self.request.user
        partner = serializer.validated_data.get("partner")
        if partner:
            from apps.partners.services import partner_user_can_manage, user_has_partner_permission

            if not (
                partner_user_can_manage(partner, user)
                or user_has_partner_permission(partner, user, "partner.groups.manage")
            ):
                raise PermissionDenied("Not allowed to create groups for this organization.")
        features = get_user_tier_features(user)
        raw_limit = features.get("groups_per_community", features.get("groups"))
        limit = normalize_limit_value(raw_limit, default=None)
        if limit is not None:
            community = serializer.validated_data.get("community")
            count = Group.objects.filter(owner=user, community=community).count()
            if count >= limit:
                raise ValidationError({"detail": "Group limit reached for your plan."})
        serializer.save()

    def _get_membership(self, group: Group, user):
        return GroupMembership.objects.filter(
            group=group,
            user=user,
            left_at__isnull=True,
        ).first()

    def _is_admin(self, membership: GroupMembership | None) -> bool:
        return membership and membership.role in (
            GroupRole.OWNER,
            GroupRole.ADMIN,
            GroupRole.MOD,
        )

    def _reactivate_group_membership(self, group: Group, user, *, role=None):
        # Shared with ConversationViewSet.add_member (apps.chat.views) so a
        # member added via either the group's own UI or the chat/conversation
        # "Add member" UI always gets both a GroupMembership and a
        # ConversationMember row - see reactivate_group_membership's own
        # docblock for why that sync matters.
        return reactivate_group_membership(group, user, role=role)

    def _community_access_all(self, community_id, user) -> bool:
        if not community_id:
            return False
        cm = CommunityMembership.objects.filter(
            community_id=community_id,
            user=user,
            left_at__isnull=True,
            is_banned=False,
        ).first()
        if not cm:
            return False
        if cm.role in (CommunityRole.OWNER, CommunityRole.ADMIN, CommunityRole.MOD):
            return True
        return bool(cm.can_access_all_groups)

    def get_queryset(self):
        user = self.request.user
        qs = Group.objects.select_related("conversation", "owner", "partner", "community", "channel")
        community_id = self.request.query_params.get("community")
        partner_id = self.request.query_params.get("partner")

        if partner_id and not community_id:
            is_privileged = False
            try:
                partner_obj = PartnerModel.objects.only("owner_id").get(pk=partner_id)
                is_privileged = str(partner_obj.owner_id) == str(user.pk)
            except PartnerModel.DoesNotExist:
                pass
            if not is_privileged:
                is_privileged = PartnerMembership.objects.filter(
                    partner_id=partner_id,
                    user=user,
                    status=PartnerMembershipStatus.MEMBER,
                ).exists()
            if is_privileged:
                qs = qs.filter(partner_id=partner_id)
                channel_id = self.request.query_params.get("channel")
                if channel_id:
                    qs = qs.filter(channel_id=channel_id)
                return qs.distinct()

        if community_id:
            if self._community_access_all(community_id, user):
                qs = qs.filter(community_id=community_id)
            else:
                qs = qs.filter(
                    models.Q(community_id=community_id)
                    & (models.Q(owner=user) | models.Q(
                        memberships__user=user,
                        memberships__left_at__isnull=True,
                        memberships__is_banned=False,
                    ))
                )
        else:
            qs = qs.filter(
                models.Q(owner=user)
                | models.Q(
                    memberships__user=user,
                    memberships__left_at__isnull=True,
                    memberships__is_banned=False,
                )
            )
        if partner_id:
            qs = qs.filter(partner_id=partner_id)
        channel_id = self.request.query_params.get("channel")
        if channel_id:
            qs = qs.filter(channel_id=channel_id)
        return qs.distinct()

    def perform_update(self, serializer):
        group = self.get_object()
        if group.owner != self.request.user:
            # Better to raise a DRF PermissionDenied so Swagger shows 403 as error
            raise PermissionDenied("Only the group owner can update this group (for now).")
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        group = self.get_object()
        if group.owner != request.user:
            return Response(
                {"detail": "Only the group owner can delete this group (for now)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().destroy(request, *args, **kwargs)

    @extend_schema(
        summary="Archive a group",
        description=(
            "Archive the group and its backing conversation.\n\n"
            "- Sets `group.is_archived = True`.\n"
            "- Sets `conversation.is_archived = True`.\n\n"
            "Only the group owner is allowed to archive (for now)."
        ),
        responses={
            200: OpenApiResponse(description="Group archived"),
            403: OpenApiResponse(description="Forbidden – not the group owner"),
        },
    )
    @action(detail=True, methods=["post"], url_path="archive")
    def archive(self, request, pk=None):
        """
        Archive the group (and its conversation).
        """
        group = self.get_object()

        if group.owner != request.user:
            return Response(
                {"detail": "Only the group owner can archive this group (for now)."},
                status=status.HTTP_403_FORBIDDEN,
            )

        group.is_archived = True
        group.save()

        # Also archive backing conversation for consistency
        conversation = group.conversation
        conversation.is_archived = True
        conversation.save()

        return Response({"detail": "Group archived."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="members")
    def members(self, request, pk=None):
        group = self.get_object()
        qs = GroupMembership.objects.filter(group=group, left_at__isnull=True)
        serializer = GroupMembershipSerializer(qs, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="join")
    def join(self, request, pk=None):
        group = self.get_object()
        user = request.user

        if group.join_policy != GroupJoinPolicy.OPEN:
            return Response({"detail": "Group is not open to direct join."}, status=status.HTTP_400_BAD_REQUEST)

        membership, _cm = self._reactivate_group_membership(group, user)

        return Response(GroupMembershipSerializer(membership).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="add-members")
    def add_members(self, request, pk=None):
        """
        Add members to a group by user IDs.
        Payload:
          {
            "userIds": ["uuid", "uuid", ...]
          }
        """
        group = self.get_object()
        user = request.user
        membership = self._get_membership(group, user)
        if group.owner != user and not self._is_admin(membership):
            raise PermissionDenied("Only group admins can add members.")

        raw_ids = request.data.get("userIds") or request.data.get("user_ids") or []
        if not isinstance(raw_ids, list):
            return Response({"detail": "userIds must be a list."}, status=status.HTTP_400_BAD_REQUEST)

        user_ids = [str(uid) for uid in raw_ids if uid]
        if not user_ids:
            return Response({"detail": "No userIds provided."}, status=status.HTTP_400_BAD_REQUEST)

        users = User.objects.filter(id__in=user_ids, is_active=True)
        added: list[str] = []
        skipped_banned: list[str] = []

        banned_user_ids: set[str] = set()
        if group.community_id:
            banned_user_ids = set(
                str(uid) for uid in CommunityBan.objects.filter(
                    community_id=group.community_id, user_id__in=user_ids,
                ).values_list("user_id", flat=True)
            )

        for target in users:
            if target.id == user.id:
                continue
            if str(target.id) in banned_user_ids:
                # This group belongs to a community the target is banned
                # from - adding them here used to silently un-ban them
                # (update_or_create below would reactivate their
                # CommunityMembership with no ban check at all), reachable
                # by any admin of just this one sub-group, not a community
                # admin. A community ban must block ALL access to the
                # community, including every one of its sub-groups.
                skipped_banned.append(str(target.id))
                continue
            created = not GroupMembership.objects.filter(group=group, user=target).exists()
            self._reactivate_group_membership(group, target)
            if group.community_id:
                CommunityMembership.objects.update_or_create(
                    community_id=group.community_id,
                    user=target,
                    defaults={"role": CommunityRole.MEMBER, "left_at": None, "is_banned": False},
                )
                community = group.community
                if community and community.main_conversation_id:
                    mcm, _ = ConversationMember.objects.get_or_create(
                        conversation=community.main_conversation,
                        user=target,
                        defaults={"base_role": BaseConversationRole.MEMBER},
                    )
                    if mcm.left_at is not None:
                        mcm.left_at = None
                        mcm.save(update_fields=["left_at"])
                if community and community.posts_conversation_id:
                    pcm, _ = ConversationMember.objects.get_or_create(
                        conversation=community.posts_conversation,
                        user=target,
                        defaults={"base_role": BaseConversationRole.MEMBER},
                    )
                    if pcm.left_at is not None:
                        pcm.left_at = None
                        pcm.save(update_fields=["left_at"])
            if created:
                added.append(str(target.id))

        return Response(
            {"added": added, "count": len(added), "skipped_banned": skipped_banned},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post", "delete"], url_path="leave")
    def leave(self, request, pk=None):
        group = self.get_object()
        membership = self._get_membership(group, request.user)
        if not membership:
            return Response({"detail": "Not a member."}, status=status.HTTP_400_BAD_REQUEST)
        membership.left_at = timezone.now()
        membership.save(update_fields=["left_at"])
        ConversationMember.objects.filter(conversation=group.conversation, user=request.user).update(left_at=timezone.now())
        return Response({"detail": "Left group."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="request-join")
    def request_join(self, request, pk=None):
        group = self.get_object()
        user = request.user
        if group.join_policy != GroupJoinPolicy.REQUEST:
            return Response({"detail": "Group does not use join requests."}, status=status.HTTP_400_BAD_REQUEST)
        obj, _ = GroupJoinRequest.objects.get_or_create(
            group=group,
            user=user,
            defaults={"message": request.data.get("message", "")},
        )
        serializer = GroupJoinRequestSerializer(obj)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="approve-request")
    def approve_request(self, request, pk=None):
        group = self.get_object()
        membership = self._get_membership(group, request.user)
        if not self._is_admin(membership):
            raise PermissionDenied("Only admins can approve requests.")

        request_id = request.data.get("request_id")
        join_req = GroupJoinRequest.objects.filter(id=request_id, group=group).first()
        if not join_req:
            return Response({"detail": "Request not found."}, status=status.HTTP_404_NOT_FOUND)

        join_req.status = GroupJoinRequestStatus.APPROVED
        join_req.reviewed_by = request.user
        join_req.reviewed_at = timezone.now()
        join_req.save(update_fields=["status", "reviewed_by", "reviewed_at"])

        self._reactivate_group_membership(group, join_req.user)

        return Response({"detail": "Approved."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="reject-request")
    def reject_request(self, request, pk=None):
        group = self.get_object()
        membership = self._get_membership(group, request.user)
        if not self._is_admin(membership):
            raise PermissionDenied("Only admins can reject requests.")

        request_id = request.data.get("request_id")
        join_req = GroupJoinRequest.objects.filter(id=request_id, group=group).first()
        if not join_req:
            return Response({"detail": "Request not found."}, status=status.HTTP_404_NOT_FOUND)

        join_req.status = GroupJoinRequestStatus.REJECTED
        join_req.reviewed_by = request.user
        join_req.reviewed_at = timezone.now()
        join_req.save(update_fields=["status", "reviewed_by", "reviewed_at"])

        return Response({"detail": "Rejected."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="members/set-role")
    def set_member_role(self, request, pk=None):
        """
        Promote/demote a group member. Previously the only way to change
        a group member's role at all was the unrelated chat-conversation
        members/role endpoint (apps.chat.views.ConversationViewSet) acting
        on the group's backing conversation - not reachable from
        CommunityRoomPage's own Groups list, which has no member-
        management UI of its own. Mirrors
        CommunityViewSet._set_member_role_response's shape/guards.
        """
        group = self.get_object()
        membership = self._get_membership(group, request.user)
        if group.owner != request.user and not self._is_admin(membership):
            raise PermissionDenied("Only group admins can change member roles.")

        user_id = request.data.get("user_id")
        role_value = request.data.get("role")
        if not user_id or not role_value:
            return Response({"detail": "user_id and role are required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            target_role = GroupRole(role_value)
        except ValueError:
            return Response({"detail": "Invalid role value."}, status=status.HTTP_400_BAD_REQUEST)

        target_membership = GroupMembership.objects.filter(
            group=group, user_id=user_id, left_at__isnull=True,
        ).first()
        if not target_membership:
            return Response({"detail": "Member not found."}, status=status.HTTP_404_NOT_FOUND)
        if target_membership.role == GroupRole.OWNER:
            return Response({"detail": "Owner role cannot be modified."}, status=status.HTTP_400_BAD_REQUEST)
        if target_role == GroupRole.OWNER:
            return Response({"detail": "Cannot assign owner role."}, status=status.HTTP_400_BAD_REQUEST)

        target_membership.role = target_role
        target_membership.save(update_fields=["role"])

        # Keep the backing conversation's base_role in step, same mapping
        # ConversationViewSet.set_member_role uses for groups in reverse.
        conv_role = (
            BaseConversationRole.ADMIN
            if target_role in (GroupRole.ADMIN, GroupRole.MOD)
            else BaseConversationRole.MEMBER
        )
        ConversationMember.objects.filter(
            conversation=group.conversation, user_id=user_id, left_at__isnull=True,
        ).update(base_role=conv_role)

        return Response(GroupMembershipSerializer(target_membership).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="members/remove")
    def remove_member(self, request, pk=None):
        """
        Non-permanent removal - the member loses access immediately but
        can rejoin later through the group's normal join_policy, distinct
        from ban() below. Previously the only non-permanent way to lose
        group membership was leaving voluntarily; an admin's only option
        to remove someone else was the permanent ban() action.
        """
        group = self.get_object()
        membership = self._get_membership(group, request.user)
        if group.owner != request.user and not self._is_admin(membership):
            raise PermissionDenied("Only group admins can remove members.")

        user_id = request.data.get("user_id")
        if not user_id:
            return Response({"detail": "user_id required."}, status=status.HTTP_400_BAD_REQUEST)

        target_membership = GroupMembership.objects.filter(
            group=group, user_id=user_id, left_at__isnull=True,
        ).first()
        if not target_membership:
            return Response({"detail": "Member not found."}, status=status.HTTP_404_NOT_FOUND)
        if target_membership.role == GroupRole.OWNER:
            return Response({"detail": "The owner cannot be removed."}, status=status.HTTP_400_BAD_REQUEST)

        target_membership.left_at = timezone.now()
        target_membership.save(update_fields=["left_at"])
        ConversationMember.objects.filter(
            conversation=group.conversation, user_id=user_id, left_at__isnull=True,
        ).update(left_at=timezone.now())

        return Response({"detail": "Member removed."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="ban")
    def ban(self, request, pk=None):
        group = self.get_object()
        membership = self._get_membership(group, request.user)
        if not self._is_admin(membership):
            raise PermissionDenied("Only admins can ban.")

        user_id = request.data.get("user_id")
        if not user_id:
            return Response({"detail": "user_id required."}, status=status.HTTP_400_BAD_REQUEST)
        ban, _ = GroupBan.objects.update_or_create(
            group=group,
            user_id=user_id,
            defaults={
                "reason": request.data.get("reason", ""),
                "banned_by": request.user,
                "expires_at": request.data.get("expires_at"),
            },
        )
        # left_at is stamped here too (not just is_banned) - previously a
        # banned member's row kept left_at=None, leaving no recorded
        # boundary timestamp for when the ban actually took effect. Must
        # stay symmetric with unban() below, which restores it.
        now = timezone.now()
        GroupMembership.objects.filter(group=group, user_id=user_id).update(is_banned=True, left_at=now)
        ConversationMember.objects.filter(conversation=group.conversation, user_id=user_id).update(left_at=now)
        return Response(GroupBanSerializer(ban).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="unban")
    def unban(self, request, pk=None):
        group = self.get_object()
        membership = self._get_membership(group, request.user)
        if not self._is_admin(membership):
            raise PermissionDenied("Only admins can unban.")

        user_id = request.data.get("user_id")
        GroupBan.objects.filter(group=group, user_id=user_id).delete()
        # A reactivation, same semantics as a rejoin: restamp joined_at so
        # the pre-join-history boundary reflects this (new) membership
        # period, not whatever the member's original join date was before
        # ban() above set left_at.
        now = timezone.now()
        GroupMembership.objects.filter(group=group, user_id=user_id).update(
            is_banned=False, left_at=None, joined_at=now,
        )
        ConversationMember.objects.filter(conversation=group.conversation, user_id=user_id).update(
            left_at=None, joined_at=now,
        )
        return Response({"detail": "Unbanned."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get", "post"], url_path="invite-link")
    def invite_link(self, request, pk=None):
        """
        GET  — return the current invite link (generates one if absent).
              Any active member may call this - sharing the existing link
              is a normal everyday action (matches WhatsApp/Telegram: any
              member can share the group's invite link), not an admin
              privilege. Previously gated to admins only, which is what
              made "allow other members to generate/share the group
              invite link" unreachable for a plain member.
        POST — regenerate the invite token (invalidates the old link).
              Admin/owner only - regenerating revokes the link for
              everyone who already has it, which is a real consequence a
              plain member shouldn't be able to trigger unilaterally.
        """
        group = self.get_object()
        membership = self._get_membership(group, request.user)
        if not membership and group.owner_id != request.user.id:
            raise PermissionDenied("You are not a member of this group.")

        if request.method == "POST":
            if not self._is_admin(membership) and group.owner_id != request.user.id:
                raise PermissionDenied("Only admins can regenerate the invite link.")
            group.invite_token = secrets.token_urlsafe(24)
            group.save(update_fields=["invite_token"])
        elif not group.invite_token:
            # First-ever GET with no link yet: still needs a real token to
            # return, same generation logic the old admin-only POST branch
            # used - just reachable by any member now, not just admins.
            group.invite_token = secrets.token_urlsafe(24)
            group.save(update_fields=["invite_token"])

        # SITE_URL is the Django API host itself (api.kingdomimpactventures.org)
        # - a link built from it points at a domain with no matching web
        # route at all. KIS_WEBSITE_PUBLIC_BASE_URL is the real, deployed
        # website that actually serves /join/... (see PublicLinkResolveView).
        base = getattr(settings, "KIS_WEBSITE_PUBLIC_BASE_URL", "").rstrip("/")
        invite_link = f"{base}/join/group/{group.invite_token}"
        return Response({"invite_link": invite_link, "invite_token": group.invite_token})

    @action(detail=False, methods=["post"], url_path="join-by-invite")
    def join_by_invite(self, request):
        """
        POST { "invite_token": "..." } — join the group identified by the token.
        """
        token = request.data.get("invite_token", "").strip()
        if not token:
            return Response({"detail": "invite_token is required."}, status=status.HTTP_400_BAD_REQUEST)

        group = Group.objects.filter(invite_token=token, is_archived=False).first()
        if not group:
            return Response({"detail": "Invalid or expired invite link."}, status=status.HTTP_404_NOT_FOUND)

        existing = GroupMembership.objects.filter(group=group, user=request.user).first()
        if existing and existing.left_at is None and not existing.is_banned:
            return Response({"detail": "Already a member."}, status=status.HTTP_200_OK)
        if existing and existing.is_banned:
            return Response({"detail": "You are banned from this group."}, status=status.HTTP_403_FORBIDDEN)

        # Previously joined immediately regardless of join_policy - a group
        # with "Approval required to join" (GroupJoinPolicy.REQUEST) still
        # let anyone with the invite link join on the spot, the exact same
        # bypass request_join()/approve_request() exist to prevent. Route
        # through the real join-request flow instead when that policy is
        # set, same as request_join() above.
        if group.join_policy == GroupJoinPolicy.REQUEST:
            obj, _ = GroupJoinRequest.objects.get_or_create(
                group=group,
                user=request.user,
                defaults={"message": "Joined via invite link"},
            )
            return Response(
                {"detail": "Join request sent for approval.", "group_id": str(group.id), "pending": True},
                status=status.HTTP_202_ACCEPTED,
            )

        self._reactivate_group_membership(group, request.user)
        return Response({"detail": "Joined successfully.", "group_id": str(group.id), "pending": False})
