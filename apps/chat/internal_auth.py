import os
import hmac
import logging
from rest_framework.exceptions import AuthenticationFailed

from .internal_signing import internal_signatures_required, verify_internal_request

logger = logging.getLogger("security.internal_auth")


def require_internal_auth(request) -> None:
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
