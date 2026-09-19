"""Django's client for KIS Auth's server-to-server exchange endpoint.

Two independent security properties, deliberately not conflated:
  1. The HMAC-signed request proves this call came from Django (reusing
     apps.chat.internal_signing verbatim — cross-language interop with
     kis-auth's TypeScript verifier confirmed byte-for-byte before this
     file was written, not assumed).
  2. The signed JWT KIS Auth returns proves the *content* of the result
     (who authenticated, for what purpose) independent of (1) — verified
     here against KIS Auth's own published JWKS, never trusted just
     because the HTTP call succeeded.
"""
from __future__ import annotations

import os
import logging
from dataclasses import dataclass

import requests
import jwt
from jwt import PyJWKClient

from apps.chat.internal_signing import sign_internal_request

logger = logging.getLogger("security.kis_auth_bridge")

ISSUER = "kisauth.kingdomimpactventures.org"
REQUEST_TIMEOUT_SECONDS = 5

_jwk_client: PyJWKClient | None = None


class ExchangeError(Exception):
    """Raised for every failure mode. Deliberately does not carry the
    specific reason in its public str() — callers show the same generic
    message externally regardless of cause; the real reason goes to the
    server-side log only, at the raise site."""


@dataclass(frozen=True)
class VerifiedAuthorization:
    kis_user_id: str
    purpose: str
    auth_identity_id: str
    provider_email: str | None
    provider_email_verified: bool


def _base_url() -> str:
    return os.environ.get("KISAUTH_BASE_URL", "").rstrip("/")


def _jwks_url() -> str:
    configured = os.environ.get("KISAUTH_JWKS_URL", "").strip()
    if configured:
        return configured
    base = _base_url()
    if not base:
        raise ExchangeError("KISAUTH_BASE_URL/KISAUTH_JWKS_URL not configured")
    return f"{base}/.well-known/jwks.json"


def _get_jwk_client() -> PyJWKClient:
    global _jwk_client
    if _jwk_client is None:
        # lifespan caches fetched keys for 10 minutes — short enough that a
        # rotation's overlap window (documented as ~7 days) comfortably
        # covers every worker picking up the new key without a restart.
        _jwk_client = PyJWKClient(_jwks_url(), lifespan=600)
    return _jwk_client


def redeem_authorization_code(
    *, code: str, client_id: str, redirect_uri: str
) -> VerifiedAuthorization:
    """Redeems a single-use KIS Auth authorization code and returns the
    verified claims. Raises ExchangeError for every failure — expired/
    reused code, signature mismatch, wrong audience, network failure,
    everything. Never distinguishes the reason to the caller."""
    secret = os.environ.get("KISAUTH_INTERNAL_HMAC_SECRET", "").strip()
    if not secret:
        raise ExchangeError("KISAUTH_INTERNAL_HMAC_SECRET not configured")

    base = _base_url()
    if not base:
        raise ExchangeError("KISAUTH_BASE_URL not configured")

    path = "/internal/v1/authorization/exchange"
    body = {"code": code, "client_id": client_id, "redirect_uri": redirect_uri}
    headers = sign_internal_request("POST", path, body=body, secret=secret)
    if not headers:
        raise ExchangeError("failed to sign internal request")
    headers["Content-Type"] = "application/json"

    try:
        response = requests.post(
            f"{base}{path}", json=body, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS
        )
    except requests.RequestException:
        logger.exception("kis_auth_bridge.exchange_request_failed")
        raise ExchangeError("exchange request failed") from None

    if response.status_code != 201:
        logger.warning(
            "kis_auth_bridge.exchange_rejected",
            extra={"status_code": response.status_code},
        )
        raise ExchangeError(f"exchange rejected: {response.status_code}")

    token = (response.json() or {}).get("token")
    if not token:
        raise ExchangeError("exchange response missing token")

    try:
        signing_key = _get_jwk_client().get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=ISSUER,
            audience=client_id,
        )
    except jwt.PyJWTError:
        logger.exception("kis_auth_bridge.jwt_verification_failed")
        raise ExchangeError("jwt verification failed") from None

    sub = payload.get("sub")
    purpose = payload.get("purpose")
    auth_identity_id = payload.get("auth_identity_id")
    if not sub or not purpose or not auth_identity_id:
        raise ExchangeError("jwt missing required claims")

    return VerifiedAuthorization(
        kis_user_id=str(sub),
        purpose=str(purpose),
        auth_identity_id=str(auth_identity_id),
        provider_email=payload.get("provider_email"),
        provider_email_verified=bool(payload.get("provider_email_verified", False)),
    )
