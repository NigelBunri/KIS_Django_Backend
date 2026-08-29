# apps/accounts/webauthn_restore.py
#
# Server side of Android's Restore Credentials feature (Google Play's
# "Zero-Tap Sign-In" quality requirement - device-migration re-auth without
# any user interaction). Restore credentials use the exact same WebAuthn
# ceremony as passkeys under the hood (Google's own docs: "Restore keys and
# passkeys use identical underlying WebAuthn implementation but are
# differentiated in the server database for management purposes"), so this
# uses the standard `webauthn` (py_webauthn) library rather than any
# restore-specific crypto - there isn't any.
#
# Two ceremonies, four endpoints:
#   1. Registration (client already logged in, on its EXISTING device):
#        POST auth/restore-credentials/registration-options/  (authenticated)
#        POST auth/restore-credentials/register/              (authenticated)
#   2. Authentication (client has no session yet, on a NEW device):
#        POST auth/restore-credentials/authentication-options/ (open)
#        POST auth/restore-credentials/authenticate/            (open)
#
# The authentication ceremony is necessarily unauthenticated - the entire
# point is signing a user in with zero taps before any session exists - so
# the challenge issued by authentication-options/ is correlated to the later
# authenticate/ call via an explicit opaque `state` token (cached server-side
# against the challenge bytes) rather than a Django session, since this API
# is otherwise fully stateless/JWT-based.
import base64
import logging
import secrets

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

import webauthn
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)
from webauthn.helpers.exceptions import InvalidRegistrationResponse, InvalidAuthenticationResponse

from .models import Device, RestoreCredential
from .security_events import log_security_event, request_meta
from .views import issue_tokens_for_user, promote_device_via_sim  # noqa: F401 (kept for symmetry/reference)

logger = logging.getLogger(__name__)

_CHALLENGE_CACHE_PREFIX = "restore_cred_challenge:"
_CHALLENGE_TTL_SECONDS = 300  # 5 minutes - generous enough for a slow network without leaving stale state around


def _cache_challenge(prefix: str, challenge_bytes: bytes, extra: dict | None = None) -> str:
    """Stores a challenge under a fresh opaque state token, returns that token."""
    state = secrets.token_urlsafe(24)
    payload = {"challenge": base64.urlsafe_b64encode(challenge_bytes).decode("ascii").rstrip("=")}
    if extra:
        payload.update(extra)
    cache.set(f"{_CHALLENGE_CACHE_PREFIX}{prefix}:{state}", payload, timeout=_CHALLENGE_TTL_SECONDS)
    return state


def _pop_challenge(prefix: str, state: str) -> dict | None:
    key = f"{_CHALLENGE_CACHE_PREFIX}{prefix}:{state}"
    payload = cache.get(key)
    if payload is not None:
        cache.delete(key)  # single-use: a challenge must never be replayable
    return payload


def _b64url_decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded)


