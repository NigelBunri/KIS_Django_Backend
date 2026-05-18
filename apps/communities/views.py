# apps/communities/views.py
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
        - Otherwise return communities where the user is the owner or active member.
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
                        filter=models.Q(memberships__left_at__isnull=True, memberships__is_banned=False),
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
                memberships__left_at__isnull=True,
                memberships__is_banned=False,
            )
        )
        if partner_id:
            qs = qs.filter(partner_id=partner_id)
        return qs.distinct()

    def perform_create(self, serializer):
        from apps.accounts.tiers import get_user_tier_features, normalize_limit_value

        user = self.request.user
        features = get_user_tier_features(user)
        limit = normalize_limit_value(features.get("communities"), default=None)
        if limit is not None:
            count = Community.objects.filter(owner=user).count()
            if count >= limit:
                raise ValidationError({"detail": "Community limit reached for your plan."})
        serializer.save()

    def _get_membership(self, community: Community, user):
        return CommunityMembership.objects.filter(
            community=community,
            user=user,
            left_at__isnull=True,
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

        if community.main_conversation_id:
            ConversationMember.objects.get_or_create(
                conversation=community.main_conversation,
                user=user,
                defaults={"base_role": BaseConversationRole.MEMBER},
            )
        if community.posts_conversation_id:
            ConversationMember.objects.get_or_create(
                conversation=community.posts_conversation,
                user=user,
                defaults={"base_role": BaseConversationRole.MEMBER},
            )

    def _update_membership_role(self, membership: CommunityMembership, role: str):
        membership.role = role
        membership.save(update_fields=["role"])
        return membership

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
        qs = CommunityMembership.objects.filter(community=community, left_at__isnull=True)
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

        membership, _ = CommunityMembership.objects.get_or_create(
            community=community,
            user=user,
            defaults={"role": CommunityRole.MEMBER},
        )
        if membership.left_at is not None:
            membership.left_at = None
            membership.is_banned = False
            membership.save(update_fields=["left_at", "is_banned"])

        self._ensure_conversation_membership(community, user)

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

        for target in users:
            if target.id == request.user.id:
                continue
            m, created = CommunityMembership.objects.get_or_create(
                community=community,
                user=target,
                defaults={"role": CommunityRole.MEMBER},
            )
            if m.left_at is not None or m.is_banned:
                m.left_at = None
                m.is_banned = False
                m.save(update_fields=["left_at", "is_banned"])
            self._ensure_conversation_membership(community, target)
            if created:
                added.append(str(target.id))

        return Response({"added": added, "count": len(added)}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="leave")
    def leave(self, request, pk=None):
        community = self.get_object()
        membership = self._get_membership(community, request.user)
        if not membership:
            return Response({"detail": "Not a member."}, status=status.HTTP_400_BAD_REQUEST)
        membership.left_at = timezone.now()
        membership.save(update_fields=["left_at"])
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
        obj, _ = CommunityJoinRequest.objects.get_or_create(
            community=community,
            user=user,
            defaults={"message": request.data.get("message", "")},
        )
        serializer = CommunityJoinRequestSerializer(obj)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="approve-request")
    def approve_request(self, request, pk=None):
        community = self.get_object()
        if not self._has_owner_privileges(community, request.user):
            raise PermissionDenied("Only admins can approve requests.")

        request_id = request.data.get("request_id")
        join_req = CommunityJoinRequest.objects.filter(id=request_id, community=community).first()
        if not join_req:
            return Response({"detail": "Request not found."}, status=status.HTTP_404_NOT_FOUND)

        join_req.status = CommunityJoinRequestStatus.APPROVED
        join_req.reviewed_by = request.user
        join_req.reviewed_at = timezone.now()
        join_req.save(update_fields=["status", "reviewed_by", "reviewed_at"])

        CommunityMembership.objects.update_or_create(
            community=community,
            user=join_req.user,
            defaults={"role": CommunityRole.MEMBER, "left_at": None, "is_banned": False},
        )

        self._ensure_conversation_membership(community, join_req.user)

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

        join_req.status = CommunityJoinRequestStatus.REJECTED
        join_req.reviewed_by = request.user
        join_req.reviewed_at = timezone.now()
        join_req.save(update_fields=["status", "reviewed_by", "reviewed_at"])

        return Response({"detail": "Rejected."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="members/set-admin")
    def set_admin(self, request, pk=None):
        community = self.get_object()
        if not self._has_owner_privileges(community, request.user):
            raise PermissionDenied("Only owners can change admin roles.")

        user_id = request.data.get("user_id")
        if not user_id:
            return Response({"detail": "user_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        membership = CommunityMembership.objects.filter(
            community=community,
            user_id=user_id,
            left_at__isnull=True,
        ).first()
        if not membership:
            return Response({"detail": "Member not found."}, status=status.HTTP_404_NOT_FOUND)
        if membership.role == CommunityRole.OWNER:
            return Response({"detail": "Owner role cannot be modified."}, status=status.HTTP_400_BAD_REQUEST)

        make_admin = bool(request.data.get("make_admin", True))
        target_role = CommunityRole.ADMIN if make_admin else CommunityRole.MEMBER
        membership = self._update_membership_role(membership, target_role)
        return Response(
            {"user_id": str(user_id), "role": membership.role},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="members/set-role")
    def set_member_role(self, request, pk=None):
        community = self.get_object()
        if not self._has_owner_privileges(community, request.user):
            raise PermissionDenied("Only owners can change member roles.")

        user_id = request.data.get("user_id")
        role_value = request.data.get("role")
        if not user_id or not role_value:
            return Response({"detail": "user_id and role are required."}, status=status.HTTP_400_BAD_REQUEST)

        membership = CommunityMembership.objects.filter(
            community=community,
            user_id=user_id,
            left_at__isnull=True,
        ).first()
        if not membership:
            return Response({"detail": "Member not found."}, status=status.HTTP_404_NOT_FOUND)
        if membership.role == CommunityRole.OWNER:
            return Response({"detail": "Owner role cannot be modified."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            target_role = CommunityRole(role_value)
        except ValueError:
            return Response({"detail": "Invalid role value."}, status=status.HTTP_400_BAD_REQUEST)

        if target_role == CommunityRole.OWNER:
            return Response({"detail": "Cannot assign owner role."}, status=status.HTTP_400_BAD_REQUEST)

        membership = self._update_membership_role(membership, target_role)
        serializer = CommunityMembershipSerializer(membership)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="members/block")
    def block_member(self, request, pk=None):
        community = self.get_object()
        if not self._has_owner_privileges(community, request.user):
            raise PermissionDenied("Only admins can block members.")
        user_id = request.data.get("user_id")
        if not user_id:
            return Response({"detail": "user_id required."}, status=status.HTTP_400_BAD_REQUEST)
        membership = CommunityMembership.objects.filter(
            community=community,
            user_id=user_id,
            left_at__isnull=True,
        ).first()
        if membership:
            membership.is_banned = True
            membership.left_at = timezone.now()
            membership.save(update_fields=["is_banned", "left_at"])

        ban, _ = CommunityBan.objects.update_or_create(
            community=community,
            user_id=user_id,
            defaults={"reason": "Blocked by community admin", "banned_by": request.user},
        )
        return Response(CommunityBanSerializer(ban).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="members/remove")
    def remove_member(self, request, pk=None):
        community = self.get_object()
        if not self._has_owner_privileges(community, request.user):
            raise PermissionDenied("Only admins can remove members.")
        user_id = request.data.get("user_id")
        if not user_id:
            return Response({"detail": "user_id required."}, status=status.HTTP_400_BAD_REQUEST)
        membership = CommunityMembership.objects.filter(
            community=community,
            user_id=user_id,
            left_at__isnull=True,
        ).first()
        if not membership:
            return Response({"detail": "Member not found."}, status=status.HTTP_404_NOT_FOUND)
        membership.left_at = timezone.now()
        membership.save(update_fields=["left_at"])
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
        ban, _ = CommunityBan.objects.update_or_create(
            community=community,
            user_id=user_id,
            defaults={
                "reason": request.data.get("reason", ""),
                "banned_by": request.user,
                "expires_at": request.data.get("expires_at"),
            },
        )
        CommunityMembership.objects.filter(community=community, user_id=user_id).update(is_banned=True)
        return Response(CommunityBanSerializer(ban).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="unban")
    def unban(self, request, pk=None):
        community = self.get_object()
        if not self._has_owner_privileges(community, request.user):
            raise PermissionDenied("Only admins can unban.")

        user_id = request.data.get("user_id")
        CommunityBan.objects.filter(community=community, user_id=user_id).delete()
        CommunityMembership.objects.filter(community=community, user_id=user_id).update(is_banned=False)
        return Response({"detail": "Unbanned."}, status=status.HTTP_200_OK)


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
        qs = CommunityPost.objects.select_related("community", "author")
        if community_id:
            qs = qs.filter(community_id=community_id)
        if blocked_ids:
            qs = qs.exclude(author_id__in=blocked_ids)
        return qs.filter(is_deleted=False).order_by("-created_at")

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
        memberships = CommunityMembership.objects.filter(
            community_id__in=community_ids,
            user=user,
            left_at__isnull=True,
            is_banned=False,
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
        return CommunityMembership.objects.filter(
            community=community,
            user=user,
            left_at__isnull=True,
            is_banned=False,
        ).first()

    def _is_owner_or_admin(self, community: Community, user):
        membership = self._get_membership(community, user)
        if membership and membership.role in (CommunityRole.OWNER, CommunityRole.ADMIN):
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

        serializer.save(author=self.request.user, status=status_val)

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
            return Response({"detail": "Reaction removed.", "has_reacted": False}, status=status.HTTP_200_OK)

        reaction, created = CommunityPostReaction.objects.get_or_create(
            post=post,
            user=request.user,
            defaults={"emoji": emoji},
        )
        if not created and reaction.emoji != emoji:
            reaction.emoji = emoji
            reaction.save(update_fields=["emoji"])

        return Response({"detail": "Reaction saved.", "has_reacted": True}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="delete")
    def delete_post(self, request, pk=None):
        post = self.get_object()
        membership = self._get_membership(post.community, request.user)
        is_owner = post.author_id == request.user.id
        if not (is_owner or self._is_owner_or_admin(post.community, request.user)):
            raise PermissionDenied("Not allowed to delete this post.")
        post.is_deleted = True
        post.save(update_fields=["is_deleted"])
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
