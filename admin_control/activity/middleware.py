"""Middleware to capture admin control activity."""
import logging
import time

from django.utils.deprecation import MiddlewareMixin

from admin_control.activity import AdminActivityLogger
from admin_control.audit.logging import SuspiciousActivityDetector
from admin_control.models import AdminUserActivity
from admin_control.services import AdminCacheService

logger = logging.getLogger("admin_control.activity")
_activity_logger = AdminActivityLogger()
_detector = SuspiciousActivityDetector()


class AdminControlActivityMiddleware(MiddlewareMixin):
    """Logs admin control requests for observability when Phase 1 expands."""

    def process_request(self, request):
        request._admin_control_start = time.time()

    def process_response(self, request, response):
        path = getattr(request, "path", "")
        if path.startswith("/control/admin/") and hasattr(request, "user"):
            duration_ms = (time.time() - getattr(request, "_admin_control_start", time.time())) * 1000.0
            try:
                record = _activity_logger.log_request(
                    user_id=str(request.user.pk) if request.user and request.user.is_authenticated else "anonymous",
                    path=path,
                    method=request.method,
                    ip=request.META.get("REMOTE_ADDR", "0.0.0.0"),
                    device=request.META.get("HTTP_USER_AGENT", "unknown"),
                    duration_ms=duration_ms,
                    status_code=getattr(response, "status_code", 0) or 0,
                    response_size=len(getattr(response, "content", b"") or b"") if hasattr(response, "content") else 0,
                )
                logger.debug("Admin activity recorded: %s", record)
            except Exception as exc:
                logger.exception("Failed to record admin activity: %s", exc)
            else:
                try:
                    AdminUserActivity.objects.create(
                        actor=request.user if request.user and request.user.is_authenticated else None,
                        action="request",
                        metadata={
                            "user_agent": request.META.get("HTTP_USER_AGENT", ""),
                            "query_string": request.META.get("QUERY_STRING", ""),
                        },
                        path=path,
                        method=request.method,
                        status_code=getattr(response, "status_code", 0) or 0,
                        ip_address=request.META.get("REMOTE_ADDR", "0.0.0.0"),
                        device=request.META.get("HTTP_USER_AGENT", ""),
                        duration_ms=duration_ms,
                        response_size=len(getattr(response, "content", b"") or b""),
                    )
                    _detector.evaluate(record)
                    AdminCacheService.invalidate_dashboard()
                    AdminCacheService.invalidate_micro()
                except Exception as exc:
                    logger.exception("Failed to persist admin activity: %s", exc)
        return response
