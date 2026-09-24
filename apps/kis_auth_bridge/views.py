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
import secrets

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.jwt_auth import DeviceBoundJWTAuthentication
from apps.accounts.models import AuditLog, Device, User
from apps.accounts.security_events import log_security_event
from apps.accounts.serializers import UserCreateSerializer
from apps.accounts.views import issue_tokens_for_user, revoke_device_session
from apps.chat.internal_signing import verify_internal_request
from apps.partners.models import Partner, PartnerIntegration

from .exchange_client import (
    ExchangeError,
    redeem_authorization_code,
    redeem_registration_ticket,
    link_identity_server_to_server,
)
from .link_ticket import LinkTicketError, mint_link_ticket

logger = logging.getLogger("security.kis_auth_bridge")

GENERIC_ERROR = "We could not complete this authentication request."
CLIENT_ID = "kis-django"

_JWT_AUTH = (DeviceBoundJWTAuthentication,)

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
    "link.succeeded",
    "link.failed",
    "link.already_linked",
    "registration.ticket_issued",
    "registration.already_registered",
    "registration.exchange_succeeded",
    "registration.exchange_failed",
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
        if not (settings.KIS_AUTH_ENABLED and settings.KIS_AUTH_RECOVERY_ENABLED):
            # Rollout gate, not a security boundary — a valid authorization
            # code can only exist because the Google round trip already
            # succeeded. This just lets recovery be turned off independent
            # of registration/link without touching kis-auth itself.
            return Response({"detail": GENERIC_ERROR}, status=status.HTTP_404_NOT_FOUND)

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


