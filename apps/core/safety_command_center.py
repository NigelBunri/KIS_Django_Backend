from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.utils import timezone


def _count(queryset) -> int:
    try:
        return int(queryset.count())
    except Exception:
        return 0


def _safe_section(*, status: str, count: int, label: str, detail: str, route: str = "") -> dict[str, Any]:
    return {
        "status": status,
        "count": int(count or 0),
        "label": label,
        "detail": detail,
        "route": route,
    }


def _health_status(*, critical: int = 0, warning: int = 0) -> str:
    if critical > 0:
        return "critical"
    if warning > 0:
        return "warning"
    return "healthy"


def _media_safety_summary(since):
    from apps.media.models import MediaSafetyScan

    open_qs = MediaSafetyScan.objects.filter(is_deleted=False).filter(
        status__in=["pending_review", "blocked", "failed"],
    ) | MediaSafetyScan.objects.filter(is_deleted=False, quarantine=True) | MediaSafetyScan.objects.filter(
        is_deleted=False,
        requires_review=True,
    )
    recent = MediaSafetyScan.objects.filter(is_deleted=False, created_at__gte=since)
    blocked = recent.filter(status="blocked")
    failed = recent.filter(status="failed")
    pending = MediaSafetyScan.objects.filter(is_deleted=False, status="pending_review")
    return {
        "open_queue": _count(open_qs.distinct()),
        "pending_review": _count(pending),
        "blocked_24h": _count(blocked),
        "failed_24h": _count(failed),
        "provider_ready": False,
        "live_provider_calls_enabled": False,
    }


def _moderation_summary(since):
    from apps.moderation.models import AuditLog, Flag, ModerationAction, SafetyAlert

    pending = Flag.objects.filter(is_deleted=False, status="PENDING")
    high = pending.filter(severity__in=["HIGH", "CRITICAL"])
    alerts = SafetyAlert.objects.filter(is_deleted=False, acknowledged_at__isnull=True, resolved_at__isnull=True)
    return {
        "pending_flags": _count(pending),
        "high_risk_flags": _count(high),
        "open_alerts": _count(alerts),
        "actions_24h": _count(ModerationAction.objects.filter(is_deleted=False, created_at__gte=since)),
        "audit_events_24h": _count(AuditLog.objects.filter(is_deleted=False, created_at__gte=since)),
    }


def _verification_summary(since):
    from apps.verification.constants import VerificationBadgeStatus, VerificationCaseStatus
    from apps.verification.models import VerificationBadge, VerificationCase

    open_cases = VerificationCase.objects.filter(
        status__in=[
            VerificationCaseStatus.SUBMITTED,
            VerificationCaseStatus.IN_REVIEW,
            VerificationCaseStatus.NEEDS_MORE_INFO,
        ],
    )
    expiring_badges = VerificationBadge.objects.filter(
        status=VerificationBadgeStatus.ACTIVE,
        expires_at__isnull=False,
        expires_at__lte=timezone.now() + timedelta(days=30),
    )
    return {
        "open_cases": _count(open_cases),
        "needs_info": _count(open_cases.filter(status=VerificationCaseStatus.NEEDS_MORE_INFO)),
        "expiring_badges_30d": _count(expiring_badges),
        "reviewed_24h": _count(VerificationCase.objects.filter(reviewed_at__gte=since)),
    }


def _payment_summary(since):
    from apps.billing.models import DirectPaymentAuditEvent, DirectPaymentIntent

    pending = DirectPaymentIntent.objects.filter(status=DirectPaymentIntent.STATUS_PENDING)
    failed_recent = DirectPaymentIntent.objects.filter(status=DirectPaymentIntent.STATUS_FAILED, updated_at__gte=since)
    cancelled_recent = DirectPaymentIntent.objects.filter(status=DirectPaymentIntent.STATUS_CANCELLED, updated_at__gte=since)
    return {
        "pending_intents": _count(pending),
        "failed_24h": _count(failed_recent),
        "cancelled_24h": _count(cancelled_recent),
        "audit_events_24h": _count(DirectPaymentAuditEvent.objects.filter(created_at__gte=since)),
        "provider": "flutterwave",
        "provider_ready": False,
    }


def _notification_summary(since):
    from apps.notifications.models import Notification, NotificationDelivery, NotificationDeviceToken

    unread = Notification.objects.filter(is_deleted=False, is_read=False)
    failed_deliveries = NotificationDelivery.objects.filter(is_deleted=False, status="FAILED")
    pending_deliveries = NotificationDelivery.objects.filter(is_deleted=False, status="PENDING")
    return {
        "unread_in_app": _count(unread),
        "created_24h": _count(Notification.objects.filter(is_deleted=False, created_at__gte=since)),
        "failed_deliveries": _count(failed_deliveries),
        "pending_deliveries": _count(pending_deliveries),
        "active_device_tokens": _count(NotificationDeviceToken.objects.filter(is_deleted=False, enabled=True)),
    }


