"""KIS Auth bridge — the one place Django accepts a KIS-Auth-verified
identity and turns it into an actual KIS account operation.

KIS Auth proves identity. This view is what proves authorization: it
re-checks the user exists and is active, checks the JWT's purpose claim
against the specific operation being performed, and only then executes
the SAME device-promotion + token-issuance transaction that
apps.accounts.views.ParentRecoveryConfirmView already uses for the
legacy (email/phone OTP) recovery path — reused via import, not
reimplemented, so both paths stay behaviorally identical except for how
the caller proved they're allowed to recover this account.
"""
from __future__ import annotations

import datetime
import logging
import os

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import AuditLog, Device, User
from apps.accounts.security_events import log_security_event
from apps.accounts.views import issue_tokens_for_user, revoke_device_session
from apps.chat.internal_signing import verify_internal_request

from .exchange_client import ExchangeError, redeem_authorization_code

logger = logging.getLogger("security.kis_auth_bridge")

GENERIC_ERROR = "We could not complete this authentication request."
CLIENT_ID = "kis-django"

# Known-safe event types only — an unrecognized value is logged with a
# generic action name rather than trusting arbitrary caller-supplied text
# into the audit log's action field.
_ALLOWED_EVENT_TYPES = {
    "oauth.callback_succeeded",
    "oauth.callback_failed",
    "oauth.identity_not_linked",
    "oauth.cancelled",
    "exchange.succeeded",
    "exchange.failed",
}


