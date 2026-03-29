"""Helpers for recording audit entries and suspicious flags."""
from datetime import timedelta
from typing import Optional

from django.utils import timezone

from admin_control.models import AdminAuditEntry, SuspiciousActivityFlag


class AuditLogger:
    """Central helper for audit trail entries."""

    @staticmethod
    def log(
        actor,
        action_type: str,
        target_app: Optional[str] = None,
        target_model: Optional[str] = None,
        target_pk: Optional[str] = None,
        severity: str = AdminAuditEntry.Severity.INFO,
        metadata: Optional[dict] = None,
    ) -> AdminAuditEntry:
        return AdminAuditEntry.objects.create(
            actor=actor,
            action_type=action_type,
            target_app=target_app or "",
            target_model=target_model or "",
            target_pk=target_pk or "",
            severity=severity,
            metadata=metadata or {},
            action=action_type,
        )


class SuspiciousActivityDetector:
    """Flag suspicious behaviors observed through admin requests."""

    ERROR_WINDOW_MINUTES = 10
    ERROR_THRESHOLD = 3

    def evaluate(self, record: dict):
        severity = AdminAuditEntry.Severity.WARNING
        actor = record.get("user_id")
        status_code = record.get("status_code")
        duration = record.get("duration_ms", 0)
        path = record.get("path")
        reason = None

        if status_code and status_code >= 500:
            reason = "Server errors observed"
            severity = AdminAuditEntry.Severity.CRITICAL
        elif duration and duration > 2000:
            reason = "Slow admin request (>2s)"
        else:
            window_start = timezone.now() - timedelta(minutes=self.ERROR_WINDOW_MINUTES)
            errors = AdminAuditEntry.objects.filter(
                actor_id=actor,
                created_at__gte=window_start,
                severity__in=[AdminAuditEntry.Severity.WARNING, AdminAuditEntry.Severity.CRITICAL],
            )
            if errors.count() >= self.ERROR_THRESHOLD:
                reason = "Repeated warning events"
        if not reason:
            return None
        flag = SuspiciousActivityFlag.objects.create(
            actor_id=actor if actor else None,
            reason=reason,
            path=path or "",
            severity=severity,
            metadata={
                "status_code": status_code,
                "duration_ms": duration,
            },
        )
        return flag