def _messaging_summary(since):
    from apps.chat.models import Conversation, ConversationMember, MessageThreadLink

    active_conversations = Conversation.objects.filter(is_archived=False)
    stale = active_conversations.filter(last_message_at__isnull=True, updated_at__lt=since)
    return {
        "active_conversations": _count(active_conversations),
        "new_conversations_24h": _count(Conversation.objects.filter(created_at__gte=since)),
        "conversation_members": _count(ConversationMember.objects.all()),
        "subrooms": _count(MessageThreadLink.objects.all()),
        "stale_without_last_message": _count(stale),
        "nest_realtime_required": True,
    }


def _provider_readiness_summary():
    return {
        "firebase_admin_credentials": "evidence_required",
        "flutterwave_callbacks": "evidence_required",
        "verification_provider_sandbox": "evidence_required",
        "explicit_content_provider": "disabled_by_default",
        "backup_restore_proof": "evidence_required",
        "rollback_proof": "evidence_required",
    }


def staff_safety_command_center_summary() -> dict[str, Any]:
    since = timezone.now() - timedelta(hours=24)

    media = _media_safety_summary(since)
    moderation = _moderation_summary(since)
    verification = _verification_summary(since)
    payments = _payment_summary(since)
    notifications = _notification_summary(since)
    messaging = _messaging_summary(since)
    provider_readiness = _provider_readiness_summary()

    critical = (
        media["failed_24h"]
        + moderation["high_risk_flags"]
        + moderation["open_alerts"]
        + payments["failed_24h"]
        + notifications["failed_deliveries"]
    )
    warning = (
        media["pending_review"]
        + verification["open_cases"]
        + payments["pending_intents"]
        + notifications["pending_deliveries"]
        + messaging["stale_without_last_message"]
    )

    sections = {
        "system_health": _safe_section(
            status=_health_status(critical=critical, warning=warning),
            count=critical + warning,
            label="Operational attention",
            detail="Aggregated safe signals across safety, trust, payments, notification, and messaging systems.",
            route="admin.command_center",
        ),
        "abuse_signals": _safe_section(
            status=_health_status(critical=moderation["high_risk_flags"] + moderation["open_alerts"], warning=moderation["pending_flags"]),
            count=moderation["pending_flags"],
            label="Moderation queue",
            detail="Pending flags, high-risk flags, open alerts, and moderator activity.",
            route="moderation.staff_operations",
        ),
        "media_quarantine": _safe_section(
            status=_health_status(critical=media["failed_24h"], warning=media["open_queue"]),
            count=media["open_queue"],
            label="Media safety queue",
            detail="Quarantined, blocked, failed, and pending-review upload safety scans.",
            route="media.safety_scans",
        ),
        "verification_queue": _safe_section(
            status=_health_status(warning=verification["open_cases"] + verification["expiring_badges_30d"]),
            count=verification["open_cases"],
            label="Verification review",
            detail="Open verification cases, needs-info cases, and badges expiring within 30 days.",
            route="verification.staff_console",
        ),
        "payment_incidents": _safe_section(
            status=_health_status(critical=payments["failed_24h"], warning=payments["pending_intents"]),
            count=payments["pending_intents"] + payments["failed_24h"],
            label="Payment readiness",
            detail="Direct USD payment intents and audit-event visibility without raw provider payloads.",
            route="billing.direct_payments",
        ),
        "messaging_delivery": _safe_section(
            status=_health_status(warning=messaging["stale_without_last_message"]),
            count=messaging["active_conversations"],
            label="Messaging reliability",
            detail="Conversation membership, subroom, and list-readiness indicators. Nest realtime remains required for delivery details.",
            route="chat.operations",
        ),
        "notification_health": _safe_section(
            status=_health_status(critical=notifications["failed_deliveries"], warning=notifications["pending_deliveries"]),
            count=notifications["unread_in_app"],
            label="Notification health",
            detail="Unread in-app notifications, push delivery state, pending deliveries, and active device tokens.",
            route="notifications.operations",
        ),
        "provider_readiness": _safe_section(
            status="warning",
            count=sum(1 for value in provider_readiness.values() if value == "evidence_required"),
            label="Provider launch evidence",
            detail="Evidence still needed before production enablement for credentials, callbacks, backups, rollback, and content-safety providers.",
            route="operations.launch_readiness",
        ),
    }

    return {
        "version": "phase_21_safety_command_center",
        "window": "24h",
        "overall_status": _health_status(critical=critical, warning=warning),
        "counts": {
            "critical_signals": int(critical),
            "warning_signals": int(warning),
            "media_open_queue": media["open_queue"],
            "moderation_pending_flags": moderation["pending_flags"],
            "verification_open_cases": verification["open_cases"],
            "payment_pending_intents": payments["pending_intents"],
            "notification_failed_deliveries": notifications["failed_deliveries"],
            "messaging_active_conversations": messaging["active_conversations"],
        },
        "sections": sections,
        "details": {
            "media_safety": media,
            "moderation": moderation,
            "verification": verification,
            "payments": payments,
            "notifications": notifications,
            "messaging": messaging,
            "provider_readiness": provider_readiness,
        },
        "privacy": {
            "staff_only": True,
            "no_secrets": True,
            "no_raw_documents": True,
            "no_raw_storage_paths": True,
            "no_private_health_records": True,
            "no_payment_instrument_data": True,
            "no_raw_provider_payloads": True,
        },
        "launch_blockers": [
            key for key, value in provider_readiness.items() if value == "evidence_required"
        ],
    }
