from __future__ import annotations

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from .models import DailyFeedUsage, FeedEngagementState


def feed_usage_status(seconds_consumed: int) -> dict:
    limit = settings.RESPONSIBLE_FEED_DAILY_LIMIT_SECONDS
    seconds_consumed = max(0, int(seconds_consumed))
    return {
        "seconds_consumed": seconds_consumed,
        "limit_seconds": limit,
        "seconds_remaining": max(0, limit - seconds_consumed),
        "limit_reached": seconds_consumed >= limit,
    }


def get_today_feed_status(user) -> dict:
    today = timezone.now().date()
    usage = DailyFeedUsage.objects.filter(user=user, date=today).first()
    return feed_usage_status(usage.seconds_consumed if usage else 0)


def is_feed_limit_reached(user) -> bool:
    return get_today_feed_status(user)["limit_reached"]


def record_feed_heartbeat(user) -> dict:
    """
    Called by the client roughly every 15-30s while the passive feed
    screen is actively on-screen and scrolling. Deliberately ignores
    whatever elapsed-time value (if any) the client sends - the only
    thing that matters is that a request arrived NOW, per the server's own
    clock. The credited elapsed time is the gap between this request and
    the account's last recorded heartbeat, clamped to
    RESPONSIBLE_FEED_MAX_HEARTBEAT_GAP_SECONDS so a stale/backgrounded
    client resuming after a long gap doesn't get credited for time it
    wasn't actually viewing the feed.
    """
    now = timezone.now()
    today = now.date()

    state, _ = FeedEngagementState.objects.get_or_create(user=user)
    elapsed_seconds = 0
    if state.last_heartbeat_at is not None and state.last_heartbeat_at <= now:
        gap_seconds = (now - state.last_heartbeat_at).total_seconds()
        elapsed_seconds = max(0, min(int(gap_seconds), settings.RESPONSIBLE_FEED_MAX_HEARTBEAT_GAP_SECONDS))
    state.last_heartbeat_at = now
    state.save(update_fields=["last_heartbeat_at", "updated_at"])

    usage, _ = DailyFeedUsage.objects.get_or_create(user=user, date=today)
    if elapsed_seconds:
        DailyFeedUsage.objects.filter(id=usage.id).update(
            seconds_consumed=F("seconds_consumed") + elapsed_seconds, updated_at=now,
        )
        usage.refresh_from_db(fields=["seconds_consumed"])

    return feed_usage_status(usage.seconds_consumed)
