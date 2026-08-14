import os
import hmac
import logging
from rest_framework.exceptions import AuthenticationFailed

from .internal_signing import internal_signatures_required, verify_internal_request

logger = logging.getLogger("security.internal_auth")


def require_internal_auth(request) -> None:
    # Idempotent per-request: several call sites can legitimately check the
    # same request (DeviceBoundJWTAuthentication's DRF-driven authenticate()
    # runs before the view body, and some views — e.g. IntrospectView — also
    # call this explicitly for defense-in-depth). The signature check below
    # consumes a single-use nonce, so a second real verification of the same
    # request would always fail as a replay; cache the outcome on the
    # request instead of re-verifying.
    if getattr(request, "_internal_auth_verified", False):
        return

    expected = os.environ.get("DJANGO_INTERNAL_TOKEN", "")
    got = request.headers.get("X-Internal-Auth", "")

    if not expected or not hmac.compare_digest(str(got), str(expected)):
        logger.warning(
            "internal_auth.failed",
            extra={"reason": "invalid_token", "path": request.path, "method": request.method},
        )
        raise AuthenticationFailed("Invalid internal auth")

    signed, reason = verify_internal_request(request, expected)
    if signed:
        request._internal_auth_verified = True
        return
    if internal_signatures_required():
        logger.warning(
            "internal_auth.failed",
            extra={"reason": reason, "path": request.path, "method": request.method},
        )
        raise AuthenticationFailed("Invalid internal auth")

    logger.info(
        "internal_auth.legacy_token_allowed",
        extra={"reason": reason, "path": request.path, "method": request.method},
    )
    request._internal_auth_verified = True
