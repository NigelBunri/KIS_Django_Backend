# chat/views.py
import os
import uuid
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.db import DatabaseError
from django.db.models import Q

from .internal_auth import require_internal_auth
from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import (
    Conversation,
    ConversationMember,
    ConversationSettings,
    MessageThreadLink,
    ConversationType,
    BaseConversationRole,
    ConversationSendPolicy,
    ConversationRequestState,
)
from .serializers import (
    ConversationListSerializer,
    ConversationDetailSerializer,
    ConversationCreateSerializer,
    DirectConversationCreateSerializer,
    ConversationMemberSerializer,
    ConversationSettingsSerializer,
    MessageThreadLinkSerializer,
)
from .services import allocate_conversation_seq, get_or_create_direct_conversation, user_is_active_member

from apps.accounts.models import User
from apps.partners.models import Partner
from apps.partners.services import ensure_partner_policy, evaluate_partner_dlp, log_partner_audit
from apps.partners.services import dispatch_partner_webhooks
from apps.groups.models import Group
from apps.channels.models import Channel
from apps.communities.models import Community


def _extract_phone_participants(raw_data) -> list[str]:
    """
    Accepts flexible shapes from RN:
      - participants: ["+237..."]
      - user_id: { participants: ["+237..."] }
      - user_id: { participant: ["+237..."] }
    Returns list of normalized phone strings.
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

    phones = []
    for p in participants:
        if p is None:
            continue
        s = str(p).strip()
        if s:
            phones.append(s)

    # unique preserve order
    seen = set()
    out = []
    for ph in phones:
        if ph not in seen:
            seen.add(ph)
            out.append(ph)
    return out


def _resolve_partner_from_conversation(conversation: Conversation) -> Partner | None:
    partner = Partner.objects.filter(main_conversation_id=conversation.id).first()
    if partner:
        return partner

    channel = Channel.objects.filter(conversation_id=conversation.id).select_related("partner", "community__partner").first()
    if channel:
        return channel.partner or (channel.community.partner if channel.community else None)

    group = Group.objects.filter(conversation_id=conversation.id).select_related("partner", "community__partner").first()
    if group:
        return group.partner or (group.community.partner if group.community else None)

    community = Community.objects.filter(
        Q(main_conversation_id=conversation.id) | Q(posts_conversation_id=conversation.id)
    ).select_related("partner").first()
    if community:
        return community.partner

    return None


def _resolve_peer_user(request_user: User, raw_data: dict) -> User:
    """
    Resolves the peer user for a direct chat.
    Priority:
      1) peer_user_id (if provided)
      2) first phone number in participants payload
    """
    peer_user_id = raw_data.get("peer_user_id")
    if peer_user_id is not None:
        try:
            peer_id = int(peer_user_id)
        except Exception:
            raise ValidationError({"peer_user_id": "peer_user_id must be an integer"})

        if peer_id == request_user.id:
            raise ValidationError({"peer_user_id": "Cannot create a direct chat with yourself."})

        try:
            return User.objects.get(id=peer_id)
        except User.DoesNotExist:
            raise ValidationError({"peer_user_id": "Peer user does not exist."})

    phones = _extract_phone_participants(raw_data)
    if not phones:
        raise ValidationError(
            "Either 'peer_user_id' or at least one participant phone number is required."
        )

    # For direct chat, use the first phone only
    first_phone = phones[0]
    try:
        peer = User.objects.get(phone=first_phone)
    except User.DoesNotExist:
        raise ValidationError({"participants": f"User with phone number {first_phone} does not exist."})

    if peer.id == request_user.id:
        raise ValidationError({"participants": "Cannot create a direct chat with yourself."})

    return peer


class ConversationViewSet(viewsets.ModelViewSet):
    """
    /api/chat/conversations/

    - list/retrieve/create/update
    - direct: create/fetch 1:1 DM using request workflow
    - accept-request / reject-request
    - update-last-message: internal endpoint called by Nest
    """
    permission_classes = [IsAuthenticated]

    # ------------------------------------------------------------------
    # Query / serializers
    # ------------------------------------------------------------------
    def get_queryset(self):
        user = self.request.user
        qs = (
            Conversation.objects
            .filter(memberships__user=user, memberships__left_at__isnull=True)
            .distinct()
            .select_related('created_by', 'request_initiator', 'request_recipient')
            .select_related('community_main', 'community_posts')
            .prefetch_related('memberships__user', 'memberships')  # memberships itself too
        )
        if self.action == "list":
            qs = qs.exclude(type=ConversationType.POST)
            qs = qs.exclude(
                Q(group__partner__isnull=False)
                | Q(group__community__partner__isnull=False)
                | Q(channel__partner__isnull=False)
                | Q(channel__community__partner__isnull=False)
                | Q(community_main__partner__isnull=False)
                | Q(community_posts__partner__isnull=False)
                | Q(partner_main__isnull=False)
            )
        q = (self.request.query_params.get('q') or self.request.query_params.get('search') or '').strip()
        if q:
            qs = qs.filter(
                Q(title__icontains=q)
                | Q(description__icontains=q)
                | Q(memberships__user__display_name__icontains=q)
                | Q(memberships__user__phone__icontains=q)
                | Q(memberships__user__username__icontains=q)
            ).distinct()
        return qs

    def get_serializer_class(self):
        if self.action == 'list':
            return ConversationListSerializer
        if self.action in ('create', 'update', 'partial_update'):
            return ConversationCreateSerializer
        if self.action == 'direct':
            return DirectConversationCreateSerializer
        return ConversationDetailSerializer

    def perform_create(self, serializer):
        serializer.save()

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        if not user_is_active_member(request.user, instance):
            return Response(
                {"detail": "You are not a member of this conversation."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(self.get_serializer(instance).data)

    @action(detail=False, methods=["get"], url_path="search")
    def search(self, request):
        query = (request.query_params.get("q") or "").strip()
        kind = (request.query_params.get("type") or "").strip()
        qs = self.get_queryset()
        if kind:
            qs = qs.filter(type=kind)
        if query:
            qs = qs.filter(
                Q(title__icontains=query)
                | Q(description__icontains=query)
                | Q(last_message_preview__icontains=query)
                | Q(memberships__user__display_name__icontains=query)
                | Q(memberships__user__phone__icontains=query)
                | Q(memberships__user__username__icontains=query)
            ).distinct()
        qs = qs.order_by("-last_message_at", "-updated_at")[:50]
        data = ConversationListSerializer(qs, many=True, context={"request": request}).data
        return Response({"results": data, "count": len(data)}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="participant-search")
    def participant_search(self, request):
        query = (request.query_params.get("q") or "").strip()
        if not query:
            return Response({"results": [], "count": 0}, status=status.HTTP_200_OK)

        qs = (
            ConversationMember.objects
            .filter(
                conversation__in=self.get_queryset(),
                left_at__isnull=True,
            )
            .select_related("user", "conversation")
            .filter(
                Q(display_name__icontains=query)
                | Q(user__display_name__icontains=query)
                | Q(user__phone__icontains=query)
                | Q(user__username__icontains=query)
            )
            .order_by("user__display_name", "user__phone")[:50]
        )
        results = []
        seen: set[tuple[str, str]] = set()
        for member in qs:
            key = (str(member.conversation_id), str(member.user_id))
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "conversation_id": str(member.conversation_id),
                    "conversation_title": member.conversation.title or "",
                    "user": {
                        "id": str(member.user_id),
                        "display_name": member.user.display_name or member.user.phone or f"User {member.user_id}",
                        "phone": member.user.phone,
                        "username": member.user.username,
                    },
                    "membership_display_name": member.display_name,
                    "base_role": member.base_role,
                }
            )
        return Response({"results": results, "count": len(results)}, status=status.HTTP_200_OK)

    # ------------------------------------------------------------------
    # Direct conversations (DM request flow)
    # ------------------------------------------------------------------
    @action(detail=False, methods=['post'], url_path='direct')
    def direct(self, request):
        """
        POST /api/v1/conversations/direct/

        Supports:
          - {"peer_user_id": 123}
          - {"participants": ["+237..."], "user_id": {"participants": ["+237..."]}, ...}

        If new, it creates a pending DM request:
          - request_state=PENDING
          - request_initiator=request.user
          - request_recipient=peer_user
        """
        # Still validate basic shape using your serializer (keeps compatibility),
        # but we also support phone payloads.
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=False)  # do not hard fail; we resolve ourselves

        peer_user = _resolve_peer_user(request.user, request.data)

        conversation, created = get_or_create_direct_conversation(
            user_a=request.user,
            user_b=peer_user,
            initiator=request.user,
            use_request_flow=True,
        )

        # Safety: ensure requester is actually a member (should always be true)
        if not user_is_active_member(request.user, conversation):
            # This indicates corrupted state; better to fail loudly than create ghost conversations.
            raise PermissionDenied("You are not a member of this conversation.")

        data = ConversationDetailSerializer(conversation).data
        return Response(
            data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    # ------------------------------------------------------------------
    # Members
    # ------------------------------------------------------------------
    @action(detail=True, methods=['post'], url_path='members')
    def add_member(self, request, pk=None):
        conversation = self.get_object()
        if not user_is_active_member(request.user, conversation):
            return Response({"detail": "You are not a member of this conversation."}, status=403)

        user_id = request.data.get('user_id')
        base_role = request.data.get('base_role', BaseConversationRole.MEMBER)

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({"detail": "User does not exist."}, status=400)

        member, created = ConversationMember.objects.get_or_create(
            conversation=conversation,
            user=user,
            defaults={'base_role': base_role},
        )

        if not created and member.left_at:
            member.left_at = None
            member.base_role = base_role
            member.save(update_fields=["left_at", "base_role"])

        return Response(ConversationMemberSerializer(member).data, status=201)

    @action(detail=True, methods=['post'], url_path='members/remove')
    def remove_member(self, request, pk=None):
        conversation = self.get_object()
        if not user_is_active_member(request.user, conversation):
            return Response({"detail": "You are not a member of this conversation."}, status=403)

        partner_owner = getattr(conversation, "partner_main", None)
        if partner_owner and partner_owner.slug in ("cc", "kis"):
            return Response(
                {"detail": "Christian Community membership is mandatory."},
                status=403,
            )

        user_id = request.data.get('user_id')
        if not user_id:
            return Response({"detail": "user_id is required."}, status=400)

        requester = ConversationMember.objects.filter(
            conversation=conversation,
            user=request.user,
            left_at__isnull=True,
        ).first()
        if not requester:
            return Response({"detail": "You are not a member of this conversation."}, status=403)

        is_admin = requester.base_role in (BaseConversationRole.OWNER, BaseConversationRole.ADMIN)
        if not is_admin and str(user_id) != str(request.user.id):
            return Response({"detail": "Only admins can remove other members."}, status=403)

        member = ConversationMember.objects.filter(
            conversation=conversation,
            user_id=user_id,
            left_at__isnull=True,
        ).first()
        if not member:
            return Response({"detail": "Member not found."}, status=404)

        member.left_at = timezone.now()
        member.save(update_fields=["left_at"])
        return Response(ConversationMemberSerializer(member).data, status=200)

    @action(detail=True, methods=['post'], url_path='members/role')
    def set_member_role(self, request, pk=None):
        conversation = self.get_object()
        if not user_is_active_member(request.user, conversation):
            return Response({"detail": "You are not a member of this conversation."}, status=403)

        user_id = request.data.get('user_id')
        base_role = request.data.get('base_role')
        if not user_id or not base_role:
            return Response({"detail": "user_id and base_role are required."}, status=400)

        requester = ConversationMember.objects.filter(
            conversation=conversation,
            user=request.user,
            left_at__isnull=True,
        ).first()
        if not requester or requester.base_role not in (BaseConversationRole.OWNER, BaseConversationRole.ADMIN):
            return Response({"detail": "Only admins can change roles."}, status=403)

        if base_role not in BaseConversationRole.values:
            return Response({"detail": "Invalid base_role."}, status=400)

        member = ConversationMember.objects.filter(
            conversation=conversation,
            user_id=user_id,
            left_at__isnull=True,
        ).first()
        if not member:
            return Response({"detail": "Member not found."}, status=404)

        member.base_role = base_role
        member.save(update_fields=["base_role"])
        return Response(ConversationMemberSerializer(member).data, status=200)

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    @action(detail=True, methods=['patch'], url_path='settings')
    def update_settings(self, request, pk=None):
        conversation = self.get_object()
        if not user_is_active_member(request.user, conversation):
            return Response({"detail": "You are not a member of this conversation."}, status=403)

        settings_obj, _ = ConversationSettings.objects.get_or_create(conversation=conversation)
        serializer = ConversationSettingsSerializer(settings_obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    # ------------------------------------------------------------------
    # DM request accept / reject
    # ------------------------------------------------------------------
    @action(detail=True, methods=['post'], url_path='accept-request')
    def accept_request(self, request, pk=None):
        conversation = self.get_object()

        if conversation.type != ConversationType.DIRECT:
            return Response({"detail": "Not a direct conversation"}, status=400)
        if conversation.request_state != ConversationRequestState.PENDING:
            return Response({"detail": "Not pending"}, status=400)
        if conversation.request_recipient_id != request.user.id:
            return Response({"detail": "Not recipient"}, status=403)

        conversation.request_state = ConversationRequestState.ACCEPTED
        conversation.request_accepted_at = timezone.now()
        conversation.save(update_fields=['request_state', 'request_accepted_at'])

        return Response(ConversationDetailSerializer(conversation).data, status=200)

    @action(detail=True, methods=['post'], url_path='block_chat')
    def block_chat(self, request, pk=None):
        conversation = Conversation.objects.get(pk=pk)
        if conversation.type != ConversationType.DIRECT:
            return Response({"detail": "Not a direct conversation"}, status=400)
        conversation.is_locked = True
        conversation.locked_by = request.user
        conversation.save(update_fields=['is_locked', 'locked_by'])
        response = ConversationDetailSerializer(conversation).data
        print("see Response: ", response)
        return Response(response, status=200)

    @action(detail=True, methods=['post'], url_path='archive')
    def archive(self, request, pk=None):
        conversation = self.get_object()
        if not user_is_active_member(request.user, conversation):
            return Response({"detail": "You are not a member of this conversation."}, status=403)

        archived = request.data.get('archived', True)
        conversation.is_archived = bool(archived)
        conversation.archived_by = request.user if archived else None
        conversation.save(update_fields=['is_archived', 'archived_by'])
        return Response(ConversationDetailSerializer(conversation).data, status=200)

    @action(detail=True, methods=['post'], url_path='lock')
    def lock(self, request, pk=None):
        conversation = self.get_object()
        if not user_is_active_member(request.user, conversation):
            return Response({"detail": "You are not a member of this conversation."}, status=403)

        locked = request.data.get('locked', True)
        conversation.is_locked = bool(locked)
        conversation.locked_by = request.user if locked else None
        conversation.save(update_fields=['is_locked', 'locked_by'])
        return Response(ConversationDetailSerializer(conversation).data, status=200)

    # ------------------------------------------------------------------
    # 🔐 INTERNAL: last-message update (called by NestJS)
    # ------------------------------------------------------------------
    @action(
        detail=True,
        methods=['patch'],
        url_path='update-last-message',
        permission_classes=[],
        authentication_classes=[],
    )
    def update_last_message(self, request, pk=None):
        require_internal_auth(request)

        try:
            conversation = Conversation.objects.get(pk=pk)
        except Conversation.DoesNotExist:
            return Response({"detail": "Not found"}, status=404)

        incoming_at = request.data.get('last_message_at')
        preview = (request.data.get('last_message_preview') or '')[:255]

        if not incoming_at:
            return Response({"detail": "last_message_at required"}, status=400)

        dt = parse_datetime(incoming_at)
        if not dt:
            return Response({"detail": "Invalid datetime"}, status=400)

        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone=timezone.utc)

        if conversation.last_message_at and dt < conversation.last_message_at:
            return Response({"ok": True, "ignored": True})

        conversation.last_message_at = dt
        conversation.last_message_preview = preview
        conversation.save(update_fields=['last_message_at', 'last_message_preview'])

        return Response({"ok": True})

    @action(
        detail=True,
        methods=['patch'],
        url_path='update-read-state',
        permission_classes=[],
        authentication_classes=[],
    )
    def update_read_state(self, request, pk=None):
        require_internal_auth(request)

        user_id = request.data.get("user_id")
        incoming_seq = request.data.get("last_read_seq")
        incoming_at = request.data.get("last_read_at")

        if not user_id:
            return Response({"detail": "user_id required"}, status=400)
        if incoming_seq is None:
            return Response({"detail": "last_read_seq required"}, status=400)

        try:
            last_read_seq = max(int(incoming_seq), 0)
        except (TypeError, ValueError):
            return Response({"detail": "last_read_seq must be an integer"}, status=400)

        try:
            conversation = Conversation.objects.get(pk=pk)
        except Conversation.DoesNotExist:
            return Response({"detail": "Not found"}, status=404)

        member = ConversationMember.objects.filter(
            conversation=conversation,
            user_id=user_id,
            left_at__isnull=True,
        ).first()
        if not member:
            return Response({"detail": "Member not found"}, status=404)

        next_seq = min(last_read_seq, int(conversation.last_message_seq or 0))
        if next_seq <= int(member.last_read_seq or 0):
            return Response({
                "ok": True,
                "ignored": True,
                "last_read_seq": int(member.last_read_seq or 0),
            })

        parsed_at = timezone.now()
        if incoming_at:
            parsed = parse_datetime(str(incoming_at))
            if not parsed:
                return Response({"detail": "Invalid last_read_at"}, status=400)
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, timezone=timezone.utc)
            parsed_at = parsed

        member.last_read_seq = next_seq
        member.last_read_at = parsed_at
        member.save(update_fields=["last_read_seq", "last_read_at"])

        return Response({
            "ok": True,
            "last_read_seq": int(member.last_read_seq or 0),
            "last_read_at": member.last_read_at.isoformat() if member.last_read_at else None,
        })


    @action(
        detail=True,
        methods=['post'],
        url_path='allocate-seq',
        permission_classes=[],
        authentication_classes=[],
    )
    def allocate_seq(self, request, pk=None):
        require_internal_auth(request)

        try:
            conversation = Conversation.objects.get(pk=pk)
        except Conversation.DoesNotExist:
            return Response({"detail": "Not found"}, status=404)

        seq = allocate_conversation_seq(conversation)

        return Response({"seq": seq})

    @action(
        detail=True,
        methods=['get'],
        url_path='member-ids',
        permission_classes=[],
        authentication_classes=[],
    )
    def member_ids(self, request, pk=None):
        require_internal_auth(request)

        try:
            conversation = Conversation.objects.get(pk=pk)
        except Conversation.DoesNotExist:
            return Response({"user_ids": []}, status=404)

        user_ids = list(
            ConversationMember.objects.filter(
                conversation=conversation,
                left_at__isnull=True,
                is_blocked=False,
            ).values_list("user_id", flat=True)
        )
        return Response({"user_ids": [str(uid) for uid in user_ids]})

    
    @action(
        detail=True,
        methods=['get'],
        url_path='ws-perms',
        permission_classes=[],
        authentication_classes=[],
    )
    def ws_perms(self, request, pk=None):
        require_internal_auth(request)

        try:
            uuid.UUID(str(pk))
        except Exception:
            return Response({"isMember": False, "isBlocked": False, "role": "member", "scopes": []})

        user_id = request.query_params.get("userId")
        if not user_id:
            auth_header = request.headers.get("Authorization", "")
            scheme = os.environ.get("DJANGO_AUTH_SCHEME", "Bearer").strip()
            prefix = f"{scheme} "
            if auth_header.startswith(prefix):
                token = auth_header[len(prefix):].strip()
                if token:
                    try:
                        jwt_auth = JWTAuthentication()
                        validated = jwt_auth.get_validated_token(token)
                        user = jwt_auth.get_user(validated)
                        if user and user.is_active:
                            user_id = str(user.id)
                    except Exception:
                        # Internal callers sometimes pass userId as the bearer token.
                        user_id = token
        if not user_id:
            return Response({"isMember": False, "isBlocked": False, "role": "member", "scopes": []})

        try:
            conversation = Conversation.objects.get(pk=pk)
        except Conversation.DoesNotExist:
            return Response({"isMember": False, "isBlocked": False, "role": "member", "scopes": []})

        member = ConversationMember.objects.filter(
            conversation=conversation,
            user_id=user_id,
            left_at__isnull=True,
        ).first()

        if not member:
            return Response({"isMember": False, "isBlocked": False, "role": "member", "scopes": []})

        if member.is_blocked:
            return Response({"isMember": True, "isBlocked": True, "role": member.base_role, "scopes": []})

        can_send = True
        settings = ConversationSettings.objects.filter(conversation=conversation).first()
        if (
            conversation.type == ConversationType.DIRECT
            and conversation.request_state == ConversationRequestState.PENDING
            and conversation.request_recipient_id == member.user_id
        ):
            can_send = False
        if conversation.is_locked and member.base_role not in (BaseConversationRole.OWNER, BaseConversationRole.ADMIN):
            can_send = False
        if member.base_role == BaseConversationRole.READONLY:
            can_send = False
        if settings and settings.send_policy == ConversationSendPolicy.ADMINS_ONLY:
            if member.base_role not in (BaseConversationRole.OWNER, BaseConversationRole.ADMIN):
                can_send = False

        scopes = []
        if member.base_role in (BaseConversationRole.OWNER, BaseConversationRole.ADMIN):
            scopes.append("chat:admin")

        return Response({
            "isMember": True,
            "isBlocked": False,
            "role": member.base_role,
            "canSend": can_send,
            "scopes": scopes,
        })

    @action(
        detail=True,
        methods=["post"],
        url_path="policy-check",
        permission_classes=[],
        authentication_classes=[],
    )
    def policy_check(self, request, pk=None):
        require_internal_auth(request)

        action = (request.data.get("action") or "").lower()
        user_id = request.data.get("userId") or request.data.get("user_id")
        text = request.data.get("text") or ""
        if action not in ("send", "edit", "delete"):
            return Response({"allowed": True})

        try:
            conversation = Conversation.objects.get(pk=pk)
        except Conversation.DoesNotExist:
            return Response({"allowed": True})

        partner = _resolve_partner_from_conversation(conversation)
        if not partner:
            return Response({"allowed": True})

        policy = ensure_partner_policy(partner)
        compliance = (policy.settings or {}).get("compliance", {})
        if action == "delete" and compliance.get("legal_hold_enabled"):
            log_partner_audit(
                partner=partner,
                actor=None,
                action="partner.legal_hold.block",
                target_type="conversation",
                target_id=str(conversation.id),
                metadata={"reason": "legal_hold", "actor_id": str(user_id) if user_id else None},
                request=request,
            )
            return Response({"allowed": False, "reason": "legal_hold"}, status=403)

        dlp = evaluate_partner_dlp(partner, text or "")
        if dlp["blocked"]:
            log_partner_audit(
                partner=partner,
                actor=None,
                action="partner.dlp.block",
                target_type="conversation",
                target_id=str(conversation.id),
                metadata={"matches": dlp["blocked"], "actor_id": str(user_id) if user_id else None},
                request=request,
            )
            return Response({"allowed": False, "reason": "dlp_blocked", "matches": dlp["blocked"]}, status=403)
        if dlp["warn"]:
            log_partner_audit(
                partner=partner,
                actor=None,
                action="partner.dlp.warn",
                target_type="conversation",
                target_id=str(conversation.id),
                metadata={"matches": dlp["warn"], "actor_id": str(user_id) if user_id else None},
                request=request,
            )

        return Response({"allowed": True, "warn": dlp["warn"]})

    @action(
        detail=True,
        methods=["post"],
        url_path="webhook-dispatch",
        permission_classes=[],
        authentication_classes=[],
    )
    def webhook_dispatch(self, request, pk=None):
        require_internal_auth(request)

        event = (request.data.get("event") or "").strip()
        payload = request.data.get("payload") or {}
        if not event:
            return Response({"detail": "event is required."}, status=400)

        try:
            conversation = Conversation.objects.get(pk=pk)
        except Conversation.DoesNotExist:
            return Response({"detail": "Conversation not found."}, status=404)

        partner = _resolve_partner_from_conversation(conversation)
        if not partner:
            return Response({"delivered": 0})

        delivered = dispatch_partner_webhooks(
            partner=partner,
            event=event,
            payload={
                "conversation_id": str(conversation.id),
                **(payload or {}),
            },
        )
        return Response({"delivered": delivered})



# ----------------------------------------------------------------------
# Threads
# ----------------------------------------------------------------------
class MessageThreadLinkViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    queryset = MessageThreadLink.objects.all()
    serializer_class = MessageThreadLinkSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        parent = serializer.validated_data['parent_conversation']
        if not user_is_active_member(self.request.user, parent):
            raise PermissionDenied("Not a member")
        serializer.save(created_by=self.request.user)
