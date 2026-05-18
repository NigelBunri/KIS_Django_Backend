"""
Middleware: Request logging and Quota enforcement.
"""
import time
import logging
import uuid
import threading
from django.utils.deprecation import MiddlewareMixin
from common.security_redaction import redact_url

logger = logging.getLogger(__name__)

_request_id_local = threading.local()


def get_request_id() -> str:
    return getattr(_request_id_local, "request_id", "-")


class RequestLoggingMiddleware(MiddlewareMixin):
    def process_request(self, request):
        rid = request.headers.get("X-Request-Id") or uuid.uuid4().hex
        request.request_id = rid
        _request_id_local.request_id = rid
        request._start_time = time.time()
        logger.debug("REQ START %s %s rid=%s", request.method, redact_url(request.get_full_path()), rid)

    def process_response(self, request, response):
        duration = (time.time() - getattr(request, "_start_time", time.time())) * 1000.0
        rid = getattr(request, "request_id", "-")
        logger.debug(
            "REQ END %s %s %s %.2fms rid=%s",
            request.method,
            redact_url(request.get_full_path()),
            response.status_code,
            duration,
            rid,
        )
        response["X-Request-Id"] = rid
        _request_id_local.request_id = "-"
        return response

class QuotaEnforcementMiddleware(MiddlewareMixin):
    """
    Backward-compatible no-op middleware.
    Quota enforcement for AI endpoints has been removed.
    """
    def process_view(self, request, view_func, view_args, view_kwargs):
        return None
