# apps/prayer/notifications.py
"""Thin wrappers around apps.notifications.services.create_notification
for the Prayer Calendar's own event types. Every call is best-effort —
wrapped in try/except by the caller (never let a notification failure
roll back a real leadership transition) — and every call that could
plausibly repeat carries a dedup_key, so a retried request or a Celery
task re-run can never double-notify.

'System-critical leadership notifications must not be silently
suppressed by ordinary group mute settings' (spec section 12): these
calls go straight to create_notification, which has no awareness of
GroupMembership.is_muted at all — that flag only ever gates in-
conversation delivery (apps.chat), never this notification pipeline —
so a muted prayer-group conversation still lets its leadership
notifications through. Only the optional upcoming-watch REMINDER
(send_upcoming_watch_reminder) deliberately checks is_muted, since that
one is the explicitly-optional, opt-outable kind the spec distinguishes
from the critical ones above it.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _notify(user_id, type_, title: str, body: str, dedup_key: str, **extra) -> None:
    try:
        from apps.notifications.services import create_notification
        create_notification(
            user_id=user_id, type=type_, title=title, body=body, dedup_key=dedup_key, **extra,
        )
    except Exception:
        logger.exception("[prayer.notifications] failed to send %s to user_id=%s", type_, user_id)


def notify_leadership_claimed(*, session, user) -> None:
    _notify(
        user.id, "prayer.leadership_claimed",
        title="You're leading a prayer watch",
        body=f"You are now leading {session.watch_group.name}.",
        dedup_key=f"prayer-leadership-claimed:{session.id}:{user.id}",
        target_type="prayer_session", target_id=str(session.id),
    )


def notify_share_requested(*, session, admin_user, member_user) -> None:
    _notify(
        member_user.id, "prayer.share_requested",
        title="An admin wants to share your watch",
        body=f"{getattr(admin_user, 'display_name', '') or 'An admin'} asked to share {session.watch_group.name} with you.",
        dedup_key=f"prayer-share-requested:{session.id}:{admin_user.id}",
        target_type="prayer_session", target_id=str(session.id), actor_user_id=admin_user.id,
    )


def notify_share_responded(*, session, admin_user, member_user, accepted: bool) -> None:
    _notify(
        admin_user.id, "prayer.share_responded",
        title="Your share request was accepted" if accepted else "Your share request was declined",
        body=f"{getattr(member_user, 'display_name', '') or 'The leader'} {'accepted' if accepted else 'declined'} your request to share {session.watch_group.name}.",
        dedup_key=f"prayer-share-responded:{session.id}:{admin_user.id}",
        target_type="prayer_session", target_id=str(session.id), actor_user_id=member_user.id,
    )


def notify_go_override(*, session, new_leader) -> None:
    if not new_leader:
        return
    _notify(
        new_leader.id, "prayer.go_override",
        title="You've been assigned to lead a prayer watch",
        body=f"The GO has assigned you to lead {session.watch_group.name}.",
        dedup_key=f"prayer-go-override:{session.id}:{new_leader.id}",
        target_type="prayer_session", target_id=str(session.id),
    )


def send_upcoming_watch_reminder(*, session, user) -> None:
    """The one genuinely optional notification type — gated on
    GroupMembership.is_muted specifically because this is the kind of
    reminder the spec says users must be able to opt out of, unlike the
    leadership-critical ones above. 'Opted in' here is modeled as
    'joined the watch and hasn't muted it' — joining is already an
    explicit action, and un-muting is the existing, familiar lever KIS
    users already have for every other conversation."""
    from apps.groups.models import GroupMembership

    is_muted = GroupMembership.objects.filter(
        group=session.watch_group.group, user=user, left_at__isnull=True, is_muted=True,
    ).exists()
    if is_muted:
        return
    _notify(
        user.id, "prayer.watch_starting_soon",
        title="A prayer watch is starting soon",
        body=f"{session.watch_group.name} begins in a few minutes.",
        dedup_key=f"prayer-reminder:{session.id}:{user.id}",
        target_type="prayer_session", target_id=str(session.id),
    )
