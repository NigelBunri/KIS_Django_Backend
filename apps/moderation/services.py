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


class AppealNotSupported(Exception):
    """Raised when an appeal is attempted against a target_type/state this
    system doesn't yet know how to safely authorize or reverse."""


def authorize_appeal(target_type: str, target_id, appellant_id) -> None:
    """
    Verifies `appellant_id` is actually the party a moderation decision was
    made against, and that the decision has actually been actioned (no
    appealing something still pending review). Raises AppealNotSupported
    (→ 400) if the check can't be done at all, or ValueError (→ 400) if it
    can be done but fails - the view maps these to distinct error messages.
    """
    appellant_id = str(appellant_id)

    if target_type == "flag":
        try:
            flag = models.Flag.objects.get(id=target_id)
        except models.Flag.DoesNotExist:
            raise ValueError("Flag not found.")
        # Only target_type=USER flags have an unambiguous affected party
        # (target_id IS the user). A flag against a POST/COMMENT/CHANNEL/
        # GROUP/STATUS doesn't reliably resolve to a single content owner
        # across every one of those apps, so appeal isn't offered for
        # those yet rather than guessing who's allowed to contest it.
        if flag.target_type != "USER":
            raise AppealNotSupported("Appeals are only supported for flags against a user account.")
        if str(flag.target_id) != appellant_id:
            raise ValueError("You can only appeal a decision made against you.")
        if flag.status != "ACTIONED":
            raise ValueError("This decision hasn't been actioned yet, so there's nothing to appeal.")
        return

    if target_type == "media_safety_scan":
        try:
            scan = MediaSafetyScan.objects.get(id=target_id)
        except MediaSafetyScan.DoesNotExist:
            raise ValueError("Media safety scan not found.")
        if str(scan.owner_id) != appellant_id:
            raise ValueError("You can only appeal a decision made against you.")
        if scan.status not in {"blocked", "failed"}:
            raise ValueError("This upload hasn't been blocked, so there's nothing to appeal.")
        return

    if target_type == "channel_moderation_record":
        from apps.broadcasts.models import ChannelModerationRecord

        try:
            record = ChannelModerationRecord.objects.select_related(
                "content", "comment", "channel"
            ).get(id=target_id)
        except ChannelModerationRecord.DoesNotExist:
            raise ValueError("Channel moderation record not found.")
        if record.status != "actioned":
            raise ValueError("This decision hasn't been actioned yet, so there's nothing to appeal.")

        affected_id = None
        if record.target_type == "comment" and record.comment_id:
            affected_id = str(record.comment.user_id)
        elif record.target_type in {"content", "channel"}:
            channel = record.content.channel if record.content_id else record.channel
            # Partner-owned channels have no single accountable user to
            # authorize the appeal as - who on the partner team may appeal
            # on the org's behalf is a real RBAC question this doesn't
            # attempt to answer yet.
            if channel.owner_type == "user" and channel.owner_user_id:
                affected_id = str(channel.owner_user_id)
        if affected_id is None:
            raise AppealNotSupported("Appeals for this content's ownership type aren't supported yet.")
        if affected_id != appellant_id:
            raise ValueError("You can only appeal a decision made against you.")
        return

    if target_type == "chat_message_report":
        # ChatMessageReport doesn't record who SENT the reported message
        # (only who reported it) - see the model's docstring. Without that,
        # there's no way to verify an appellant is the affected party.
        raise AppealNotSupported("Appeals for chat message reports aren't supported yet.")

    raise AppealNotSupported("Unsupported appeal target type.")


def _reverse_strike_for_user(user_id, *, flag=None) -> bool:
    """
    Undoes exactly one apply_ai_flag_consequence strike: lifts an
    auto-suspension if the user is currently suspended, decrements the
    UserReputation counters that strike incremented, and records a
    REINSTATE ModerationAction when there's a Flag to attach it to (the FK
    is required, so a strike with no discoverable Flag - shouldn't happen
    in practice, but isn't guaranteed - just skips that one record rather
    than crashing the whole reversal).

    Returns whether a real reversal happened (i.e. the user was actually
    suspended) - overturning an appeal for a WARN-level strike still
    decrements the counters below but there's no suspension to lift, so
    the caller can report that honestly rather than implying a suspension
    was undone when none existed.
    """
    from apps.accounts.models import User  # local import: avoids a
    # moderation<->accounts import cycle at module load time.

    user = User.objects.filter(id=user_id).first()
    if user is None:
        return False

    was_suspended = user.status == "suspended"
    if was_suspended:
        user.status = "active"
        user.is_active = True
        user.save(update_fields=["status", "is_active"])

    models.UserReputation.objects.filter(user_id=user_id).update(
        flags_received=F("flags_received") - 1,
        actions_taken=F("actions_taken") - 1,
    )

    if flag is not None:
        models.ModerationAction.objects.create(
            flag=flag,
            action="REINSTATE",
            notes="Reversed via appeal overturn.",
            performed_by_id=SYSTEM_ACTOR_ID,
            auto_generated=True,
        )

    return was_suspended


