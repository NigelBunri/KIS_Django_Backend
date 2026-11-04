# apps/bots/auth.py
"""
External-bot authentication — a dedicated OAuth2 client-credentials flow,
deliberately NOT built on rest_framework_simplejwt (that's for human User
sessions). Per section 7: "Do not reuse KIS user access tokens as
permanent bot credentials." Uses django.core.signing (the same in-house
signed-token mechanism apps.media.signing already uses for short-lived
asset download tokens), scoped to its own salt so a bot access token can
never be confused with, or substituted for, any other signed token in
this codebase.
"""
from __future__ import annotations

import json

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core import signing
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .models import BotCredential, BotStatus

BOT_TOKEN_SALT = "kis-bot-access-token"
BOT_TOKEN_TTL_SECONDS = int(getattr(settings, "BOT_ACCESS_TOKEN_TTL_SECONDS", 3600))


def issue_access_token(credential: BotCredential) -> dict:
    signer = signing.TimestampSigner(salt=BOT_TOKEN_SALT)
    payload = json.dumps({
        "credential_id": str(credential.id),
        "bot_id": str(credential.bot_id),
    })
    token = signer.sign(payload)
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": BOT_TOKEN_TTL_SECONDS,
        "scope": " ".join(credential.scopes or []),
    }


def _unsign_token(token: str) -> dict | None:
    signer = signing.TimestampSigner(salt=BOT_TOKEN_SALT)
    try:
        raw = signer.unsign(token, max_age=BOT_TOKEN_TTL_SECONDS)
    except signing.BadSignature:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


class BotAccessTokenAuthentication(BaseAuthentication):
    """Validates `Authorization: Bearer <bot-access-token>` on the
    external bot API surface ONLY (apps.bots.urls' external_* routes) -
    never wired into any endpoint a human user also calls, so there is
    no path by which this could be confused with normal user auth."""

    keyword = "Bearer"

    def authenticate(self, request):
        header = request.headers.get("Authorization", "")
        if not header.startswith(f"{self.keyword} "):
            return None
        token = header[len(self.keyword) + 1:].strip()
        if not token:
            return None

        claims = _unsign_token(token)
        if not claims:
            raise AuthenticationFailed("Invalid or expired bot access token.")

        try:
            credential = BotCredential.objects.select_related("bot").get(
                id=claims.get("credential_id"), is_active=True,
            )
        except (BotCredential.DoesNotExist, ValueError, TypeError):
            raise AuthenticationFailed("This bot credential has been revoked.")

        if str(credential.bot_id) != str(claims.get("bot_id")):
            raise AuthenticationFailed("Token does not match its issuing bot.")
        if credential.bot.status != BotStatus.ACTIVE:
            raise AuthenticationFailed("This bot is not active.")

        from django.utils import timezone

        credential.last_used_at = timezone.now()
        ip = request.META.get("REMOTE_ADDR")
        if ip:
            credential.last_used_ip = ip
        credential.save(update_fields=["last_used_at", "last_used_ip"])

        # (AnonymousUser, credential) — request.user stays a well-formed
        # AnonymousUser (no AttributeErrors in generic middleware/logging
        # that defensively reads request.user), request.auth is the real
        # bot identity every view in this module actually checks.
        return (AnonymousUser(), credential)
