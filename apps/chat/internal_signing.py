import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger("security.internal_auth")

SIGNATURE_HEADER = "X-Internal-Signature"
TIMESTAMP_HEADER = "X-Internal-Timestamp"
NONCE_HEADER = "X-Internal-Nonce"
AUTH_HEADER = "X-Internal-Auth"
DEFAULT_MAX_SKEW_SECONDS = 300


def _strict_internal_signatures_required() -> bool:
    configured = os.environ.get("INTERNAL_SIGNATURE_REQUIRED")
    if configured is not None:
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    return getattr(settings, "DEBUG", False) is False


def _max_skew_seconds() -> int:
    raw = os.environ.get("INTERNAL_SIGNATURE_MAX_SKEW_SECONDS", str(DEFAULT_MAX_SKEW_SECONDS))
    try:
        return max(30, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_MAX_SKEW_SECONDS


def _canonical_json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


def canonical_body_hash(body) -> str:
    if body in (None, b"", ""):
        return hashlib.sha256(b"").hexdigest()
    if isinstance(body, bytes):
        raw = body
    elif isinstance(body, str):
        raw = body.encode("utf-8")
    else:
        raw = _canonical_json(body).encode("utf-8")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception:
        return hashlib.sha256(raw).hexdigest()
    return hashlib.sha256(_canonical_json(parsed).encode("utf-8")).hexdigest()


def _path_with_query(url_or_path: str) -> str:
    parsed = urlsplit(url_or_path)
    path = parsed.path or "/"
    return f"{path}?{parsed.query}" if parsed.query else path


def _signature_payload(method: str, path_with_query: str, timestamp: str, nonce: str, body_hash: str) -> str:
    return "\n".join(
        [
            method.upper(),
            path_with_query or "/",
            str(timestamp),
            str(nonce),
            str(body_hash),
        ]
    )


def sign_internal_request(method: str, url_or_path: str, body=None, secret: str | None = None) -> dict[str, str]:
    signing_secret = (secret or getattr(settings, "NEST_INTERNAL_TOKEN", "") or os.environ.get("DJANGO_INTERNAL_TOKEN", "")).strip()
    if not signing_secret:
        return {}
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    body_hash = canonical_body_hash(body)
    payload = _signature_payload(method, _path_with_query(url_or_path), timestamp, nonce, body_hash)
    signature = hmac.new(signing_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        AUTH_HEADER: signing_secret,
        TIMESTAMP_HEADER: timestamp,
        NONCE_HEADER: nonce,
        SIGNATURE_HEADER: signature,
    }


def verify_internal_request(request, expected_secret: str) -> tuple[bool, str]:
    timestamp = request.headers.get(TIMESTAMP_HEADER, "")
    nonce = request.headers.get(NONCE_HEADER, "")
    signature = request.headers.get(SIGNATURE_HEADER, "")
    if not timestamp or not nonce or not signature:
        return False, "missing_signature_headers"
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False, "invalid_timestamp"
    if abs(int(time.time()) - ts) > _max_skew_seconds():
        return False, "timestamp_outside_window"
    nonce_key = f"internal-auth-nonce:{nonce}"
    if not cache.add(nonce_key, "1", timeout=_max_skew_seconds()):
        return False, "replayed_nonce"

    try:
        body = request.body
    except Exception:
        body = b""
    body_hash = canonical_body_hash(body)
    payload = _signature_payload(request.method, request.get_full_path(), timestamp, nonce, body_hash)
    expected = hmac.new(expected_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        logger.warning(
            "internal_auth.signature_mismatch",
            extra={"reason": "signature_mismatch", "path": request.path, "method": request.method},
        )
        return False, "signature_mismatch"
    return True, "ok"


def internal_signatures_required() -> bool:
    return _strict_internal_signatures_required()
