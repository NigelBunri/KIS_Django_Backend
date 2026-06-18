# chat/views.py
import logging
import os
import re
import uuid
import phonenumbers as _phonenumbers
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Q

from .internal_auth import require_internal_auth
from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import (
    Conversation,
    ConversationMember,
    ConversationSettings,
    MessageThreadLink,
    ConversationType,
    BaseConversationRole,
    ConversationNotificationLevel,
    ConversationSendPolicy,
    ConversationJoinPolicy,
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

logger = logging.getLogger(__name__)
from .services import allocate_conversation_seq, get_or_create_direct_conversation, user_is_active_member
from apps.notifications.realtime import notify_main_tab_badges_updated

from apps.accounts.models import User
from apps.partners.models import Partner
from apps.partners.services import ensure_partner_policy, evaluate_partner_dlp, log_partner_audit
from apps.partners.services import dispatch_partner_webhooks
from apps.groups.models import Group, GroupMembership, GroupRole
from apps.channels.models import Channel
from apps.communities.models import Community, CommunityMembership, CommunityRole


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
        s = User.objects.normalize_phone(str(p).strip())
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


def _phone_variants(phone: str) -> tuple[list[str], list[str]]:
    """Return (phone_variants, digit_variants) for a loose phone lookup.

    Handles every common format a React Native contact can arrive in:
      "+237676139884"  →  exact, digits-only, national ("676139884")
      "676139884"      →  exact, e164 ("+237676139884"), national
      "00237676139884" →  same as the +237 form
    """
    raw = (phone or "").strip()
    digits = re.sub(r"\D", "", raw)

    phones: list[str] = [raw]
    if digits:
        phones.append(digits)
        phones.append(f"+{digits}")
        if digits.startswith("00") and len(digits) > 2:
            intl = digits[2:]
            phones.extend([intl, f"+{intl}"])
        if digits.startswith("0") and len(digits) > 1:
            stripped = digits[1:]
            phones.extend([stripped, f"+{stripped}"])

    # Try e164 and extract national number for local-only DB entries
    for candidate in (raw, digits):
        if not candidate:
            continue
        for region in ("CM", None):
            try:
                parsed = _phonenumbers.parse(candidate, region)
                if _phonenumbers.is_possible_number(parsed):
                    e164 = _phonenumbers.format_number(parsed, _phonenumbers.PhoneNumberFormat.E164)
                    phones.append(e164)
                    phones.append(e164.lstrip("+"))
                    national = str(parsed.national_number)
                    if national:
                        phones.append(national)
                    break
            except Exception:
                continue

    seen: set[str] = set()
    unique_phones: list[str] = []
    for v in phones:
        v = v.strip()
        if v and v not in seen:
            seen.add(v)
            unique_phones.append(v)

    unique_digits = list({re.sub(r"\D", "", v) for v in unique_phones if re.sub(r"\D", "", v)})
    return unique_phones, unique_digits


def _lookup_user_by_phone(phone: str, exclude_id: str | None = None) -> "User | None":
    """Find a KIS user by phone regardless of storage format."""
    phone_variants, digit_variants = _phone_variants(phone)
    qs = User.objects.filter(
        Q(phone__in=phone_variants) | Q(phone_number__in=digit_variants)
    )
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    return qs.first()


def _resolve_peer_user(request_user: User, raw_data: dict) -> User:
    """
    Resolves the peer user for a direct chat.
    Priority:
      1) peer_user_id (if provided)
      2) first phone number in participants payload
    """
    peer_user_id = raw_data.get("peer_user_id")
    if peer_user_id is not None:
        peer_id = str(peer_user_id).strip()
        if peer_id == str(request_user.id):
            raise ValidationError({"peer_user_id": "Cannot create a direct chat with yourself."})

        try:
            return User.objects.get(id=peer_id)
        except (User.DoesNotExist, ValueError, TypeError):
            raise ValidationError({"peer_user_id": "Peer user does not exist."})

    phones = _extract_phone_participants(raw_data)
    if not phones:
        raise ValidationError(
            "Either 'peer_user_id' or at least one participant phone number is required."
        )

    # For direct chat, use the first phone only
    first_phone = phones[0]
    peer = _lookup_user_by_phone(first_phone, exclude_id=str(request_user.id))
    if not peer:
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

    def get_throttles(self):
        action = getattr(self, "action", None)
        if action in {"search", "participant_search"}:
            self.throttle_scope = "search"
        elif action in {"create", "direct", "accept_request", "reject_request"}:
            self.throttle_scope = "messaging"
        else:
            self.throttle_scope = None
        return super().get_throttles()

    # ------------------------------------------------------------------
    # Query / serializers
    # ------------------------------------------------------------------
    def get_queryset(self):
        user = self.request.user
        qs = (
            Conversation.objects
            .filter(
                memberships__user=user,
                memberships__left_at__isnull=True,
                memberships__is_hidden=False,
            )
            .distinct()
            .select_related('created_by', 'request_initiator', 'request_recipient')
            .select_related('community_main', 'community_posts')
            .prefetch_related('memberships__user', 'memberships')  # memberships itself too
        )
        if self.action == "list":
            qs = qs.exclude(type=ConversationType.POST)
            qs = qs.exclude(type=ConversationType.THREAD)
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
                | Q(last_message_preview__icontains=q)
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

    def _current_member_for_action(self, request, conversation):
        member = ConversationMember.objects.filter(
            conversation=conversation,
            user=request.user,
            left_at__isnull=True,
        ).first()
        if not member:
            raise PermissionDenied("You are not a member of this conversation.")
        return member

    @action(detail=True, methods=['post'], url_path='pin')
    def pin(self, request, pk=None):
        conversation = self.get_object()
        member = self._current_member_for_action(request, conversation)
        pinned = bool(request.data.get("pinned", True))
        member.is_pinned = pinned
        member.save(update_fields=["is_pinned"])
        return Response({
            "ok": True,
            "conversation_id": str(conversation.id),
            "is_pinned": member.is_pinned,
        })

    @action(detail=True, methods=['post'], url_path='mute')
    def mute(self, request, pk=None):
        conversation = self.get_object()
        member = self._current_member_for_action(request, conversation)
        muted = bool(request.data.get("muted", True))
        member.is_muted = muted
        member.notification_level = (
            "none" if muted else ConversationNotificationLevel.ALL
        )
        member.save(update_fields=["is_muted", "notification_level"])
        return Response({
            "ok": True,
            "conversation_id": str(conversation.id),
            "is_muted": member.is_muted,
            "notification_level": member.notification_level,
        })

    @action(detail=True, methods=['post'], url_path='delete-for-me')
    def delete_for_me(self, request, pk=None):
        conversation = self.get_object()
        member = self._current_member_for_action(request, conversation)
        member.is_hidden = True
        member.is_pinned = False
        member.save(update_fields=["is_hidden", "is_pinned"])
        return Response({
            "ok": True,
            "conversation_id": str(conversation.id),
            "is_hidden": member.is_hidden,
        })

    @action(detail=True, methods=['post'], url_path='mark-read')
    def mark_read(self, request, pk=None):
        conversation = self.get_object()
        member = self._current_member_for_action(request, conversation)
        member.last_read_seq = max(int(conversation.last_message_seq or 0), 0)
        member.last_read_at = timezone.now()
        member.save(update_fields=["last_read_seq", "last_read_at"])
        notify_main_tab_badges_updated(
            [str(request.user.id)],
            source="messages",
            reason="mark_read",
            extra={"conversation_id": str(conversation.id)},
        )
        return Response({
            "ok": True,
            "conversation_id": str(conversation.id),
            "last_read_seq": int(member.last_read_seq or 0),
            "last_read_at": member.last_read_at.isoformat() if member.last_read_at else None,
            "unread_count": 0,
        })

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
        update_fields = ['last_message_at', 'last_message_preview']
        if conversation.type == ConversationType.DIRECT and conversation.is_locked:
            conversation.is_locked = False
            conversation.locked_by = None
            update_fields.extend(['is_locked', 'locked_by'])
        conversation.save(update_fields=update_fields)

        if conversation.type == ConversationType.DIRECT:
            ConversationMember.objects.filter(
                conversation=conversation,
                left_at__isnull=True,
                is_hidden=True,
            ).update(is_hidden=False)

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
        notify_main_tab_badges_updated(
            [str(user_id)],
            source="messages",
            reason="read_state_updated",
            extra={"conversation_id": str(conversation.id), "last_read_seq": int(member.last_read_seq or 0)},
        )

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
            # Log for any active conversation type to diagnose missing membership
            all_members = ConversationMember.objects.filter(
                conversation=conversation,
                user_id=user_id,
            ).values_list('left_at', flat=True)
            logger.warning(
                "chat.ws_perms no active member conversation=%s user=%s type=%s "
                "member_rows_with_any_left_at=%s",
                pk, user_id, conversation.type, list(all_members),
            )

            # --- Fallback: check Group / Community membership and auto-repair ---
            repaired_role = None

            if conversation.type == ConversationType.GROUP:
                try:
                    group = Group.objects.get(conversation=conversation)
                except Group.DoesNotExist:
                    group = None
                if group:
                    if str(group.owner_id) == str(user_id):
                        repaired_role = BaseConversationRole.OWNER
                    else:
                        gm = GroupMembership.objects.filter(
                            group=group, user_id=user_id,
                            left_at__isnull=True, is_banned=False,
                        ).first()
                        if gm:
                            repaired_role = (
                                BaseConversationRole.ADMIN
                                if gm.role in (GroupRole.OWNER, GroupRole.ADMIN, GroupRole.MOD)
                                else BaseConversationRole.MEMBER
                            )
                # Fallback: conversation.created_by is always the owner
                if repaired_role is None and str(getattr(conversation, 'created_by_id', None) or '') == str(user_id):
                    repaired_role = BaseConversationRole.OWNER

            elif conversation.type in (ConversationType.CHANNEL, ConversationType.DIRECT):
                # For channels: created_by is an owner; allow them in
                if str(getattr(conversation, 'created_by_id', None) or '') == str(user_id):
                    repaired_role = BaseConversationRole.OWNER

            elif conversation.type in (ConversationType.POST, ConversationType.THREAD):
                # Feed comment rooms and thread rooms use OPEN join policy — auto-admit anyone
                settings_obj = ConversationSettings.objects.filter(conversation=conversation).first()
                if settings_obj and settings_obj.join_policy == ConversationJoinPolicy.OPEN:
                    repaired_role = BaseConversationRole.MEMBER
                elif str(getattr(conversation, 'created_by_id', None) or '') == str(user_id):
                    repaired_role = BaseConversationRole.OWNER
                else:
                    # POST rooms are public — auto-admit even without explicit settings
                    repaired_role = BaseConversationRole.MEMBER

            # Community main/posts conversations: check CommunityMembership
            if repaired_role is None:
                cm = CommunityMembership.objects.filter(
                    user_id=user_id,
                    left_at__isnull=True,
                    is_banned=False,
                    community__in=Community.objects.filter(
                        Q(main_conversation=conversation) | Q(posts_conversation=conversation)
                    ),
                ).first()
                if cm:
                    repaired_role = (
                        BaseConversationRole.ADMIN
                        if cm.role in (CommunityRole.OWNER, CommunityRole.ADMIN, CommunityRole.MOD)
                        else BaseConversationRole.MEMBER
                    )

            if repaired_role is None:
                logger.warning(
                    "chat.ws_perms access denied conversation=%s user=%s type=%s created_by=%s",
                    pk, user_id, conversation.type,
                    getattr(conversation, 'created_by_id', None),
                )
                return Response({"isMember": False, "isBlocked": False, "role": "member", "scopes": []})

            # Auto-repair: recreate the missing ConversationMember row
            member, _ = ConversationMember.objects.get_or_create(
                conversation=conversation,
                user_id=user_id,
                defaults={"base_role": repaired_role},
            )
            if member.left_at is not None:
                member.left_at = None
                member.base_role = repaired_role
                member.save(update_fields=["left_at", "base_role"])
            logger.warning(
                "chat.ws_perms auto-repaired missing ConversationMember "
                "conversation=%s user=%s role=%s",
                conversation.id, user_id, repaired_role,
            )

        if member.is_blocked:
            return Response({"isMember": True, "isBlocked": True, "role": member.base_role, "scopes": []})

        can_send = True
        settings = ConversationSettings.objects.filter(conversation=conversation).first()
        if (
            conversation.type != ConversationType.DIRECT
            and conversation.is_locked
            and member.base_role not in (BaseConversationRole.OWNER, BaseConversationRole.ADMIN)
        ):
            can_send = False
        if member.base_role == BaseConversationRole.READONLY:
            can_send = False
        if settings and settings.send_policy == ConversationSendPolicy.ADMINS_ONLY:
            if member.base_role not in (BaseConversationRole.OWNER, BaseConversationRole.ADMIN):
                can_send = False

        scopes = []
        if member.base_role in (BaseConversationRole.OWNER, BaseConversationRole.ADMIN):
            scopes.append("chat:admin")
        if (
            conversation.type == ConversationType.DIRECT
            and conversation.request_state == ConversationRequestState.PENDING
            and conversation.request_recipient_id == member.user_id
        ):
            scopes.append("chat:direct_pending_reply")

        logger.info(
            "chat.ws_perms decision conversation=%s user=%s type=%s request_state=%s "
            "request_recipient=%s is_locked=%s member_role=%s member_blocked=%s "
            "send_policy=%s can_send=%s scopes=%s",
            conversation.id,
            user_id,
            conversation.type,
            conversation.request_state,
            conversation.request_recipient_id,
            conversation.is_locked,
            member.base_role,
            member.is_blocked,
            getattr(settings, "send_policy", None),
            can_send,
            scopes,
        )

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
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    queryset = MessageThreadLink.objects.all()
    serializer_class = MessageThreadLinkSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = (
            MessageThreadLink.objects
            .select_related("parent_conversation", "child_conversation", "created_by")
            .filter(
                Q(parent_conversation__memberships__user=user, parent_conversation__memberships__left_at__isnull=True)
                | Q(child_conversation__memberships__user=user, child_conversation__memberships__left_at__isnull=True)
            )
            .distinct()
            .order_by("-created_at")
        )
        parent_conversation = self.request.query_params.get("parent_conversation")
        if parent_conversation:
            qs = qs.filter(parent_conversation_id=parent_conversation)
        return qs

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        parent_id = serializer.validated_data["parent_conversation"].id
        parent = Conversation.objects.select_for_update(of=("self",)).get(id=parent_id)
        if not user_is_active_member(request.user, parent):
            raise PermissionDenied("Not a member")

        settings_obj = getattr(parent, "settings", None)
        member = ConversationMember.objects.filter(
            conversation=parent,
            user=request.user,
            left_at__isnull=True,
        ).first()
        if (
            settings_obj
            and settings_obj.subroom_policy == "admins_only"
            and (not member or member.base_role not in (BaseConversationRole.OWNER, BaseConversationRole.ADMIN))
        ):
            raise PermissionDenied("Only admins can create sub-rooms in this conversation.")

        parent_thread = serializer.validated_data.get("parent_thread")
        depth = (parent_thread.depth + 1) if parent_thread else 1
        max_depth = getattr(settings_obj, "max_subroom_depth", 8) if settings_obj else 8
        if max_depth and depth > max_depth:
            raise ValidationError({"parent_thread": "Maximum sub-room depth reached."})

        parent_message_key = serializer.validated_data["parent_message_key"]
        existing = MessageThreadLink.objects.filter(
            parent_conversation=parent,
            parent_message_key=parent_message_key,
        ).select_related("child_conversation").first()
        if existing:
            return Response(self.get_serializer(existing).data, status=status.HTTP_200_OK)

        raw_title = str(request.data.get("title") or "").strip()
        title = raw_title[:255] or f"Sub-room for message {str(parent_message_key)[:8]}"

        child = Conversation.objects.create(
            type=ConversationType.THREAD,
            title=title,
            description=f"Sub-room from message {parent_message_key}",
            created_by=request.user,
        )
        ConversationSettings.objects.create(conversation=child)
        parent_members = ConversationMember.objects.filter(
            conversation=parent,
            left_at__isnull=True,
        ).select_related("user")
        ConversationMember.objects.bulk_create(
            [
                ConversationMember(
                    conversation=child,
                    user=row.user,
                    base_role=row.base_role,
                    display_name=row.display_name,
                    notification_level=row.notification_level,
                    color=row.color,
                    is_muted=row.is_muted,
                    is_blocked=row.is_blocked,
                )
                for row in parent_members
            ],
            ignore_conflicts=True,
        )

        try:
            link = MessageThreadLink.objects.create(
                parent_conversation=parent,
                parent_message_key=parent_message_key,
                child_conversation=child,
                parent_thread=parent_thread,
                depth=depth,
                created_by=request.user,
            )
        except IntegrityError:
            child.delete()
            link = MessageThreadLink.objects.select_related("child_conversation").get(
                parent_conversation=parent,
                parent_message_key=parent_message_key,
            )
            return Response(self.get_serializer(link).data, status=status.HTTP_200_OK)

        return Response(self.get_serializer(link).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Sticker Packs
# ---------------------------------------------------------------------------

_BUILTIN_STICKER_PACKS = [
    {
        "id": "default",
        "name": "Default",
        "stickers": [
            {"id": "default_thumbsup",   "url": "", "text": "👍"},
            {"id": "default_heart",      "url": "", "text": "❤️"},
            {"id": "default_laugh",      "url": "", "text": "😂"},
            {"id": "default_wow",        "url": "", "text": "😮"},
            {"id": "default_sad",        "url": "", "text": "😢"},
            {"id": "default_pray",       "url": "", "text": "🙏"},
            {"id": "default_fire",       "url": "", "text": "🔥"},
            {"id": "default_clap",       "url": "", "text": "👏"},
            {"id": "default_celebrate",  "url": "", "text": "🎉"},
            {"id": "default_amen",       "url": "", "text": "🙌"},
        ],
    },
    {
        "id": "expressions",
        "name": "Expressions",
        "stickers": [
            {"id": "expr_thinking",  "url": "", "text": "🤔"},
            {"id": "expr_wink",      "url": "", "text": "😉"},
            {"id": "expr_sunglasses","url": "", "text": "😎"},
            {"id": "expr_angry",     "url": "", "text": "😠"},
            {"id": "expr_shocked",   "url": "", "text": "😱"},
            {"id": "expr_love",      "url": "", "text": "🥰"},
        ],
    },
]


class StickerPackListView(APIView):
    """
    GET /api/v1/stickers/packs/
    Returns the list of available sticker packs (built-in).
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_BUILTIN_STICKER_PACKS, status=status.HTTP_200_OK)
