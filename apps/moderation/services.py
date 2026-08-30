from __future__ import annotations

import uuid
from typing import Any

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from apps.media.models import MediaAsset, MediaSafetyScan
from apps.media.safety import EXPLICIT_CONTENT_POLICY_VERSION

from . import models


STAFF_ACTIONS = {"approve", "block", "dismiss", "escalate", "review", "note"}

# Reserved actor id for automated system actions (ModerationAction.
# performed_by_id has no FK constraint — it's a bare UUIDField — so this
# never needs to correspond to a real user row). Distinguishing this from a
# real GO/staff id matters for audit trails: "the system did this on its
# own" vs. "a human reviewed and did this."
SYSTEM_ACTOR_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")

# 5 warnings, 6th violation = auto-suspend. Matches the platform's published
# community-standards escalation: repeated confirmed violations, not a single
# uncertain flag, are what triggers account-level consequences.
STRIKES_BEFORE_SUSPENSION = 6


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


def apply_ai_flag_consequence(scan: MediaSafetyScan, flag: "models.Flag | None") -> None:
    """Applies the escalating warn -> auto-suspend consequence for a
    CONFIRMED explicit-content violation — either an AI auto-block at high
    confidence (see run_nudenet_scan_on_file's threshold), or a staff member
    manually confirming a low-confidence flag via apply_media_safety_action's
    "block" action. Deliberately NOT called for merely-uncertain
    pending_review scans — an unconfirmed flag shouldn't cost a user a
    strike before a human (or a confident model) has actually decided it's a
    real violation.

    Uses UserReputation.flags_received as the strike counter — that field
    already existed for exactly this purpose but nothing was incrementing
    it before now.
    """
    if not scan.owner_id:
        return

    from apps.accounts.models import User  # local import: avoids a
    # moderation<->accounts import cycle at module load time.

    reputation, _ = models.UserReputation.objects.get_or_create(user_id=scan.owner_id)
    models.UserReputation.objects.filter(id=reputation.id).update(
        flags_received=F("flags_received") + 1,
        actions_taken=F("actions_taken") + 1,
        last_updated=timezone.now(),
    )
    reputation.refresh_from_db()
    strike_number = reputation.flags_received

    is_suspension_strike = strike_number >= STRIKES_BEFORE_SUSPENSION

    models.ModerationAction.objects.create(
        flag=flag,
        action="SUSPEND" if is_suspension_strike else "WARN",
        notes=f"Automated explicit-content violation, strike {strike_number}/{STRIKES_BEFORE_SUSPENSION}.",
        performed_by_id=SYSTEM_ACTOR_ID,
        auto_generated=True,
    )

    if flag is not None and is_suspension_strike:
        # Bump this to the front of GO's review queue — an auto-suspended
        # account needs a human decision (uphold, unsuspend, escalate to
        # ban/delete), not just a passive audit trail entry.
        models.Flag.objects.filter(id=flag.id).update(escalation_level="ADMIN")

    user = User.objects.filter(id=scan.owner_id).first()
    if user is None:
        return

    remaining = max(0, STRIKES_BEFORE_SUSPENSION - strike_number)
    if is_suspension_strike:
        user.status = "suspended"
        user.is_active = False
        user.save(update_fields=["status", "is_active"])
        title = "Your KIS account has been suspended"
        body = (
            "Your account was automatically suspended after repeated uploads that "
            "violated KIS's family-safety standards. This decision will be reviewed "
            "by our team."
        )
        notif_type = "MODERATION_SUSPENSION"
    else:
        title = "Content removed — community guidelines warning"
        body = (
            f"Something you uploaded was removed for violating KIS's family-safety "
            f"standards. This is warning {strike_number} of {STRIKES_BEFORE_SUSPENSION - 1} — "
            f"after {remaining} more violation{'s' if remaining != 1 else ''}, your account "
            "will be automatically suspended."
        )
        notif_type = "MODERATION_WARNING"

    try:
        from apps.notifications.services import create_notification

        create_notification(
            user_id=user.id,
            type=notif_type,
            title=title,
            body=body,
            priority="HIGH",
            channels=["IN_APP", "PUSH"],
        )
    except Exception:
        # Never let a notification-delivery failure block the takedown/
        # suspension itself — those already happened above.
        pass


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

    # High-confidence AI auto-block is a CONFIRMED violation (see
    # run_nudenet_scan_on_file's threshold) — apply the strike immediately,
    # don't wait for a human to also confirm what the model already flagged
    # with high confidence. Low-confidence pending_review scans do NOT reach
    # here — those wait for apply_media_safety_action's manual "block".
    if scan.status == "blocked":
        apply_ai_flag_consequence(scan, flag)


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

    if normalized == "block":
        # A human just confirmed what was previously only an uncertain
        # pending_review flag — this is now a CONFIRMED violation, so it
        # applies a strike the same as a high-confidence AI auto-block does.
        flag = models.Flag.objects.filter(target_type="POST", target_id=scan.id).first()
        apply_ai_flag_consequence(scan, flag)

    return scan

