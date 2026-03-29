"""
Middleware: Request logging and Quota enforcement.
"""
import time
import logging
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)

class RequestLoggingMiddleware(MiddlewareMixin):
    def process_request(self, request):
        request._start_time = time.time()
        logger.debug(f"REQ START {request.method} {request.get_full_path()}")

    def process_response(self, request, response):
        duration = (time.time() - getattr(request, "_start_time", time.time())) * 1000.0
        logger.debug(f"REQ END {request.method} {request.get_full_path()} {response.status_code} {duration:.2f}ms")
        return response

class QuotaEnforcementMiddleware(MiddlewareMixin):
    """
    Backward-compatible no-op middleware.
    Quota enforcement for AI endpoints has been removed.
    """
    def process_view(self, request, view_func, view_args, view_kwargs):
        return None
