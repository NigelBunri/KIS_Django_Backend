"""
Middleware: Request logging and Quota enforcement.
"""
import time
import logging
from django.utils.deprecation import MiddlewareMixin
from common.security_redaction import redact_url

logger = logging.getLogger(__name__)

class RequestLoggingMiddleware(MiddlewareMixin):
    def process_request(self, request):
        request._start_time = time.time()
        logger.debug("REQ START %s %s", request.method, redact_url(request.get_full_path()))

    def process_response(self, request, response):
        duration = (time.time() - getattr(request, "_start_time", time.time())) * 1000.0
        logger.debug(
            "REQ END %s %s %s %.2fms",
            request.method,
            redact_url(request.get_full_path()),
            response.status_code,
            duration,
        )
        return response

class QuotaEnforcementMiddleware(MiddlewareMixin):
    """
    Backward-compatible no-op middleware.
    Quota enforcement for AI endpoints has been removed.
    """
    def process_view(self, request, view_func, view_args, view_kwargs):
        return None
