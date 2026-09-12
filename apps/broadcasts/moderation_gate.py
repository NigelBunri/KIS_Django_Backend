"""
The single authoritative "may this go out to the public" rule for
BroadcastVideo, per an explicit product requirement: an AI content-safety
scan alone must NEVER be sufficient for public distribution, and a human
PASS is never permanent - it must be revalidated periodically or the
content silently loses eligibility again.

BROADCAST_ALLOWED = True only when moderation_status == PASSED AND
moderation_expires_at is set AND still in the future. Every other state
(PENDING_REVIEW, BLOCKED, an expired PASSED, or a PASSED with no
expiry at all - which should never happen once set_pass() is used, but
is treated as ineligible rather than trusted if it somehow does) is
NOT BROADCASTABLE. Fail closed, not fail open.

Used from exactly two places by design, so there is one gate, not several
copies of the same condition that could drift apart:
  - BroadcastVideoListView (queryset-level filter - what the public feed
    can even see)
  - BroadcastVideoStreamView (row-level re-check at request time - closes
    the gap between "was eligible when the feed was cached/rendered" and
    "is eligible right now", e.g. an approval expiring between the two)
"""
from __future__ import annotations

from django.db.models import QuerySet
from django.utils import timezone


def is_broadcast_eligible(video) -> bool:
    if video.moderation_status != video.ModerationStatus.PASSED:
        return False
    if not video.moderation_expires_at:
        return False
    return video.moderation_expires_at > timezone.now()


def filter_broadcast_eligible(queryset: QuerySet) -> QuerySet:
    """Same rule as is_broadcast_eligible(), expressed as a queryset filter
    for listing endpoints - avoids loading every candidate row into Python
    just to check eligibility one at a time."""
    from .models import BroadcastVideo

    now = timezone.now()
    return queryset.filter(
        moderation_status=BroadcastVideo.ModerationStatus.PASSED,
        moderation_expires_at__gt=now,
    )


def apply_moderation_decision(video, *, action: str, actor, notes: str = "") -> None:
    """Applies a human moderator's decision. `action` is one of
    "pass" | "pending" | "block" | "delete". Deletion is a soft-delete
    (is_active=False), matching this model's existing visibility
    convention rather than introducing a second one."""
    from django.conf import settings
    from datetime import timedelta

    from .models import BroadcastVideo

    now = timezone.now()
    if action == "pass":
        video.moderation_status = BroadcastVideo.ModerationStatus.PASSED
        video.moderation_passed_at = now
        window_days = int(getattr(settings, "BROADCAST_MODERATION_REVALIDATION_DAYS", 90))
        video.moderation_expires_at = now + timedelta(days=window_days)
        video.moderation_reviewed_by = actor
        video.is_active = True
        video.save(update_fields=[
            "moderation_status", "moderation_passed_at", "moderation_expires_at",
            "moderation_reviewed_by", "is_active", "updated_at",
        ])
    elif action == "pending":
        video.moderation_status = BroadcastVideo.ModerationStatus.PENDING_REVIEW
        video.moderation_reviewed_by = actor
        video.save(update_fields=["moderation_status", "moderation_reviewed_by", "updated_at"])
    elif action == "block":
        video.moderation_status = BroadcastVideo.ModerationStatus.BLOCKED
        video.moderation_reviewed_by = actor
        video.is_active = False
        video.save(update_fields=["moderation_status", "moderation_reviewed_by", "is_active", "updated_at"])
    elif action == "delete":
        video.moderation_status = BroadcastVideo.ModerationStatus.DELETED
        video.moderation_reviewed_by = actor
        video.is_active = False
        video.save(update_fields=["moderation_status", "moderation_reviewed_by", "is_active", "updated_at"])
    else:
        raise ValueError(f"Unknown moderation action: {action!r}")


# target_type values (apps.media.tasks.ContentSafetyResolutionTarget) this
# module knows how to resolve to a real model instance. Deliberately narrow
# - apps.moderation.services and admin_control both dispatch through
# resolve_and_apply_moderation_decision() below rather than each knowing how
# to look up a BroadcastVideo directly, so adding a second content type
# later (education_material, status_item, ...) only means adding one entry
# here, not touching every caller.
MODERATABLE_TARGET_TYPES = {"broadcast_video"}


def resolve_and_apply_moderation_decision(target_type: str, target_id, *, action: str, actor, notes: str = "") -> bool:
    """Resolves target_type/target_id (the same resolution_target/
    resolution_id convention already stored on MediaSafetyScan.result by
    apps.media.tasks) to a real content row and applies a moderation
    decision to it. Returns False (no-op) for an unknown target_type or a
    target_id that no longer resolves, rather than raising - a scan whose
    underlying content was already hard-deleted by the 24h sweep is a
    normal, expected state to encounter here, not an error."""
    if target_type == "broadcast_video":
        from .models import BroadcastVideo

        video = BroadcastVideo.objects.filter(id=target_id).first()
        if video is None:
            return False
        apply_moderation_decision(video, action=action, actor=actor, notes=notes)
        return True
    return False


def apply_ai_block_takedown(target_type: str, target_id) -> bool:
    """A definitive AI BLOCKED verdict is immediately sufficient to take
    content offline on its own - unlike every other transition, this one
    deliberately does NOT require a human actor. Reuses the same "block"
    application as a manual admin block (moderation_status=BLOCKED,
    is_active=False) so there is exactly one code path for "this content
    is blocked", whether AI or a human decided it."""
    return resolve_and_apply_moderation_decision(target_type, target_id, action="block", actor=None, notes="ai_auto_block")