class KisAuthRecoveryCompleteView(APIView):
    """
    POST api/v1/kis-auth/recovery/complete/
    No auth required — the KIS Auth authorization code IS the proof.
    Body: { authorization_code, redirect_uri, device_id, device_name?, platform? }
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "password_reset"  # same scope as the legacy recovery confirm endpoint

    def post(self, request):
        code = str(request.data.get("authorization_code") or "").strip()
        redirect_uri = str(request.data.get("redirect_uri") or "").strip()
        device_id = str(request.data.get("device_id") or "").strip()
        device_name = (str(request.data.get("device_name") or "").strip()) or None
        platform = (str(request.data.get("platform") or "unknown").strip())

        if not code or not redirect_uri or not device_id:
            return Response({"detail": GENERIC_ERROR}, status=status.HTTP_400_BAD_REQUEST)

        try:
            verified = redeem_authorization_code(
                code=code, client_id=CLIENT_ID, redirect_uri=redirect_uri
            )
        except ExchangeError:
            logger.info("kis_auth_bridge.recovery.exchange_failed")
            return Response({"detail": GENERIC_ERROR}, status=status.HTTP_400_BAD_REQUEST)

        # Purpose binding: a code issued for anything other than recovery
        # must not be usable here, by construction — not by convention.
        if verified.purpose != "recovery":
            logger.warning(
                "kis_auth_bridge.recovery.wrong_purpose",
                extra={"purpose": verified.purpose},
            )
            return Response({"detail": GENERIC_ERROR}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = User.objects.get(id=verified.kis_user_id, is_active=True)
        except (User.DoesNotExist, ValueError, TypeError, ValidationError):
            # Same generic message as "invalid recovery token" in the
            # legacy flow — never confirms/denies a specific account exists.
            return Response({"detail": GENERIC_ERROR}, status=status.HTTP_400_BAD_REQUEST)

        now = timezone.now()
        try:
            with transaction.atomic():
                # Identical logic to ParentRecoveryConfirmView (see
                # apps/accounts/views.py) — revoke every other active
                # parent device through the real revocation path (bumps
                # token_version, wipes E2EE keys), then promote this
                # device to parent.
                old_parents = list(
                    Device.objects.select_for_update()
                    .filter(user=user, is_parent=True, revoked_at__isnull=True)
                    .exclude(device_id=str(device_id))
                )
                for old_parent in old_parents:
                    old_parent.is_parent = False
                    old_parent.save(update_fields=["is_parent", "updated_at"])
                    revoke_device_session(user, old_parent, reason="kis_auth_recovery", request=request)

                existing = (
                    Device.objects.select_for_update()
                    .filter(user=user, device_id=str(device_id))
                    .first()
                )
                token_version = (
                    (existing.token_version + 1)
                    if existing and existing.revoked_at
                    else (existing.token_version if existing else 1)
                )
                Device.objects.update_or_create(
                    user=user,
                    device_id=str(device_id),
                    defaults={
                        "platform": platform,
                        "name": device_name,
                        "last_seen_at": now,
                        "last_ip": request.META.get("REMOTE_ADDR"),
                        "user_agent": request.META.get("HTTP_USER_AGENT"),
                        "token_version": token_version,
                        "revoked_at": None,
                        "revoke_reason": "",
                        "is_parent": True,
                        "linked_via_qr": False,
                        "trusted_until": now + datetime.timedelta(days=30),
                        "parent_device": None,
                    },
                )
        except IntegrityError:
            return Response(
                {"detail": "Recovery could not complete - please retry."},
                status=status.HTTP_409_CONFLICT,
            )

        tokens = issue_tokens_for_user(user, device_id=device_id)

        AuditLog.log(
            actor=user,
            action="device.kis_auth_recovery",
            meta={
                "new_parent_device_id": device_id,
                "auth_identity_id": verified.auth_identity_id,
                "provider_email_verified": verified.provider_email_verified,
            },
        )

        return Response(
            {
                "access": tokens["access"],
                "refresh": tokens["refresh"],
                "token_type": "Bearer",
                "user": {
                    "id": user.id,
                    "phone": getattr(user, "phone", None),
                    "status": getattr(user, "status", "active"),
                },
            }
        )


class KisAuthSecurityEventView(APIView):
    """
    POST api/v1/kis-auth/security-event/
    Server-to-server only — HMAC-signed callers (kis-auth) only, verified
    here directly rather than through require_internal_auth (that helper
    is hardcoded to DJANGO_INTERNAL_TOKEN, the Nest<->Django secret; this
    channel uses its own distinct KISAUTH_INTERNAL_HMAC_SECRET).

    Best-effort audit forwarding (Phase 2 §16): lands kis-auth's own
    security events in Django's existing log_security_event() so an
    operator has one place to look, instead of two databases that can
    disagree. This endpoint accepting/rejecting an event has no bearing
    on whether the auth operation it describes succeeded — that decision
    was already made, on kis-auth's side, before this call happened.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        secret = os.environ.get("KISAUTH_INTERNAL_HMAC_SECRET", "").strip()
        if not secret:
            # Not configured — nothing to verify against, so nothing can
            # be trusted. Same posture as internal_signatures_required()
            # in production: fail closed, not open.
            return Response({"detail": "not configured"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        signed, reason = verify_internal_request(request, secret)
        if not signed:
            logger.warning("kis_auth_bridge.security_event.signature_invalid", extra={"reason": reason})
            return Response({"detail": "invalid signature"}, status=status.HTTP_401_UNAUTHORIZED)

        event_type = str(request.data.get("event_type") or "").strip()
        if event_type not in _ALLOWED_EVENT_TYPES:
            event_type = "unknown"
        outcome = str(request.data.get("outcome") or "").strip() or "unknown"
        kis_user_id = request.data.get("kis_user_id")
        client_id = request.data.get("client_id")
        reason_field = request.data.get("reason")
        ip = request.data.get("ip")
        metadata = request.data.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}

        actor = None
        if kis_user_id:
            try:
                actor = User.objects.filter(id=kis_user_id).first()
            except (ValueError, TypeError, ValidationError):
                actor = None

        severity = "warning" if outcome == "failure" else "info"
        log_security_event(
            actor,
            f"security.kis_auth.{event_type}",
            severity=severity,
            outcome=outcome,
            client_id=client_id,
            reason=reason_field,
            source_ip=ip,
            **{f"meta_{k}": v for k, v in list(metadata.items())[:20]},  # bounded — never an unbounded caller-controlled payload
        )

        return Response({"ok": True}, status=status.HTTP_201_CREATED)
