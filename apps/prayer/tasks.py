# apps/prayer/tasks.py
from __future__ import annotations

import datetime

from celery import shared_task
from django.utils import timezone

REMINDER_WINDOW_MINUTES = 10


@shared_task
def send_upcoming_watch_reminders() -> dict:
    """Runs every few minutes (see CELERY_BEAT_SCHEDULE) — finds the one
    watch whose session is starting within the next
    REMINDER_WINDOW_MINUTES and sends an optional reminder to every
    member who hasn't muted that watch's conversation. Safe against
    duplicate sends (create_notification's dedup_key, keyed on this
    specific session+user) even if this task's own schedule overlaps a
    run or is delayed into the same window twice."""
    from apps.groups.models import GroupMembership

    from . import services
    from .models import PrayerWatchGroup
    from .notifications import send_upcoming_watch_reminder

    now = timezone.now()
    window_end = now + datetime.timedelta(minutes=REMINDER_WINDOW_MINUTES)
    notified = 0
    watches_matched = []

    for watch_group in PrayerWatchGroup.objects.select_related("group").all():
        session = services.get_or_create_session_for_watch(watch_group, at=now)
        if not (now <= session.scheduled_start < window_end):
            continue
        watches_matched.append(watch_group.hour)
        members = GroupMembership.objects.filter(
            group=watch_group.group, left_at__isnull=True, is_banned=False,
        ).select_related("user")
        for membership in members:
            send_upcoming_watch_reminder(session=session, user=membership.user)
            notified += 1

    return {"watches_matched": watches_matched, "notified": notified}
