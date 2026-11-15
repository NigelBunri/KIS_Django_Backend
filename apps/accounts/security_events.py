from __future__ import annotations

import hashlib
import logging
from typing import Any

from django.core.cache import cache
from django.utils import timezone

from .models import AuditLog

try:
    from admin_control.audit.logging import AuditLogger
    from admin_control.models import AdminAuditEntry
except Exception:
    AuditLogger = None
    AdminAuditEntry = None

logger = logging.getLogger(__name__)


FAILED_AUTH_WINDOW_SECONDS = 15 * 60
FAILED_AUTH_WARNING_THRESHOLD = 5


def client_ip(request) -> str:
    forwarded_for = (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
    return forwarded_for or request.META.get("REMOTE_ADDR") or ""


def user_agent(request) -> str:
    return request.META.get("HTTP_USER_AGENT", "")


def request_meta(request, **extra: Any) -> dict:
    meta = {
        "ip": client_ip(request),
        "user_agent": user_agent(request),
        "path": request.path,
        "method": request.method,
        "at": timezone.now().isoformat(),
    }
    meta.update({key: value for key, value in extra.items() if value is not None})
    return meta


def safe_identifier(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


def log_security_event(actor, action: str, request=None, severity: str = "info", **extra: Any):
    meta = {"severity": severity, **extra}
    if request is not None:
        meta = request_meta(request, **meta)
    event = AuditLog.log(actor=actor, action=action, meta=meta)
    if AuditLogger and action.startswith("security."):
        admin_severity = severity
        if AdminAuditEntry and severity not in dict(AdminAuditEntry.Severity.choices):
            admin_severity = AdminAuditEntry.Severity.INFO
        try:
            AuditLogger.log(
                actor=actor,
                action_type=action[:64],
                target_app="accounts",
                target_model="User",
                target_pk=str(getattr(actor, "pk", "") or ""),
                severity=admin_severity,
                metadata={**meta, "audit_log_id": str(event.id)},
            )
        except Exception:
            pass
    return event


def notify_security_event(
    user,
    *,
    type: str,
    title: str,
    body: str,
    dedup_key: str | None = None,
    context: dict | None = None,
) -> None:
    """Best-effort in-app + push + email delivery for a security-relevant
    event (device revoked, E2EE identity key changed, etc). Deliberately
    separate from log_security_event (the audit trail, written
    unconditionally): until this existed, a "security.*" AuditLog entry was
    the ONLY record of these events anywhere — nothing ever reached the
    affected user (KIS_E2EE_KEY_LIFECYCLE_AUDIT.md Defect D4). Routes
    through the same create_notification() pipeline every other in-app
    notification uses, so it gets real IN_APP/PUSH/EMAIL delivery rows,
    retry/backoff, and stale-token cleanup for free.
    Never raises: a failed security *notification* must not fail the
    security *action* it's reporting on (key rotation, device revoke, ...).
    """
    try:
        from apps.notifications.services import create_notification

        create_notification(
            user_id=user.id,
            type=type,
            title=title,
            body=body,
            priority="HIGH",
            channels=["IN_APP", "PUSH", "EMAIL"],
            dedup_key=dedup_key,
            context=context or {},
        )
    except Exception:
        logger.exception(
            "Failed to deliver security notification %s to user %s", type, getattr(user, "id", None)
        )


def record_failed_auth(request, *, identifier: str | None = None, reason: str = "invalid_credentials") -> dict:
    ip = client_ip(request)
    ident_hash = safe_identifier(identifier)
    ip_key = f"security:auth_failed:ip:{ip or 'unknown'}"
    ident_key = f"security:auth_failed:identifier:{ident_hash or 'unknown'}"
    total_key = "security:auth_failed:total"

    ip_count = cache.get(ip_key, 0) + 1
    ident_count = cache.get(ident_key, 0) + 1
    total_count = cache.get(total_key, 0) + 1
    cache.set(ip_key, ip_count, FAILED_AUTH_WINDOW_SECONDS)
    cache.set(ident_key, ident_count, FAILED_AUTH_WINDOW_SECONDS)
    cache.set(total_key, total_count, FAILED_AUTH_WINDOW_SECONDS)

    severity = "warning" if max(ip_count, ident_count) >= FAILED_AUTH_WARNING_THRESHOLD else "info"
    event = log_security_event(
        None,
        "security.auth.failed",
        request=request,
        severity=severity,
        identifier_hash=ident_hash,
        reason=reason,
        ip_window_count=ip_count,
        identifier_window_count=ident_count,
    )
    return {
        "event_id": str(event.id),
        "ip_window_count": ip_count,
        "identifier_window_count": ident_count,
        "total_window_count": total_count,
        "severity": severity,
    }
