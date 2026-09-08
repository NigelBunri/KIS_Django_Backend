# apps/communities/views.py
import secrets
from django.conf import settings
from django.db import models, transaction
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.communities.models import Community
from apps.communities.serializers import (
    CommunityListSerializer,
    CommunityDetailSerializer,
    CommunityCreateSerializer,
    CommunityMembershipSerializer,
    CommunityJoinRequestSerializer,
    CommunityBanSerializer,
    CommunityPostSerializer,
    CommunityPostCreateSerializer,
    CommunityPostCommentSerializer,
)
from apps.communities.models import (
    CommunityMembership,
    CommunityMembershipStatus,
    CommunityJoinRequest,
    CommunityJoinRequestStatus,
    CommunityRole,
    CommunityBan,
    CommunityPost,
    CommunityPostComment,
    CommunityPostReaction,
    CommunityCommentReaction,
    CommunityPostStatus,
    CommunityJoinPolicy,
    CommunityPostPolicy,
    CommunityVisibility,
)
from apps.accounts.models import User
from apps.partners.models import Partner as PartnerModel, PartnerMembership, PartnerMembershipStatus
from apps.chat.models import (
    BaseConversationRole,
    Conversation,
    ConversationMember,
    ConversationSettings,
    ConversationType,
    ConversationSendPolicy,
    ConversationJoinPolicy as ChatConversationJoinPolicy,
)
from apps.chat.discussion import ensure_conversation_member, ensure_post_comment_conversation
from apps.feed_personalization import (
    get_affinity_profile,
    log_feed_interaction,
    rank_feed_items,
    resolve_personalization_sample_limit,
)
from apps.moderation.models import UserBlock