class KisAuthLinkInitiateView(APIView):
    """
    POST api/v1/kis-auth/link/initiate/
    Authenticated — the whole point of this endpoint is proving WHICH
    already-logged-in KIS user is starting the link, something an
    unauthenticated caller must never be trusted to supply directly (see
    kis-auth's LinkTicketService docstring for the attack this prevents).
    """

    permission_classes = [IsAuthenticated]
    authentication_classes = _JWT_AUTH

    def post(self, request):
        if not (settings.KIS_AUTH_ENABLED and settings.KIS_AUTH_LINK_ENABLED):
            return Response({"detail": GENERIC_ERROR}, status=status.HTTP_404_NOT_FOUND)

        try:
            ticket = mint_link_ticket(str(request.user.id))
        except LinkTicketError:
            logger.exception("kis_auth_bridge.link_initiate.mint_failed")
            return Response({"detail": GENERIC_ERROR}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        return Response({"link_ticket": ticket, "expires_in": 300})


class KisAuthLinkCompleteView(APIView):
    """
    POST api/v1/kis-auth/link/complete/
    Authenticated. Body: { code, redirect_uri }
    Redeems the code through the SAME exchange endpoint recovery uses (a
    link-purpose code has the identical kis_user_id + auth_identity_id
    shape) — the one extra check here, beyond what recovery does, is that
    the redeemed identity's kis_user_id matches the CALLER, not just that
    the code was valid. Without that check, a stolen/leaked link-result
    code could be redeemed by anyone who happened to be logged in as
    someone else when they got hold of it.
    """

    permission_classes = [IsAuthenticated]
    authentication_classes = _JWT_AUTH

    def post(self, request):
        if not (settings.KIS_AUTH_ENABLED and settings.KIS_AUTH_LINK_ENABLED):
            return Response({"detail": GENERIC_ERROR}, status=status.HTTP_404_NOT_FOUND)

        code = str(request.data.get("code") or "").strip()
        redirect_uri = str(request.data.get("redirect_uri") or "").strip()
        if not code or not redirect_uri:
            return Response({"detail": GENERIC_ERROR}, status=status.HTTP_400_BAD_REQUEST)

        try:
            verified = redeem_authorization_code(
                code=code, client_id=CLIENT_ID, redirect_uri=redirect_uri
            )
        except ExchangeError:
            logger.info("kis_auth_bridge.link_complete.exchange_failed")
            return Response({"detail": GENERIC_ERROR}, status=status.HTTP_400_BAD_REQUEST)

        if verified.purpose != "link":
            logger.warning(
                "kis_auth_bridge.link_complete.wrong_purpose",
                extra={"purpose": verified.purpose},
            )
            return Response({"detail": GENERIC_ERROR}, status=status.HTTP_400_BAD_REQUEST)

        if verified.kis_user_id != str(request.user.id):
            logger.warning("kis_auth_bridge.link_complete.user_mismatch")
            return Response({"detail": GENERIC_ERROR}, status=status.HTTP_400_BAD_REQUEST)

        AuditLog.log(
            actor=request.user,
            action="account.google_identity_linked",
            meta={
                "auth_identity_id": verified.auth_identity_id,
                "provider_email_verified": verified.provider_email_verified,
            },
        )

        return Response({"ok": True, "linked": True})


class KisAuthRegistrationCompleteView(APIView):
    """
    POST api/v1/kis-auth/registration/complete/
    No auth required — same posture as recovery/complete: the registration
    ticket IS the proof (it could only exist because kis-auth's OAuth
    callback verified a real Google sign-in moments earlier). Body:
    { registration_code, redirect_uri, phone, phone_country_code,
      phone_number, country?, display_name?, date_of_birth?, referral_code?,
      device_id, device_name?, platform? }

    Reuses UserCreateSerializer as-is for every phone/age/country
    validation rule an ordinary signup already enforces — this is
    deliberately NOT a second, parallel account-creation implementation.
    The only divergence: password is a random value the user can never
    know (immediately marked unusable) since Google is the actual
    credential, and the account is linked to the Google identity in the
    same transaction as creation.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "password_reset"

    def post(self, request):
        if not (settings.KIS_AUTH_ENABLED and settings.KIS_AUTH_REGISTRATION_ENABLED):
            return Response({"detail": GENERIC_ERROR}, status=status.HTTP_404_NOT_FOUND)

        code = str(request.data.get("registration_code") or "").strip()
        redirect_uri = str(request.data.get("redirect_uri") or "").strip()
        device_id = str(request.data.get("device_id") or "").strip()
        device_name = (str(request.data.get("device_name") or "").strip()) or None
        platform = str(request.data.get("platform") or "unknown").strip()
        referral_code = str(request.data.get("referral_code") or "").strip()

        if not code or not redirect_uri or not device_id:
            return Response({"detail": GENERIC_ERROR}, status=status.HTTP_400_BAD_REQUEST)

        try:
            verified = redeem_registration_ticket(
                code=code, client_id=CLIENT_ID, redirect_uri=redirect_uri
            )
        except ExchangeError:
            logger.info("kis_auth_bridge.registration.exchange_failed")
            return Response({"detail": GENERIC_ERROR}, status=status.HTTP_400_BAD_REQUEST)

        # The DB-level unique constraint on User.email would catch this too
        # (as an IntegrityError, below), but that path can't tell the caller
        # anything actionable. Check up front so someone whose Google email
        # already belongs to a password-registered account gets pointed at
        # logging in + linking Google from Settings instead of a dead-end
        # "please retry" that will fail identically every time.
        if (
            verified.provider_email
            and verified.provider_email_verified
            and User.objects.filter(email__iexact=verified.provider_email).exists()
        ):
            return Response(
                {
                    "detail": "An account already exists for this email. Log in and link Google from Settings instead.",
                    "code": "account_already_exists",
                },
                status=status.HTTP_409_CONFLICT,
            )

        is_enterprise_sso = verified.purpose == "enterprise_sso_registration"

        if is_enterprise_sso:
            # No phone exists for an IdP-federated account - synthesize a
            # unique placeholder instead of reading one from the request.
            # "999" is unassigned by the ITU, so it can't collide with (or
            # be mistaken for) a real calling code. Global uniqueness of
            # `phone` is still enforced by the DB constraint; a collision
            # here just retries via the existing IntegrityError handling
            # below, same as a Google email collision would.
            placeholder_number = f"{secrets.randbelow(10**9):09d}"
            serializer_data = {
                "password": secrets.token_urlsafe(32),
                "password2": None,
                "display_name": request.data.get("display_name", ""),
                "phone": "",
                "phone_country_code": "999",
                "phone_number": placeholder_number,
                "country": request.data.get("country", ""),
                "date_of_birth": request.data.get("date_of_birth"),
            }
        else:
            serializer_data = {
                "password": secrets.token_urlsafe(32),
                "password2": None,
                "display_name": request.data.get("display_name", ""),
                "phone": request.data.get("phone", ""),
                "phone_country_code": request.data.get("phone_country_code", ""),
                "phone_number": request.data.get("phone_number", ""),
                "country": request.data.get("country", ""),
                "date_of_birth": request.data.get("date_of_birth"),
            }
        serializer_data["password2"] = serializer_data["password"]

        serializer = UserCreateSerializer(data=serializer_data)
        if not serializer.is_valid():
            return Response(
                {"detail": GENERIC_ERROR, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                user = serializer.save()

                if referral_code:
                    from apps.referrals.services import register_referral

                    register_referral(
                        referred_user=user, referral_code=referral_code, device_id=device_id
                    )

                # Attempt the link FIRST, before any further writes to this
                # user — a conflict here (e.g. a race between two
                # registration attempts for the same Google identity, each
                # getting their own valid ticket before either redeemed)
                # must roll back the account cleanly, not leave an
                # unlinkable orphan or a half-set email behind.
                try:
                    link_identity_server_to_server(
                        kis_user_id=str(user.id),
                        provider_subject=verified.provider_subject,
                        provider_email=verified.provider_email,
                        provider_email_verified=verified.provider_email_verified,
                        provider=verified.provider,
                    )
                except ExchangeError:
                    logger.exception("kis_auth_bridge.registration.link_failed")
                    transaction.set_rollback(True)
                    return Response({"detail": GENERIC_ERROR}, status=status.HTTP_400_BAD_REQUEST)

                # The IdP (Google, or an enterprise OIDC provider) is the
                # actual credential — this account should never be
                # reachable through the password-login path.
                user.set_unusable_password()
                if verified.provider_email and verified.provider_email_verified:
                    user.email = verified.provider_email
                    user.email_verified = True
                if is_enterprise_sso:
                    user.phone_is_placeholder = True
                user.save(update_fields=["password", "email", "email_verified", "phone_is_placeholder"])

                now = timezone.now()
                Device.objects.update_or_create(
                    user=user,
                    device_id=device_id,
                    defaults={
                        "platform": platform,
                        "name": device_name,
                        "last_seen_at": now,
                        "last_ip": request.META.get("REMOTE_ADDR"),
                        "user_agent": request.META.get("HTTP_USER_AGENT"),
                        "token_version": 1,
                        "revoked_at": None,
                        "revoke_reason": "",
                        "is_parent": True,
                        "linked_via_qr": False,
                        "trusted_until": now + datetime.timedelta(days=30),
                        "parent_device": None,
                    },
                )
        except IntegrityError:
            # e.g. provider_email collides with an unrelated existing
            # account's email — fail the whole registration cleanly rather
            # than a raw 500.
            return Response(
                {"detail": "Registration could not complete - please retry."},
                status=status.HTTP_409_CONFLICT,
            )

        tokens = issue_tokens_for_user(user, device_id=device_id)

        AuditLog.log(
            actor=user,
            action="account.kis_auth_registration",
            meta={
                "provider_email_verified": verified.provider_email_verified,
                "provider": verified.provider,
                "partner_slug": verified.partner_slug,
            },
        )

        # Same welcome notification as apps.accounts.views' password-based
        # registration - the successful Google hand-off is itself the
        # "you're in" moment, so this is in-app/push, never email.
        try:
            from apps.notifications.services import create_notification
            create_notification(
                user_id=user.id,
                type="ACCOUNT_WELCOME",
                title="Welcome to KIS",
                body="Your account is ready. Start exploring today.",
                priority="LOW",
                dedup_key=f"account_welcome:{user.id}",
            )
        except Exception:
            logger.warning("Welcome notification failed for user_id=%s", user.id)

        return Response(
            {
                "access": tokens["access"],
                "refresh": tokens["refresh"],
                "token_type": "Bearer",
                "user": {
                    "id": user.id,
                    "phone": None if is_enterprise_sso else getattr(user, "phone", None),
                    "phone_is_placeholder": is_enterprise_sso,
                    "status": getattr(user, "status", "active"),
                },
            },
            status=status.HTTP_201_CREATED,
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


class KisAuthSsoConfigView(APIView):
    """
    GET api/v1/kis-auth/sso-config/?partner_slug=<slug>

    Server-to-server only, same posture as KisAuthSecurityEventView:
    verified via verify_internal_request() against
    KISAUTH_INTERNAL_HMAC_SECRET, fail-closed if that secret isn't
    configured. This is the ONLY place a PartnerIntegration's OIDC
    client_secret is ever returned un-redacted — every client-facing path
    (PartnerIntegrationSerializer) redacts it. kis-auth uses this to
    resolve which IdP to send a user to for /oauth/enterprise/<slug>/start
    and to verify the ID token it gets back.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        secret = os.environ.get("KISAUTH_INTERNAL_HMAC_SECRET", "").strip()
        if not secret:
            return Response({"detail": "not configured"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        signed, reason = verify_internal_request(request, secret)
        if not signed:
            logger.warning("kis_auth_bridge.sso_config.signature_invalid", extra={"reason": reason})
            return Response({"detail": "invalid signature"}, status=status.HTTP_401_UNAUTHORIZED)

        partner_slug = str(request.query_params.get("partner_slug") or "").strip()
        if not partner_slug:
            return Response({"detail": "partner_slug is required"}, status=status.HTTP_400_BAD_REQUEST)

        partner = Partner.objects.filter(slug=partner_slug).first()
        if not partner:
            return Response({"detail": "unknown partner"}, status=status.HTTP_404_NOT_FOUND)

        integration = PartnerIntegration.objects.filter(
            partner=partner, kind=PartnerIntegration.KIND_SSO, is_enabled=True
        ).first()
        if not integration:
            return Response({"detail": "sso not configured for this partner"}, status=status.HTTP_404_NOT_FOUND)

        config = integration.config or {}
        required = ("issuer", "client_id", "client_secret")
        if any(not config.get(field) for field in required):
            logger.error(
                "kis_auth_bridge.sso_config.incomplete", extra={"partner_slug": partner_slug}
            )
            return Response({"detail": "sso misconfigured for this partner"}, status=status.HTTP_409_CONFLICT)

        return Response(
            {
                "partner_id": str(partner.id),
                "partner_slug": partner.slug,
                "provider": integration.provider or "oidc",
                "issuer": config.get("issuer"),
                "client_id": config.get("client_id"),
                "client_secret": config.get("client_secret"),
                "discovery_url": config.get("discovery_url") or None,
            },
            status=status.HTTP_200_OK,
        )