def decide_appeal(appeal: "models.ModerationAppeal", *, decision: str, actor, notes: str = "") -> dict[str, Any]:
    """
    Resolves a ModerationAppeal. "uphold" just records the decision.
    "overturn" additionally attempts a REAL reversal of the original
    consequence, per target_type:
      - flag (target_type=USER only - see the view's authorization check):
        lifts a suspension and undoes the strike via _reverse_strike_for_user.
      - media_safety_scan: reuses apply_media_safety_action's own "approve"
        path (restores the MediaAsset to ready), then also reverses any
        strike that scan's confirmed-violation triggered.
      - channel_moderation_record: restores the soft-deleted content/
        comment to public/published - not a full replay of whatever
        visibility it had before moderation, just the honest default of
        "visible again."
      - chat_message_report: status-only. The reported message's content
        was already destructively scrubbed (Mongo $unset, same as a normal
        user delete-for-everyone) - there is nothing left to restore, so
        this never claims reversal_applied=True.

    Returns {"status": ..., "reversal_applied": bool} - the view uses this
    to fill in ModerationAppeal.status/reversal_applied honestly rather
    than assuming overturn always means something was actually undone.
    """
    now = timezone.now()
    reversal_applied = False

    if decision == "overturn":
        if appeal.target_type == "flag":
            flag = models.Flag.objects.get(id=appeal.target_id)
            reversal_applied = _reverse_strike_for_user(flag.target_id, flag=flag)
            flag.status = "DISMISSED"
            flag.resolved_at = now
            flag.save(update_fields=["status", "resolved_at", "updated_at"])

        elif appeal.target_type == "media_safety_scan":
            scan = MediaSafetyScan.objects.get(id=appeal.target_id)
            apply_media_safety_action(scan, action="approve", actor=actor, notes=notes)
            related_flag = models.Flag.objects.filter(target_type="POST", target_id=scan.id).first()
            strike_reversed = _reverse_strike_for_user(scan.owner_id, flag=related_flag)
            reversal_applied = True  # the scan/asset restoration itself always applies
            _ = strike_reversed  # counted separately in audit metadata by the caller if needed

        elif appeal.target_type == "channel_moderation_record":
            from apps.broadcasts.models import ChannelContent, ChannelContentComment, ChannelModerationRecord

            record = ChannelModerationRecord.objects.select_related("content", "comment").get(id=appeal.target_id)
            if record.comment_id:
                ChannelContentComment.objects.filter(id=record.comment_id).update(is_deleted=False)
                reversal_applied = True
            if record.content_id:
                ChannelContent.objects.filter(id=record.content_id).update(
                    is_deleted=False, visibility="public", status="published",
                )
                reversal_applied = True
            record.status = "dismissed"
            record.action = "keep"
            record.resolved_at = now
            record.save(update_fields=["status", "action", "resolved_at", "updated_at"])

        elif appeal.target_type == "chat_message_report":
            # No sender identity recorded (see ModerationAppeal's docstring)
            # and the message content is already irreversibly scrubbed -
            # nothing to restore. Status-only.
            report = models.ChatMessageReport.objects.get(id=appeal.target_id)
            report.status = "DISMISSED"
            report.resolved_at = now
            report.save(update_fields=["status", "resolved_at", "updated_at"])

    appeal.status = "OVERTURNED" if decision == "overturn" else "UPHELD"
    appeal.decided_by_id = getattr(actor, "id", actor)
    appeal.decision_notes = notes[:2000]
    appeal.decided_at = now
    appeal.reversal_applied = reversal_applied
    appeal.save(update_fields=["status", "decided_by_id", "decision_notes", "decided_at", "reversal_applied", "updated_at"])

    return {"status": appeal.status, "reversal_applied": reversal_applied}