class CommunityViewSet(viewsets.ModelViewSet):
    """
    /api/v1/communities/communities/

    - list:       GET    /api/v1/communities/communities/
    - create:     POST   /api/v1/communities/communities/
    - retrieve:   GET    /api/v1/communities/communities/{id}/
    - update:     PUT/PATCH /api/v1/communities/communities/{id}/
    - deactivate: POST   /api/v1/communities/communities/{id}/deactivate/
    """
    permission_classes = [IsAuthenticated]
    queryset = Community.objects.select_related("partner", "owner", "main_conversation")

    def get_serializer_class(self):
        if self.action == "list":
            return CommunityListSerializer
        if self.action == "create":
            return CommunityCreateSerializer
        return CommunityDetailSerializer

    def get_queryset(self):
        """
        - Return public communities when ?public=true is passed (for discovery).
        - Otherwise return communities where the user is the owner, an active
          member, or which are publicly visible (so a genuine first-time
          user can retrieve/join/request-join a public community they're
          not a member of yet - get_object() for every detail action,
          including join/request-join/members/invite-link, goes through
          this same queryset; without the public OR-clause here, those
          actions 404'd before ever reaching their own join_policy/
          permission checks, so a real outsider could never successfully
          join any community via the standard join route. Caught by the
          Phase 5 regression tests, not previously covered by anything).
          PRIVATE/HIDDEN communities remain invisible to non-members here,
          as intended - they're only reachable via join_by_invite (which
          looks up by invite_token, not by this queryset).
        - Supports ?search=, ?ordering=-member_count.
        """
        user = self.request.user
        qs = Community.objects.select_related("partner", "owner", "main_conversation")
        partner_id = self.request.query_params.get("partner")

        # Public discovery mode
        is_public_filter = self.request.query_params.get("public", "").lower() in ("true", "1")
        if is_public_filter:
            qs = qs.filter(
                is_active=True,
                visibility=CommunityVisibility.PUBLIC,
            )
            search = self.request.query_params.get("search", "").strip()
            if search:
                qs = qs.filter(
                    models.Q(name__icontains=search) | models.Q(description__icontains=search)
                )
            ordering = self.request.query_params.get("ordering", "")
            if ordering == "-member_count":
                qs = qs.annotate(
                    member_count=models.Count(
                        "memberships",
                        filter=models.Q(memberships__status=CommunityMembershipStatus.ACTIVE),
                    )
                ).order_by("-member_count")
            if partner_id:
                qs = qs.filter(partner_id=partner_id)
            return qs.distinct()

        if partner_id:
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
                return qs.filter(partner_id=partner_id).distinct()

        qs = qs.filter(
            models.Q(owner=user)
            | models.Q(
                memberships__user=user,
                memberships__status=CommunityMembershipStatus.ACTIVE,
            )
            | models.Q(is_active=True, visibility=CommunityVisibility.PUBLIC)
        )
        if partner_id:
            qs = qs.filter(partner_id=partner_id)
        return qs.distinct()

    def perform_create(self, serializer):
        from apps.accounts.tiers import get_user_tier_features, normalize_limit_value
        import logging as _logging
        _log = _logging.getLogger(__name__)

        user = self.request.user
        features = get_user_tier_features(user)
        limit = normalize_limit_value(features.get("communities"), default=None)
        if limit is not None:
            count = Community.objects.filter(owner=user).count()
            if count >= limit:
                raise ValidationError({"detail": "Community limit reached for your plan."})
        _log.info("community.create request_data=%s", self.request.data)
        try:
            serializer.save()
        except Exception as exc:
            _log.error("community.create failed exc=%r validated=%r", exc, getattr(serializer, '_validated_data', None))
            raise

    def update(self, request, *args, **kwargs):
        # The default PUT/PATCH path had no permission check at all (only
        # IsAuthenticated at the class level) - any authenticated user
        # could rewrite any community's name/description/visibility/
        # join_policy/is_active/etc via this route regardless of role,
        # completely bypassing the correctly-checked update_settings
        # action. avatar_url updates (CommunityInfoPage.tsx) are the one
        # confirmed legitimate caller of this endpoint - gating it to
        # owner/admin here is what makes that call safe.
        community = self.get_object()
        if not self._has_owner_privileges(community, request.user):
            raise PermissionDenied("Only community owners/admins can update this community.")
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        community = self.get_object()
        if not self._has_owner_privileges(community, request.user):
            raise PermissionDenied("Only community owners/admins can update this community.")
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        # No frontend caller uses DELETE /communities/{id}/ - the intended
        # path is the deactivate action (soft-delete, owner-only, already
        # correctly checked). The default hard-delete route had zero
        # permission check; block it outright rather than reviewing and
        # exposing a second, more destructive deletion mechanism no one
        # actually needs.
        raise PermissionDenied("Communities cannot be deleted directly. Use the deactivate action.")

    def _get_membership(self, community: Community, user):
        return CommunityMembership.objects.active().filter(
            community=community,
            user=user,
        ).first()

    def _has_owner_privileges(self, community: Community, user):
        if community.owner_id == user.id:
            return True
        membership = self._get_membership(community, user)
        if membership and membership.role == CommunityRole.ADMIN:
            return True
        return self._is_partner_admin(community.partner, user)

    def _coerce_boolean(self, value):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in ("1", "true", "t", "yes"):
                return True
            if normalized in ("0", "false", "f", "no"):
                return False
        if isinstance(value, (int, float)):
            return bool(value)
        return None

    def _is_partner_admin(self, partner, user):
        if not partner or not partner.main_conversation_id:
            return False
        from apps.chat.models import ConversationMember

        return ConversationMember.objects.filter(
            conversation_id=partner.main_conversation_id,
            user=user,
            base_role__in=(BaseConversationRole.OWNER, BaseConversationRole.ADMIN),
            left_at__isnull=True,
        ).exists()

    def _ensure_conversation_membership(self, community: Community, user):
        from apps.chat.models import ConversationMember, BaseConversationRole

        for conversation in (community.main_conversation, community.posts_conversation):
            if not conversation:
                continue
            member, created = ConversationMember.objects.get_or_create(
                conversation=conversation,
                user=user,
                defaults={"base_role": BaseConversationRole.MEMBER},
            )
            # get_or_create alone would leave a previously-removed
            # ConversationMember row stale (left_at still set) when a user
            # rejoins - they'd have a CommunityMembership but no working
            # chat access, since nothing re-activates the conversation
            # side. Symmetric to _remove_conversation_membership below.
            if not created and member.left_at is not None:
                member.left_at = None
                member.save(update_fields=["left_at"])

    def _remove_conversation_membership(self, community: Community, user_id):
        from apps.chat.models import ConversationMember

        conversation_ids = [
            cid for cid in (community.main_conversation_id, community.posts_conversation_id) if cid
        ]
        if conversation_ids:
            ConversationMember.objects.filter(
                conversation_id__in=conversation_ids,
                user_id=user_id,
                left_at__isnull=True,
            ).update(left_at=timezone.now())

    def _update_membership_role(self, membership: CommunityMembership, role: str):
        membership.role = role
        membership.save(update_fields=["role"])
        return membership

    def _check_not_banned(self, community: Community, user):
        if CommunityBan.objects.filter(community=community, user=user).exists():
            raise PermissionDenied("You are banned from this community.")
        membership = CommunityMembership.objects.filter(community=community, user=user).first()
        if membership and membership.status == CommunityMembershipStatus.BANNED:
            raise PermissionDenied("You are banned from this community.")

    @transaction.atomic
    def _activate_membership(self, community: Community, user, *, role=CommunityRole.MEMBER):
        """
        Single entry point for every way a user becomes an active member:
        direct join, invite-link join, admin add-members, and approved
        join requests all go through this. A ban check here is the ONE
        place that decision is made, so every path enforces it identically
        instead of each one re-implementing (and inevitably diverging on,
        as join()/approve_request() previously did) the same rule -
        join_by_invite() was the only path that got it right before this.
        """
        self._check_not_banned(community, user)
        membership = CommunityMembership.objects.filter(community=community, user=user).first()
        if membership is None:
            membership = CommunityMembership.objects.create(
                community=community,
                user=user,
                role=role,
                status=CommunityMembershipStatus.ACTIVE,
            )
        elif membership.status != CommunityMembershipStatus.ACTIVE:
            membership.reactivate(role=role)
        self._ensure_conversation_membership(community, user)
        return membership

    @transaction.atomic
    def _ban_user(self, community: Community, user_id, *, reason="", banned_by=None, expires_at=None):
        """
        Single canonical ban path. `ban` and `members/block` (below) both
        call this now - previously they were two independent
        implementations with different side effects (only one of them set
        left_at), which is exactly why a ban()-banned user's membership
        row could still look "active" everywhere that checked left_at
        instead of status.
        """
        ban, _ = CommunityBan.objects.update_or_create(
            community=community,
            user_id=user_id,
            defaults={"reason": reason, "banned_by": banned_by, "expires_at": expires_at},
        )
        membership = CommunityMembership.objects.filter(community=community, user_id=user_id).first()
        if membership:
            membership.mark_banned()
        self._remove_conversation_membership(community, user_id)
        return ban

    @action(detail=True, methods=["post"], url_path="deactivate")
    def deactivate(self, request, pk=None):
        """
        Soft-deactivate a community.

        For now: only the community owner can deactivate.
        Later you can plug in RBAC (partner-level admin, global admin, etc.).
        """
        community = self.get_object()

        if community.owner != request.user:
            return Response(
                {"detail": "Only the community owner can deactivate this community (for now)."},
                status=status.HTTP_403_FORBIDDEN,
            )

        community.is_active = False
        community.save()

        return Response({"detail": "Community deactivated."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="members")
    def members(self, request, pk=None):
        community = self.get_object()
        qs = CommunityMembership.objects.active().filter(community=community)
        serializer = CommunityMembershipSerializer(qs, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="join")
    def join(self, request, pk=None):
        community = self.get_object()
        user = request.user

        if community.join_policy != CommunityJoinPolicy.OPEN:
            return Response(
                {"detail": "Community is not open to direct join."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # _activate_membership raises PermissionDenied (-> 403) if the user
        # is banned - join() no longer has its own copy of that check to
        # forget.
        membership = self._activate_membership(community, user, role=CommunityRole.MEMBER)
        from apps.communities.realtime import notify_member_joined

        notify_member_joined(community, membership)
        return Response(CommunityMembershipSerializer(membership).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="add-members")
    def add_members(self, request, pk=None):
        """
        Add members to a community by user IDs.
        Payload:
          {
            "userIds": ["uuid", "uuid", ...]
          }
        """
        community = self.get_object()
        if not self._has_owner_privileges(community, request.user):
            raise PermissionDenied("Only community admins can add members.")

        raw_ids = request.data.get("userIds") or request.data.get("user_ids") or []
        if not isinstance(raw_ids, list):
            return Response({"detail": "userIds must be a list."}, status=status.HTTP_400_BAD_REQUEST)

        user_ids = [str(uid) for uid in raw_ids if uid]
        if not user_ids:
            return Response({"detail": "No userIds provided."}, status=status.HTTP_400_BAD_REQUEST)

        users = User.objects.filter(id__in=user_ids, is_active=True)
        added: list[str] = []
        skipped_banned: list[str] = []

        for target in users:
            if target.id == request.user.id:
                continue
            try:
                self._activate_membership(community, target, role=CommunityRole.MEMBER)
                added.append(str(target.id))
            except PermissionDenied:
                skipped_banned.append(str(target.id))

        return Response(
            {"added": added, "count": len(added), "skipped_banned": skipped_banned},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post", "delete"], url_path="leave")
    def leave(self, request, pk=None):
        community = self.get_object()
        membership = self._get_membership(community, request.user)
        if not membership:
            return Response({"detail": "Not a member."}, status=status.HTTP_400_BAD_REQUEST)
        membership.mark_left()
        from apps.communities.realtime import notify_member_left

        notify_member_left(community, membership, reason="left")
        return Response({"detail": "Left community."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="request-join")
    def request_join(self, request, pk=None):
        community = self.get_object()
        user = request.user
        if community.join_policy != CommunityJoinPolicy.REQUEST:
            return Response(
                {"detail": "Community does not use join requests."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        self._check_not_banned(community, user)
        existing_membership = self._get_membership(community, user)
        if existing_membership:
            return Response({"detail": "You are already a member."}, status=status.HTTP_400_BAD_REQUEST)

        obj, created = CommunityJoinRequest.objects.get_or_create(
            community=community,
            user=user,
            defaults={"message": request.data.get("message", "")},
        )
        if not created and obj.status != CommunityJoinRequestStatus.PENDING:
            # Re-requesting after a rejection (or any other terminal state)
            # must actually become a new pending request - get_or_create
            # alone just returns the same, permanently-REJECTED row, which
            # silently no-ops every future attempt and never becomes
            # visible to admins again. Reopen the same row (still one row
            # per community+user, per the unique_together constraint)
            # rather than leaving it stuck.
            obj.status = CommunityJoinRequestStatus.PENDING
            obj.message = request.data.get("message", obj.message)
            obj.reviewed_by = None
            obj.reviewed_at = None
            obj.save(update_fields=["status", "message", "reviewed_by", "reviewed_at"])
            from apps.communities.realtime import notify_join_request_created
            from apps.communities.notifications import (
                notify_join_request_created as notify_join_request_created_persistent,
            )

            notify_join_request_created(community, obj)
            notify_join_request_created_persistent(community, obj)
        elif created:
            from apps.communities.realtime import notify_join_request_created
            from apps.communities.notifications import (
                notify_join_request_created as notify_join_request_created_persistent,
            )

            notify_join_request_created(community, obj)
            notify_join_request_created_persistent(community, obj)
        serializer = CommunityJoinRequestSerializer(obj)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="approve-request")
    @transaction.atomic
    def approve_request(self, request, pk=None):
        community = self.get_object()
        if not self._has_owner_privileges(community, request.user):
            raise PermissionDenied("Only admins can approve requests.")

        request_id = request.data.get("request_id")
        join_req = CommunityJoinRequest.objects.filter(id=request_id, community=community).first()
        if not join_req:
            return Response({"detail": "Request not found."}, status=status.HTTP_404_NOT_FOUND)
        if join_req.status != CommunityJoinRequestStatus.PENDING:
            return Response(
                {"detail": f"Request is already {join_req.status}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            self._activate_membership(community, join_req.user, role=CommunityRole.MEMBER)
        except PermissionDenied as exc:
            # The user was banned after requesting (or is banned under a
            # different flow entirely) - leave the request PENDING rather
            # than silently marking it approved with no real membership
            # behind it. An admin can unban first, then approve.
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

        join_req.status = CommunityJoinRequestStatus.APPROVED
        join_req.reviewed_by = request.user
        join_req.reviewed_at = timezone.now()
        join_req.save(update_fields=["status", "reviewed_by", "reviewed_at"])

        from apps.communities.realtime import notify_join_request_decided
        from apps.communities.notifications import (
            notify_join_request_decided as notify_join_request_decided_persistent,
        )

        notify_join_request_decided(community, join_req, approved=True)
        notify_join_request_decided_persistent(community, join_req, approved=True)
        return Response({"detail": "Approved."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="reject-request")
    def reject_request(self, request, pk=None):
        community = self.get_object()
        if not self._has_owner_privileges(community, request.user):
            raise PermissionDenied("Only admins can reject requests.")

        request_id = request.data.get("request_id")
        join_req = CommunityJoinRequest.objects.filter(id=request_id, community=community).first()
        if not join_req:
            return Response({"detail": "Request not found."}, status=status.HTTP_404_NOT_FOUND)
        if join_req.status != CommunityJoinRequestStatus.PENDING:
            return Response(
                {"detail": f"Request is already {join_req.status}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        join_req.status = CommunityJoinRequestStatus.REJECTED
        join_req.reviewed_by = request.user
        join_req.reviewed_at = timezone.now()
        join_req.save(update_fields=["status", "reviewed_by", "reviewed_at"])

        from apps.communities.realtime import notify_join_request_decided
        from apps.communities.notifications import (
            notify_join_request_decided as notify_join_request_decided_persistent,
        )

        notify_join_request_decided(community, join_req, approved=False)
        notify_join_request_decided_persistent(community, join_req, approved=False)
        return Response({"detail": "Rejected."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="members/set-admin")
    def set_admin(self, request, pk=None):
        # Kept for backward compatibility with any existing caller, but
        # set_member_role below is the canonical, more general endpoint -
        # this now just delegates to it instead of duplicating the same
        # role-change logic a second time.
        community = self.get_object()
        make_admin = bool(request.data.get("make_admin", True))
        role_value = CommunityRole.ADMIN if make_admin else CommunityRole.MEMBER
        return self._set_member_role_response(community, request, role_value)

    @action(detail=True, methods=["post"], url_path="members/set-role")
    def set_member_role(self, request, pk=None):
        community = self.get_object()
        role_value = request.data.get("role")
        if not role_value:
            return Response({"detail": "role is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            target_role = CommunityRole(role_value)
        except ValueError:
            return Response({"detail": "Invalid role value."}, status=status.HTTP_400_BAD_REQUEST)
        return self._set_member_role_response(community, request, target_role)

    def _set_member_role_response(self, community: Community, request, target_role: str):
        if not self._has_owner_privileges(community, request.user):
            raise PermissionDenied("Only owners/admins can change member roles.")

        user_id = request.data.get("user_id")
        if not user_id:
            return Response({"detail": "user_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        membership = CommunityMembership.objects.active().filter(
            community=community,
            user_id=user_id,
        ).first()
        if not membership:
            return Response({"detail": "Member not found."}, status=status.HTTP_404_NOT_FOUND)
        if membership.role == CommunityRole.OWNER:
            return Response({"detail": "Owner role cannot be modified."}, status=status.HTTP_400_BAD_REQUEST)
        if target_role == CommunityRole.OWNER:
            return Response({"detail": "Cannot assign owner role."}, status=status.HTTP_400_BAD_REQUEST)

        previous_role = membership.role
        membership = self._update_membership_role(membership, target_role)

        if previous_role != membership.role:
            from apps.communities.realtime import notify_role_changed
            from apps.communities.notifications import notify_role_changed as notify_role_changed_persistent

            notify_role_changed(community, membership, previous_role=previous_role, changed_by=request.user)
            notify_role_changed_persistent(community, membership, previous_role=previous_role)

        serializer = CommunityMembershipSerializer(membership)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="members/block")
    def block_member(self, request, pk=None):
        # Historically a second, divergent ban implementation (it set
        # left_at on the membership, the /ban/ action below didn't) - both
        # routes now delegate to the one real ban path so they always
        # produce identical, correct state.
        community = self.get_object()
        if not self._has_owner_privileges(community, request.user):
            raise PermissionDenied("Only admins can block members.")
        user_id = request.data.get("user_id")
        if not user_id:
            return Response({"detail": "user_id required."}, status=status.HTTP_400_BAD_REQUEST)
        ban = self._ban_user(
            community, user_id, reason="Blocked by community admin", banned_by=request.user
        )
        from apps.communities.realtime import notify_member_banned
        from apps.communities.notifications import notify_member_banned as notify_member_banned_persistent

        notify_member_banned(community, user_id, banned_by=request.user)
        notify_member_banned_persistent(community, user_id)
        return Response(CommunityBanSerializer(ban).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="members/remove")
    @transaction.atomic
    def remove_member(self, request, pk=None):
        """
        Non-permanent removal - the member loses access immediately, but
        is NOT banned: they can rejoin later through the community's
        normal join_policy, exactly like anyone who left voluntarily.
        Distinct from ban() below, which is permanent until explicitly
        unbanned. This is what the frontend's "Remove from community"
        action now actually calls (see CommunityInfoPage.tsx) - it
        previously called ban() by mistake.
        """
        community = self.get_object()
        if not self._has_owner_privileges(community, request.user):
            raise PermissionDenied("Only admins can remove members.")
        user_id = request.data.get("user_id")
        if not user_id:
            return Response({"detail": "user_id required."}, status=status.HTTP_400_BAD_REQUEST)
        membership = CommunityMembership.objects.active().filter(
            community=community,
            user_id=user_id,
        ).first()
        if not membership:
            return Response({"detail": "Member not found."}, status=status.HTTP_404_NOT_FOUND)
        if membership.role == CommunityRole.OWNER:
            return Response({"detail": "The owner cannot be removed."}, status=status.HTTP_400_BAD_REQUEST)
        membership.mark_removed()
        self._remove_conversation_membership(community, user_id)

        from apps.communities.realtime import notify_member_left
        from apps.communities.notifications import notify_member_removed

        notify_member_left(community, membership, reason="removed")
        notify_member_removed(community, user_id)
        return Response({"detail": "Member removed."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["patch"], url_path="settings")
    def update_settings(self, request, pk=None):
        community = self.get_object()
        if not self._has_owner_privileges(community, request.user):
            raise PermissionDenied("Only admins can update settings.")

        allowed_fields = {
            "post_policy": CommunityPostPolicy,
            "join_policy": CommunityJoinPolicy,
            "require_post_approval": None,
            "allow_links": None,
            "allow_comments": None,
            "allow_reactions": None,
            "allow_media": None,
            "allow_polls": None,
            "allow_events": None,
            "allow_post_link_copy": None,
            "allow_join_link": None,
            "require_join_survey": None,
            "allow_broadcasts": None,
            "visibility": CommunityVisibility,
        }
        updates = {}
        for key, enum_cls in allowed_fields.items():
            if key not in request.data:
                continue
            value = request.data.get(key)
            if enum_cls:
                if isinstance(value, str) and value in enum_cls.values:
                    updates[key] = value
                else:
                    return Response(
                        {"detail": f"Invalid value for {key}."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            else:
                parsed_bool = self._coerce_boolean(value)
                if parsed_bool is None:
                    return Response(
                        {"detail": f"Invalid value for {key}."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                updates[key] = parsed_bool

        if updates:
            for key, value in updates.items():
                setattr(community, key, value)
            community.save(update_fields=list(updates.keys()))

            from apps.communities.realtime import notify_settings_changed
            from apps.communities.notifications import notify_settings_changed as notify_settings_changed_persistent

            notify_settings_changed(community, changed_by=request.user, changed_fields=list(updates.keys()))
            notify_settings_changed_persistent(community, changed_by=request.user, changed_fields=list(updates.keys()))

        serializer = CommunityDetailSerializer(community)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["patch"], url_path="settings/broadcast")
    def update_broadcast_settings(self, request, pk=None):
        community = self.get_object()
        if not self._has_owner_privileges(community, request.user):
            raise PermissionDenied("Only admins can change broadcast settings.")

        if "allow_broadcasts" not in request.data:
            return Response(
                {"detail": "allow_broadcasts is required."}, status=status.HTTP_400_BAD_REQUEST
            )

        parsed = self._coerce_boolean(request.data.get("allow_broadcasts"))
        if parsed is None:
            return Response(
                {"detail": "Invalid value for allow_broadcasts."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        community.allow_broadcasts = parsed
        community.save(update_fields=["allow_broadcasts"])

        serializer = CommunityDetailSerializer(community)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="ban")
    def ban(self, request, pk=None):
        community = self.get_object()
        if not self._has_owner_privileges(community, request.user):
            raise PermissionDenied("Only admins can ban.")

        user_id = request.data.get("user_id")
        if not user_id:
            return Response({"detail": "user_id required."}, status=status.HTTP_400_BAD_REQUEST)
        target_membership = CommunityMembership.objects.filter(community=community, user_id=user_id).first()
        if target_membership and target_membership.role == CommunityRole.OWNER:
            return Response({"detail": "The owner cannot be banned."}, status=status.HTTP_400_BAD_REQUEST)
        ban = self._ban_user(
            community,
            user_id,
            reason=request.data.get("reason", ""),
            banned_by=request.user,
            expires_at=request.data.get("expires_at"),
        )
        from apps.communities.realtime import notify_member_banned
        from apps.communities.notifications import notify_member_banned as notify_member_banned_persistent

        notify_member_banned(community, user_id, banned_by=request.user)
        notify_member_banned_persistent(community, user_id)
        return Response(CommunityBanSerializer(ban).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="unban")
    @transaction.atomic
    def unban(self, request, pk=None):
        community = self.get_object()
        if not self._has_owner_privileges(community, request.user):
            raise PermissionDenied("Only admins can unban.")

        user_id = request.data.get("user_id")
        if not user_id:
            return Response({"detail": "user_id required."}, status=status.HTTP_400_BAD_REQUEST)
        CommunityBan.objects.filter(community=community, user_id=user_id).delete()
        membership = CommunityMembership.objects.filter(
            community=community, user_id=user_id, status=CommunityMembershipStatus.BANNED
        ).first()
        if membership:
            # Unbanning lifts the prohibition; it does not silently restore
            # membership. The user still needs to (re)join through the
            # community's normal join_policy, same as anyone else who
            # isn't currently a member - a REQUEST or INVITE_ONLY community
            # shouldn't readmit someone with no review just because they
            # were once unbanned.
            membership.status = CommunityMembershipStatus.LEFT
            membership.is_banned = False
            membership.save(update_fields=["status", "is_banned"])
        return Response({"detail": "Unbanned."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get", "post"], url_path="invite-link")
    def invite_link(self, request, pk=None):
        """GET returns current link; POST regenerates it. Admins only."""
        community = self.get_object()
        membership = self._get_membership(community, request.user)
        role = membership.role if membership else None
        if role not in ("owner", "admin", "mod"):
            raise PermissionDenied("Only admins can manage the invite link.")
        if not community.allow_join_link:
            return Response({"detail": "Invite links are disabled for this community."}, status=400)
        if request.method == "POST" or not community.invite_token:
            community.invite_token = secrets.token_urlsafe(24)
            community.save(update_fields=["invite_token"])
        base = getattr(settings, "SITE_URL", "").rstrip("/")
        link = f"{base}/join/community/{community.invite_token}"
        return Response({"invite_link": link, "invite_token": community.invite_token})

    @action(detail=False, methods=["post"], url_path="join-by-invite")
    def join_by_invite(self, request):
        """POST { invite_token } — join by invite token."""
        token = request.data.get("invite_token", "").strip()
        if not token:
            return Response({"detail": "invite_token is required."}, status=400)
        community = Community.objects.filter(invite_token=token, is_active=True).first()
        if not community:
            return Response({"detail": "Invalid or expired invite link."}, status=404)
        if not community.allow_join_link:
            return Response({"detail": "Invite links are disabled for this community."}, status=403)
        existing = self._get_membership(community, request.user)
        if existing:
            return Response({"detail": "Already a member."})
        # _activate_membership raises PermissionDenied (-> 403) for a
        # banned user - same single ban check every other join path now
        # uses, instead of this route's own (previously correct, but now
        # redundant) inline version of the same check.
        membership = self._activate_membership(community, request.user, role=CommunityRole.MEMBER)
        from apps.communities.realtime import notify_member_joined

        notify_member_joined(community, membership)
        return Response({"detail": "Joined successfully.", "community_id": str(community.id)})


class CommunityPostViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    queryset = CommunityPost.objects.select_related("community", "author")
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_serializer_class(self):
        if self.action == "create":
            return CommunityPostCreateSerializer
        return CommunityPostSerializer

    def get_queryset(self):
        user = self.request.user
        community_id = self.request.query_params.get("community")
        blocked_ids = UserBlock.objects.filter(blocker=user).values_list("blocked_id", flat=True)
        # Previously this only filtered by community_id when the caller
        # happened to pass one - GET /posts/ with no query param (or a
        # retrieve/update/destroy on any post id) returned/operated on
        # posts from EVERY community, including PRIVATE and HIDDEN ones
        # the requesting user isn't a member of. Mirrors the same
        # public-or-member visibility rule CommunityViewSet.get_queryset()
        # already applies to listing communities themselves.
        visible_communities = Community.objects.filter(
            models.Q(visibility=CommunityVisibility.PUBLIC)
            | models.Q(owner=user)
            | models.Q(memberships__user=user, memberships__status=CommunityMembershipStatus.ACTIVE)
        ).distinct()
        qs = CommunityPost.objects.select_related("community", "author").filter(
            community__in=visible_communities
        )
        if community_id:
            qs = qs.filter(community_id=community_id)
        if blocked_ids:
            qs = qs.exclude(author_id__in=blocked_ids)
        return qs.filter(is_deleted=False).order_by("-created_at")

    def _can_edit_post(self, post: CommunityPost, user) -> bool:
        return post.author_id == user.id or self._is_owner_or_admin(post.community, user)

    def update(self, request, *args, **kwargs):
        # PATCH /posts/{id}/ had no permission check at all beyond
        # IsAuthenticated - any authenticated user could edit any post in
        # any community. This is now the one canonical, secured edit path
        # (the frontend previously called a nonexistent .../edit/ route -
        # see CommunityFeedScreen.tsx's editEndpoint, now pointed here).
        post = self.get_object()
        if not self._can_edit_post(post, request.user):
            raise PermissionDenied("You don't have permission to edit this post.")
        response = super().update(request, *args, **kwargs)
        from apps.communities.realtime import notify_post_updated

        notify_post_updated(post)
        return response

    def partial_update(self, request, *args, **kwargs):
        # No notify_post_updated call here: DRF's UpdateModelMixin.partial_update
        # is implemented as `return self.update(request, *args, **kwargs)` -
        # since self.update resolves to THIS class's own override above (not
        # DRF's base), super().partial_update() below already runs that
        # override in full, including its own notify_post_updated call. A
        # second call here fired the live "post updated" event twice for
        # every single PATCH - confirmed via live verification (two
        # community.post_updated socket deliveries for one edit request).
        post = self.get_object()
        if not self._can_edit_post(post, request.user):
            raise PermissionDenied("You don't have permission to edit this post.")
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        # Real deletion is intentionally routed through the delete_post
        # action below (soft delete + permission check + real-time event).
        # The default DELETE verb had zero permission check and would hard-
        # delete via the ORM - block it outright rather than maintaining a
        # second, divergent deletion mechanism.
        raise PermissionDenied("Use the delete action to remove a post.")

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        sample_limit = self._personalization_sample_limit(request)
        candidates = list(queryset[:sample_limit])
        metadata = self._build_community_metadata(request.user, candidates)
        profile = get_affinity_profile(request.user)
        if profile:
            for entry in metadata.values():
                entry["profile"] = profile
        ranked = rank_feed_items(candidates, request.user, feed_type="community", metadata_map=metadata)
        page = self.paginate_queryset(ranked)
        serializer = self.get_serializer(page if page is not None else ranked, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        log_feed_interaction(request.user, "community", "feed_impression", weight=0.05)
        return Response(serializer.data)

    def _personalization_sample_limit(self, request):
        return resolve_personalization_sample_limit(request.query_params.get("limit"))

    def _build_community_metadata(self, user, posts):
        if not posts:
            return {}
        community_ids = {post.community_id for post in posts if post.community_id}
        memberships = CommunityMembership.objects.active().filter(
            community_id__in=community_ids,
            user=user,
        ).values_list("community_id", flat=True)
        member_ids = {str(cid) for cid in memberships}
        metadata = {}
        for post in posts:
            community_id = str(post.community_id) if post.community_id else None
            metadata[str(post.id)] = {
                "source": {
                    "type": "community",
                    "id": community_id,
                    "is_member": community_id in member_ids if community_id else False,
                    "can_open": community_id in member_ids if community_id else False,
                }
            }
        return metadata

    def _get_membership(self, community: Community, user):
        return CommunityMembership.objects.active().filter(
            community=community,
            user=user,
        ).first()

    def _is_owner_or_admin(self, community: Community, user):
        membership = self._get_membership(community, user)
        if membership and membership.role in (CommunityRole.OWNER, CommunityRole.ADMIN, CommunityRole.MOD):
            return True
        partner = community.partner
        if not partner or not partner.main_conversation_id:
            return False
        return ConversationMember.objects.filter(
            conversation_id=partner.main_conversation_id,
            user=user,
            base_role__in=(BaseConversationRole.OWNER, BaseConversationRole.ADMIN),
            left_at__isnull=True,
        ).exists()

    def perform_create(self, serializer):
        community = serializer.validated_data["community"]
        membership = self._get_membership(community, self.request.user)
        if not membership:
            raise PermissionDenied("Join the community to post.")

        allowed_roles = None
        if community.post_policy == CommunityPostPolicy.ADMINS_ONLY:
            allowed_roles = (CommunityRole.OWNER, CommunityRole.ADMIN)
        elif community.post_policy == CommunityPostPolicy.MODS_ONLY:
            allowed_roles = (CommunityRole.OWNER, CommunityRole.ADMIN, CommunityRole.MOD)
        if allowed_roles and membership.role not in allowed_roles:
            raise PermissionDenied("Only admins/moderators can post.")

        status_val = CommunityPostStatus.PUBLISHED
        if community.require_post_approval:
            status_val = CommunityPostStatus.PENDING

        post = serializer.save(author=self.request.user, status=status_val)
        if status_val == CommunityPostStatus.PUBLISHED:
            from apps.communities.realtime import notify_post_created

            notify_post_created(post)

    @action(detail=True, methods=["post"], url_path="comment")
    def comment(self, request, pk=None):
        post = self.get_object()
        membership = self._get_membership(post.community, request.user)
        if not membership:
            raise PermissionDenied("Join the community to comment.")
        if not post.community.allow_comments:
            raise PermissionDenied("Comments are disabled.")
        comment = CommunityPostComment.objects.create(
            post=post,
            author=request.user,
            text=request.data.get("text", ""),
        )
        from apps.communities.realtime import notify_comment_created

        notify_comment_created(comment)
        return Response(CommunityPostCommentSerializer(comment).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="comment-room")
    def comment_room(self, request, pk=None):
        post = self.get_object()
        membership = self._get_membership(post.community, request.user)
        if not membership:
            raise PermissionDenied("Join the community to comment.")

        conversation = ensure_post_comment_conversation(
            post,
            actor=request.user,
            created_by=post.author or request.user,
            title=f"{post.community.name} comments",
            description=f"Comments for community post {post.id}",
        )
        ensure_conversation_member(conversation, request.user)

        return Response(
            {"conversation_id": str(conversation.id), "title": conversation.title},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get"], url_path="comments")
    def comments(self, request, pk=None):
        post = self.get_object()
        comments = post.comments.filter(is_deleted=False).select_related("author").order_by("created_at")
        return Response(CommunityPostCommentSerializer(comments, many=True).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="react")
    def react(self, request, pk=None):
        post = self.get_object()
        membership = self._get_membership(post.community, request.user)
        if not membership:
            raise PermissionDenied("Join the community to react.")
        if not post.community.allow_reactions:
            raise PermissionDenied("Reactions are disabled.")
        emoji = request.data.get("emoji")
        if not emoji:
            return Response({"detail": "emoji required."}, status=status.HTTP_400_BAD_REQUEST)

        action = request.data.get("action")
        existing = CommunityPostReaction.objects.filter(post=post, user=request.user).first()
        if action in ("remove", "unlike") or (action == "toggle" and existing):
            if existing:
                existing.delete()
            reactions = list(
                post.reactions.values("emoji").annotate(count=models.Count("id"))
            )
            return Response(
                {
                    "detail": "Reaction removed.",
                    "has_reacted": False,
                    "reactions": reactions,
                    "reactions_count": sum(item["count"] for item in reactions),
                },
                status=status.HTTP_200_OK,
            )

        reaction, created = CommunityPostReaction.objects.get_or_create(
            post=post,
            user=request.user,
            defaults={"emoji": emoji},
        )
        if not created and reaction.emoji != emoji:
            reaction.emoji = emoji
            reaction.save(update_fields=["emoji"])

        reactions = list(
            post.reactions.values("emoji").annotate(count=models.Count("id"))
        )
        return Response(
            {
                "detail": "Reaction saved.",
                "has_reacted": True,
                "reactions": reactions,
                "reactions_count": sum(item["count"] for item in reactions),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="delete")
    def delete_post(self, request, pk=None):
        post = self.get_object()
        is_owner = post.author_id == request.user.id
        if not (is_owner or self._is_owner_or_admin(post.community, request.user)):
            raise PermissionDenied("Not allowed to delete this post.")
        post.is_deleted = True
        post.save(update_fields=["is_deleted"])
        from apps.communities.realtime import notify_post_deleted

        notify_post_deleted(post)
        return Response({"detail": "Post deleted."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="broadcast")
    def broadcast(self, request, pk=None):
        post = self.get_object()
        membership = self._get_membership(post.community, request.user)
        is_owner = post.author_id == request.user.id
        if not (is_owner or self._is_owner_or_admin(post.community, request.user)):
            raise PermissionDenied("Not allowed to broadcast this post.")
        if not post.community.allow_broadcasts:
            raise PermissionDenied("Broadcasting is disabled for this community.")
        post.is_broadcast = True
        post.save(update_fields=["is_broadcast"])
        try:
            from apps.broadcasts.models import BroadcastItem, BroadcastSourceType
            from datetime import timedelta

            BroadcastItem.objects.update_or_create(
                source_type=BroadcastSourceType.COMMUNITY_POST,
                source_id=str(post.id),
                defaults={
                    "broadcasted_by": request.user,
                    "broadcasted_at": timezone.now(),
                    "expires_at": timezone.now() + timedelta(days=10),
                    "is_deleted": False,
                },
            )
        except Exception:
            pass
        return Response({"detail": "Post broadcasted."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="pin")
    def pin(self, request, pk=None):
        post = self.get_object()
        if not self._is_owner_or_admin(post.community, request.user):
            raise PermissionDenied("Only admins/moderators can pin.")
        post.is_pinned = True
        post.pinned_by = request.user
        post.pinned_at = timezone.now()
        post.save(update_fields=["is_pinned", "pinned_by", "pinned_at"])
        return Response({"detail": "Pinned."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="unpin")
    def unpin(self, request, pk=None):
        post = self.get_object()
        if not self._is_owner_or_admin(post.community, request.user):
            raise PermissionDenied("Only admins/moderators can unpin.")
        post.is_pinned = False
        post.pinned_by = None
        post.pinned_at = None
        post.save(update_fields=["is_pinned", "pinned_by", "pinned_at"])
        return Response({"detail": "Unpinned."}, status=status.HTTP_200_OK)
