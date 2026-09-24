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


#: Registration purposes Django will actually complete an account for.
#: "registration" is the original Google JIT flow; "enterprise_sso_registration"
#: is an IdP-federated (OIDC) JIT signup with no phone number available -
#: KisAuthRegistrationCompleteView branches on this to synthesize a
#: placeholder phone instead of requiring one from the request body.
REGISTRATION_PURPOSES = {"registration", "enterprise_sso_registration"}


@dataclass(frozen=True)
class VerifiedRegistration:
    """Deliberately has no kis_user_id/auth_identity_id — a registration
    ticket is issued before any KIS account or linked identity exists.
    provider_subject is what Django uses, immediately after creating the
    User, to call kis-auth's link endpoint and associate the two.

    provider/partner_slug default to the original Google flow's implicit
    values so existing tickets (minted before these claims existed) keep
    decoding the same way. For enterprise_sso_registration, kis-auth sets
    provider="oidc" and partner_slug to the tenant the IdP config belongs
    to - partner_slug makes the (provider, provider_subject) identity key
    unique across tenants whose upstream IdPs could otherwise mint
    colliding `sub` values."""

    purpose: str
    provider_subject: str
    provider_email: str | None
    provider_email_verified: bool
    provider: str = "google"
    partner_slug: str | None = None


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
        #
        # Explicit User-Agent required: PyJWKClient's default fetch uses
        # bare urllib with Python's stock "Python-urllib/x.y" UA, which
        # kisauth's Cloudflare front rejects outright with a 403 before the
        # request ever reaches the app — confirmed live against production
        # (plain `requests`/urllib with any browser-style UA succeeds,
        # urllib with no UA override does not). Without this, every KIS
        # Auth verification fails at the JWKS-fetch step, before any JWT
        # content is even inspected.
        _jwk_client = PyJWKClient(
            _jwks_url(),
            lifespan=600,
            headers={"User-Agent": "KIS-Django-Backend/1.0"},
        )
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


def redeem_registration_ticket(
    *, code: str, client_id: str, redirect_uri: str
) -> VerifiedRegistration:
    """Same HMAC-signed request + JWKS-verified response shape as
    redeem_authorization_code, against the separate registration-exchange
    endpoint — the JWT here carries provider_subject instead of sub, since
    no KIS account exists yet at this point."""
    secret = os.environ.get("KISAUTH_INTERNAL_HMAC_SECRET", "").strip()
    if not secret:
        raise ExchangeError("KISAUTH_INTERNAL_HMAC_SECRET not configured")

    base = _base_url()
    if not base:
        raise ExchangeError("KISAUTH_BASE_URL not configured")

    path = "/internal/v1/registration/exchange"
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
        logger.exception("kis_auth_bridge.registration_exchange_request_failed")
        raise ExchangeError("exchange request failed") from None

    if response.status_code != 201:
        logger.warning(
            "kis_auth_bridge.registration_exchange_rejected",
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
        logger.exception("kis_auth_bridge.registration_jwt_verification_failed")
        raise ExchangeError("jwt verification failed") from None

    purpose = payload.get("purpose")
    provider_subject = payload.get("provider_subject")
    if purpose not in REGISTRATION_PURPOSES or not provider_subject:
        raise ExchangeError("jwt missing required claims")

    partner_slug = payload.get("partner_slug")
    return VerifiedRegistration(
        purpose=str(purpose),
        provider_subject=str(provider_subject),
        provider_email=payload.get("provider_email"),
        provider_email_verified=bool(payload.get("provider_email_verified", False)),
        provider=str(payload.get("provider") or "google"),
        partner_slug=str(partner_slug) if partner_slug else None,
    )


def link_identity_server_to_server(
    *,
    kis_user_id: str,
    provider_subject: str,
    provider_email: str | None,
    provider_email_verified: bool,
    provider: str = "google",
) -> None:
    """Direct server-to-server call to kis-auth's /internal/v1/identity/link
    — used only right after registration, where Google auth already
    happened (proven by the registration ticket having existed at all) and
    there's no browser round trip left to attach a link-purpose OAuth flow
    to. Raises ExchangeError on any failure, including a 409 conflict
    (this provider_subject or kis_user_id already linked to something
    else) — the caller decides how to surface that."""
    secret = os.environ.get("KISAUTH_INTERNAL_HMAC_SECRET", "").strip()
    if not secret:
        raise ExchangeError("KISAUTH_INTERNAL_HMAC_SECRET not configured")

    base = _base_url()
    if not base:
        raise ExchangeError("KISAUTH_BASE_URL not configured")

    path = "/internal/v1/identity/link"
    body = {
        "provider": provider,
        "provider_subject": provider_subject,
        "kis_user_id": kis_user_id,
        "provider_email": provider_email,
        "provider_email_verified": provider_email_verified,
    }
    headers = sign_internal_request("POST", path, body=body, secret=secret)
    if not headers:
        raise ExchangeError("failed to sign internal request")
    headers["Content-Type"] = "application/json"

    try:
        response = requests.post(
            f"{base}{path}", json=body, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS
        )
    except requests.RequestException:
        logger.exception("kis_auth_bridge.identity_link_request_failed")
        raise ExchangeError("identity link request failed") from None

    if response.status_code == 409:
        raise ExchangeError("identity already linked")
    if response.status_code != 201:
        logger.warning(
            "kis_auth_bridge.identity_link_rejected",
            extra={"status_code": response.status_code},
        )
        raise ExchangeError(f"identity link rejected: {response.status_code}")