class RestoreCredentialRegistrationOptionsView(APIView):
    """
    Called right after a normal, already-authenticated login on the user's
    CURRENT device, before asking Android's CredentialManager.createCredential
    to actually generate the restore keypair. Returns a WebAuthn
    PublicKeyCredentialCreationOptionsJSON for the client to pass straight
    into CreateRestoreCredentialRequest(requestJson=...).
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        existing_ids = list(
            RestoreCredential.objects.filter(user=user, revoked_at__isnull=True).values_list("credential_id", flat=True)
        )
        options = webauthn.generate_registration_options(
            rp_id=settings.WEBAUTHN_RP_ID,
            rp_name=settings.WEBAUTHN_RP_NAME,
            user_id=str(user.id).encode("utf-8"),
            user_name=user.phone or user.username or str(user.id),
            user_display_name=user.display_name or user.phone or "KIS user",
            exclude_credentials=[
                PublicKeyCredentialDescriptor(id=_b64url_decode(cred_id)) for cred_id in existing_ids
            ],
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
        )
        state = _cache_challenge("reg", options.challenge)
        return Response({
            "state": state,
            "options": webauthn.options_to_json(options),
        })


class RestoreCredentialRegisterView(APIView):
    """
    Verifies the CreateRestoreCredentialResponse the client got back from
    CredentialManager.createCredential(), then stores the public key.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        state = request.data.get("state")
        credential_json = request.data.get("credential")
        device_id = (request.data.get("device_id") or "").strip()
        if not state or not credential_json:
            return Response({"detail": "state and credential are required"}, status=status.HTTP_400_BAD_REQUEST)

        cached = _pop_challenge("reg", state)
        if not cached:
            return Response(
                {"detail": "Registration challenge expired or already used. Request new options and retry."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            verification = webauthn.verify_registration_response(
                credential=credential_json,
                expected_challenge=_b64url_decode(cached["challenge"]),
                expected_origin=settings.WEBAUTHN_ORIGIN,
                expected_rp_id=settings.WEBAUTHN_RP_ID,
                require_user_verification=False,
            )
        except InvalidRegistrationResponse as exc:
            logger.warning("Restore credential registration failed for user %s: %s", request.user.id, exc)
            return Response({"detail": "Could not verify restore credential."}, status=status.HTTP_400_BAD_REQUEST)

        credential_id_b64 = base64.urlsafe_b64encode(verification.credential_id).decode("ascii").rstrip("=")
        public_key_b64 = base64.urlsafe_b64encode(verification.credential_public_key).decode("ascii").rstrip("=")

        RestoreCredential.objects.update_or_create(
            credential_id=credential_id_b64,
            defaults={
                "user": request.user,
                "public_key": public_key_b64,
                "sign_count": verification.sign_count,
                "origin_device_id": device_id,
                "revoked_at": None,
                "revoke_reason": "",
            },
        )
        log_security_event(
            request.user,
            "security.auth.restore_credential_registered",
            request=request,
            severity="info",
            device_id=device_id,
        )
        return Response({"status": "registered"})


class RestoreCredentialAuthenticationOptionsView(APIView):
    """
    Called on a brand-new device, before any user is known — this is the
    "zero tap" entry point. No username/identifier is requested from the
    client at all; WebAuthn's discoverable-credential flow means the
    assertion itself (via its credential ID) tells us which user this is
    once verified.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        options = webauthn.generate_authentication_options(
            rp_id=settings.WEBAUTHN_RP_ID,
            user_verification=UserVerificationRequirement.PREFERRED,
        )
        state = _cache_challenge("auth", options.challenge)
        return Response({
            "state": state,
            "options": webauthn.options_to_json(options),
        })


class RestoreCredentialAuthenticateView(APIView):
    """
    Verifies the assertion, resolves it to a RestoreCredential -> User, and
    on success issues a normal JWT pair for the new device — the same
    issue_tokens_for_user()/Device upsert path a password login uses, so a
    restored session is indistinguishable from a normal one from that point
    on. Device-migration continuity proven by a valid signature over this
    challenge is treated the same way SIM-verified login already is
    elsewhere in this file: strong enough out-of-band proof of ownership to
    become the account's new primary device without a QR-link step.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        state = request.data.get("state")
        credential_json = request.data.get("credential")
        device_id = (request.data.get("device_id") or "").strip()
        device_platform = request.data.get("device_platform") or None
        device_name = request.data.get("device_name") or None
        if not state or not credential_json or not device_id:
            return Response(
                {"detail": "state, credential, and device_id are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cached = _pop_challenge("auth", state)
        if not cached:
            return Response(
                {"detail": "Authentication challenge expired or already used. Request new options and retry."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw_credential_id = credential_json.get("id") if isinstance(credential_json, dict) else None
        if not raw_credential_id:
            return Response({"detail": "Malformed credential."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            stored = RestoreCredential.objects.select_related("user").get(
                credential_id=raw_credential_id, revoked_at__isnull=True,
            )
        except RestoreCredential.DoesNotExist:
            return Response({"detail": "Unknown restore credential."}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            verification = webauthn.verify_authentication_response(
                credential=credential_json,
                expected_challenge=_b64url_decode(cached["challenge"]),
                expected_origin=settings.WEBAUTHN_ORIGIN,
                expected_rp_id=settings.WEBAUTHN_RP_ID,
                credential_public_key=_b64url_decode(stored.public_key),
                credential_current_sign_count=stored.sign_count,
                require_user_verification=False,
            )
        except InvalidAuthenticationResponse as exc:
            logger.warning("Restore credential authentication failed for credential %s: %s", stored.id, exc)
            log_security_event(
                stored.user,
                "security.auth.restore_credential_rejected",
                request=request,
                severity="warning",
                device_id=device_id,
            )
            return Response({"detail": "Could not verify restore credential."}, status=status.HTTP_401_UNAUTHORIZED)

        user = stored.user
        with transaction.atomic():
            stored.sign_count = verification.new_sign_count
            stored.last_used_at = timezone.now()
            stored.save(update_fields=["sign_count", "last_used_at", "updated_at"])

            # Same "out-of-band proof, promote to sole primary device"
            # pattern as promote_device_via_sim() in views.py — a valid
            # signature from a key that never left the original device's
            # secure storage is at least as strong a continuity signal as
            # SIM match, so this device becomes the new primary rather than
            # being gated behind the secondary-device QR flow.
            normalized_device_id = str(device_id).strip()
            existing_parents = (
                Device.objects.select_for_update()
                .filter(user=user, is_parent=True, revoked_at__isnull=True)
                .exclude(device_id=normalized_device_id)
            )
            list(existing_parents)  # lock rows before the update below
            Device.objects.filter(
                user=user, is_parent=True, revoked_at__isnull=True,
            ).exclude(device_id=normalized_device_id).update(is_parent=False)

            Device.objects.update_or_create(
                user=user,
                device_id=normalized_device_id,
                defaults={
                    "platform": device_platform or "unknown",
                    "name": device_name or None,
                    "last_seen_at": timezone.now(),
                    "last_ip": request.META.get("REMOTE_ADDR") if request else None,
                    "user_agent": request.META.get("HTTP_USER_AGENT") if request else None,
                    "revoked_at": None,
                    "revoke_reason": "",
                    "is_parent": True,
                },
            )

        tokens = issue_tokens_for_user(user, device_id=device_id)
        log_security_event(
            user,
            "security.auth.restore_credential_redeemed",
            request=request,
            severity="info",
            device_id=device_id,
            device_platform=device_platform,
        )
        return Response({
            "access": tokens.get("access"),
            "refresh": tokens.get("refresh"),
            "user": {
                "id": user.id,
                "phone": user.phone,
                "phone_country_code": getattr(user, "phone_country_code", None),
                "phone_number": getattr(user, "phone_number", None),
                "status": getattr(user, "status", "active"),
                "is_active": user.is_active,
                "device_id": device_id,
            },
        })
