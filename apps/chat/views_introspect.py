import os
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.exceptions import AuthenticationFailed
from django.db.models import Q
from apps.accounts.jwt_auth import DeviceBoundJWTAuthentication, validate_device_bound_token
from apps.accounts.tiers import get_aggregated_tier_features, get_user_tier, is_paid_tier_name
from apps.moderation.models import ChatMessageReport, UserBlock

from .internal_auth import require_internal_auth


class IntrospectView(APIView):
    """
    Authoritative token-validation endpoint for Nest.js (chat, calls, push
    notifications) — the single place external services confirm a bearer
    token is still valid. Must enforce EXACTLY the same device-bound
    revocation policy as normal Django REST auth: previously this only
    checked that the token carried a device_id claim, never whether a live,
    non-revoked Device row still backed it, so revoking a device (logout,
    single-device revoke, bulk revoke) had no effect on Nest.js access until
    the token's own expiry. Now routed through the same
    validate_device_bound_token() DeviceBoundJWTAuthentication itself uses.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        require_internal_auth(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header:
            raise AuthenticationFailed("Missing token")

        scheme = os.environ.get("DJANGO_AUTH_SCHEME", "Bearer").strip()
        prefix = f"{scheme} "
        if not auth_header.startswith(prefix):
            raise AuthenticationFailed("Invalid auth scheme")

        token = auth_header[len(prefix):].strip()
        if not token:
            raise AuthenticationFailed("Missing token")

        jwt_auth = DeviceBoundJWTAuthentication()
        validated = jwt_auth.get_validated_token(token)
        user = jwt_auth.get_user(validated)
        if not user or not user.is_active:
            raise AuthenticationFailed("Invalid token")

        # X-Device-Id is optional here (Nest is relaying a client's token,
        # not originating the request) but cross-checked when present.
        header_device_id = (
            request.headers.get("X-Device-Id")
            or request.headers.get("X-Device-ID")
            or request.headers.get("X-DeviceId")
        )
        validate_device_bound_token(
            user, validated, header_device_id=header_device_id, require_header=False,
        )

        username = getattr(user, "username", "") or ""
        email = getattr(user, "email", "") or ""
        display_name = (getattr(user, "display_name", "") or "").strip()
        if hasattr(user, "get_full_name"):
            display_name = display_name or user.get_full_name() or ""
        if not display_name:
            display_name = username or (email.split("@")[0] if email else "")

        # Resolves via the same canonical path as the rest of the app
        # (active Subscription first, falling back to the denormalized
        # User.tier string with alias normalization) — previously this did
        # its own ad-hoc extraction and compared against the stale string
        # "basic" (the free tier was renamed to "Free" well before this
        # endpoint existed), so every free-tier user was reported as
        # isPremium: true to Nest.js. entitlements was also always a
        # hardcoded {}, so Nest.js never had real per-tier feature data to
        # act on even though the plumbing for it existed.
        tier_obj = get_user_tier(user)
        tier_name = tier_obj.name if tier_obj else (getattr(user, "tier", "") or "")
        is_premium = is_paid_tier_name(tier_name)
        entitlements = get_aggregated_tier_features(tier_obj) if tier_obj else {}

        return Response({
            "id": str(user.id),
            "username": username,
            "email": email,
            "display_name": display_name,
            "tier": tier_name,
            "isPremium": is_premium,
            "device_id": validated.get("device_id"),
            "entitlements": entitlements,
            "scopes": [],
        })


class UserBlockCheckView(APIView):
    """
    GET /api/v1/chat/internal/blocked-among/?userId=X&otherUserIds=a,b,c

    Standalone calls (`standalone:<callId>`) have no Django conversation
    record, so they skip assertMember() entirely and previously had no
    authorization check of any kind - any authenticated user could ring
    any other user, including one who had blocked them, via NestJS's
    call.offer socket handler or POST /calls/standalone (both in
    /Users/nigel/dev/backend/Nestjs/src/chat/features/calls/). Real
    conversations get their block check from ws_perms(); this is the
    equivalent for the invitee list of a call that has no conversation to
    check membership against.

    Returns the subset of otherUserIds that have ANY UserBlock relationship
    (either direction) with userId, so the caller can filter/reject them
    before ringing.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        require_internal_auth(request)

        user_id = (request.query_params.get("userId") or "").strip()
        other_ids_raw = request.query_params.get("otherUserIds") or ""
        other_ids = [uid.strip() for uid in other_ids_raw.split(",") if uid.strip()]

        if not user_id or not other_ids:
            return Response({"blockedUserIds": []})

        blocked_pairs = UserBlock.objects.filter(
            Q(blocker_id=user_id, blocked_id__in=other_ids)
            | Q(blocked_id=user_id, blocker_id__in=other_ids)
        ).values_list("blocker_id", "blocked_id")

        blocked_user_ids = set()
        for blocker_id, blocked_id in blocked_pairs:
            other = str(blocked_id) if str(blocker_id) == user_id else str(blocker_id)
            blocked_user_ids.add(other)

        return Response({"blockedUserIds": sorted(blocked_user_ids)})


class ChatMessageReportView(APIView):
    """
    POST /api/v1/chat/internal/message-reports/
    Body: {"conversationId", "messageId", "reportedBy", "reason", "note"}

    Called by Nest's ModerationController.report() right after it writes
    its own local Mongo MessageReport. Chat messages live entirely in
    Nest's Mongo, so this can't reuse the Flag model that every other
    report type uses (Flag.target_id is a strict UUIDField; a Mongo
    ObjectId isn't a valid UUID) - see ChatMessageReport's docstring.
    Without this call, a chat message report only ever existed in Nest's
    Mongo collection with no admin surface anywhere in Django, so a GO/
    staff moderator reviewing the unified queue could never see it.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        require_internal_auth(request)

        conversation_id = str(request.data.get("conversationId") or "").strip()
        message_id = str(request.data.get("messageId") or "").strip()
        reported_by = str(request.data.get("reportedBy") or "").strip()
        if not conversation_id or not message_id or not reported_by:
            return Response(
                {"detail": "conversationId, messageId, and reportedBy are required."},
                status=400,
            )

        report, _created = ChatMessageReport.objects.get_or_create(
            conversation_id=conversation_id,
            message_id=message_id,
            reported_by_id=reported_by,
            defaults={
                "reason": str(request.data.get("reason") or "")[:64],
                "note": str(request.data.get("note") or "")[:4000],
            },
        )
        return Response({"ok": True, "id": str(report.id)})
