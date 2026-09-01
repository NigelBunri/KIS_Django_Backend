# testimony/tasks.py
#
# Thin @shared_task wrapper around a real, independently testable
# function - matching the established house pattern (apps.rewards.tasks /
# apps.billing.tasks). Registered in CELERY_BEAT_SCHEDULE,
# config/settings/base.py.
from __future__ import annotations

from celery import shared_task
from django.utils import timezone

from .models import UserTestimony


def expire_stale_testimonies() -> int:
    """
    14-day testimony expiration (TESTIMONY_LIFETIME_DAYS). Unpublishes,
    does not delete - flips is_available to False and stamps expired_at,
    the same "unbroadcast not delete" pattern apps.broadcasts.BroadcastItem
    uses. The story/endorsements stay fully intact; TestimonyDetailView.
    perform_update gives a testimony a fresh expires_at if the author
    turns is_available back on.
    """
    now = timezone.now()
    updated = UserTestimony.objects.filter(
        is_available=True, expires_at__lte=now,
    ).update(is_available=False, expired_at=now)
    return updated


@shared_task
def expire_stale_testimonies_task():
    return {"expired": expire_stale_testimonies()}
