# referrals/tasks.py
#
# Thin @shared_task wrapper around a real, independently testable
# services.py function — matching the established house pattern
# (apps.billing.tasks / apps.media.tasks). Registered in
# CELERY_BEAT_SCHEDULE, config/settings/base.py.
from __future__ import annotations

from celery import shared_task

from .services import sweep_settleable_referrals


@shared_task
def confirm_settled_referrals():
    """Periodic sweep for Referral rows that are QUALIFIED and past
    REFERRAL_SETTLEMENT_WINDOW_DAYS, settling each via the existing
    confirm_referral_reward(). See
    apps.referrals.services.sweep_settleable_referrals for what this
    actually does; apps/referrals/management/commands/
    confirm_settled_referrals.py wraps the same function for manual/cron
    use where Beat isn't configured."""
    return sweep_settleable_referrals()
