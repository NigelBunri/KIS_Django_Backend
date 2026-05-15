from __future__ import annotations

from typing import Any

from django.utils import timezone

from apps.media.models import MediaAsset, MediaSafetyScan
from apps.media.safety import EXPLICIT_CONTENT_POLICY_VERSION

from . import models


STAFF_ACTIONS = {"approve", "block", "dismiss", "escalate", "review", "note"}


def request_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "") if request else ""
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return str(getattr(request, "META", {}).get("REMOTE_ADDR", "") if request else "")[:45]


def request_device(request) -> str:
    if not request:
        return ""
    return str(request.META.get("HTTP_USER_AGENT") or "")[:500]


def record_moderation_audit(
    *,
    actor,
    action: str,
    target_type: str,
    target_id,
    metadata: dict[str, Any] | None = None,
    request=None,
) -> models.AuditLog:
    return models.AuditLog.objects.create(
        actor_id=getattr(actor, "id", actor),
        action=str(action)[:128],
        target_type=str(target_type).upper()[:32],
        target_id=target_id,
        metadata=metadata or {},
        ip_address=request_ip(request) or None,
        device_info=request_device(request) or None,
    )


def create_media_safety_alert_for_scan(scan: MediaSafetyScan, *, actor=None, request=None) -> None:
    if scan.status not in {"pending_review", "blocked", "failed"} and not scan.quarantine and not scan.requires_review:
        return
    flag = None
    try:
        flag, _ = models.Flag.objects.get_or_create(
            source="SYSTEM",
            target_type="POST",
            target_id=scan.id,
            reason="Media upload requires KIS family-safety review.",
            defaults={
                "reporter_id": getattr(actor, "id", None),
                "severity": "HIGH" if scan.status in {"blocked", "failed"} else "MEDIUM",
                "status": "PENDING",
                "escalation_level": "MODERATOR",
                "tags": {
                    "source": "media_safety_scan",
                    "media_safety_scan_id": str(scan.id),
                    "context": scan.context,
                    "policy_version": scan.policy_version,
                },
            },
        )
    except Exception:
        flag = None
    try:
        models.SafetyAlert.objects.get_or_create(
            flag=flag,
            alert_type="HIGH_SEVERITY" if scan.status in {"blocked", "failed"} else "COMMUNITY_RISK",
            message=f"Media safety scan needs review for {scan.context or 'general'} upload.",
            defaults={"sent_to_ids": []},
        )
    except Exception:
        pass
    record_moderation_audit(
        actor=actor or scan.owner_id or scan.id,
        action="media_safety.scan.queued_for_review",
        target_type="MEDIA_SCAN",
        target_id=scan.id,
        metadata={
            "context": scan.context,
            "status": scan.status,
            "quarantine": scan.quarantine,
            "requires_review": scan.requires_review,
            "policy_version": scan.policy_version,
        },
        request=request,
    )


def apply_media_safety_action(scan: MediaSafetyScan, *, action: str, actor, notes: str = "", request=None) -> MediaSafetyScan:
    normalized = str(action or "").strip().lower()
    if normalized not in STAFF_ACTIONS:
        raise ValueError("Unknown moderation action.")

    result = scan.result if isinstance(scan.result, dict) else {}
    history = result.get("moderation_history") if isinstance(result.get("moderation_history"), list) else []
    history.append(
        {
            "action": normalized,
            "actor_id": str(getattr(actor, "id", actor)),
            "notes": notes[:2000],
            "at": timezone.now().isoformat(),
        }
    )
    result["moderation_history"] = history
    result["policy_version"] = scan.policy_version or EXPLICIT_CONTENT_POLICY_VERSION

    update_fields = ["result", "updated_at"]
    if normalized == "approve":
        scan.status = "passed"
        scan.quarantine = False
        scan.requires_review = False
        scan.reason = "staff_approved"
        update_fields.extend(["status", "quarantine", "requires_review", "reason"])
        if scan.asset_id:
            MediaAsset.objects.filter(id=scan.asset_id).update(status="ready", updated_at=timezone.now())
    elif normalized == "block":
        scan.status = "blocked"
        scan.quarantine = True
        scan.requires_review = False
        scan.reason = "staff_blocked"
        update_fields.extend(["status", "quarantine", "requires_review", "reason"])
        if scan.asset_id:
            MediaAsset.objects.filter(id=scan.asset_id).update(status="blocked", updated_at=timezone.now())
    elif normalized == "dismiss":
        scan.status = "not_configured" if scan.status == "pending_review" else scan.status
        scan.requires_review = False
        update_fields.extend(["status", "requires_review"])
    elif normalized == "escalate":
        scan.status = "pending_review"
        scan.quarantine = True
        scan.requires_review = True
        scan.reason = "staff_escalated"
        update_fields.extend(["status", "quarantine", "requires_review", "reason"])
    elif normalized in {"review", "note"}:
        scan.requires_review = True
        update_fields.append("requires_review")

    scan.result = result
    scan.save(update_fields=sorted(set(update_fields)))
    record_moderation_audit(
        actor=actor,
        action=f"media_safety.scan.{normalized}",
        target_type="MEDIA_SCAN",
        target_id=scan.id,
        metadata={
            "notes": notes[:2000],
            "status": scan.status,
            "quarantine": scan.quarantine,
            "requires_review": scan.requires_review,
            "context": scan.context,
        },
        request=request,
    )
    return scan

