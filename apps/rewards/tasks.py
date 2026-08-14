# rewards/tasks.py
#
# Thin @shared_task wrappers around real, independently testable
# services.py functions — matching the established house pattern
# (apps.billing.tasks / apps.media.tasks). Registered in
# CELERY_BEAT_SCHEDULE, config/settings/base.py.
from __future__ import annotations

from celery import shared_task

from .services import expire_reward_entries, reconcile_rewards_and_referrals


@shared_task
def expire_reward_ledger_entries():
    """Periodic sweep for RewardLedgerEntry rows past their expires_at that
    are still CONFIRMED (earned but never spent). See
    apps.rewards.services.expire_reward_entries for what this actually
    does; apps/rewards/management/commands/expire_reward_entries.py wraps
    the same function for manual/cron use where Beat isn't configured."""
    return expire_reward_entries()


@shared_task
def reconcile_rewards_and_referrals_task():
    """Periodic read-only consistency check across Referral/
    RewardLedgerEntry/WalletTransaction — see
    apps.rewards.services.reconcile_rewards_and_referrals. Never writes
    anything; anomalies are logged (logger.warning) and returned for
    whatever log/alerting pipeline is watching this task's result."""
    return reconcile_rewards_and_referrals()
