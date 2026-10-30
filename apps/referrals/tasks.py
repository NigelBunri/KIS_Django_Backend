# referrals/tasks.py
#
# Thin @shared_task wrapper around a real, independently testable
# services.py function — matching the established house pattern
# (apps.billing.tasks / apps.media.tasks). Registered in
# CELERY_BEAT_SCHEDULE, config/settings/base.py.
from __future__ import annotations

from celery import shared_task

from .commission_services import sweep_approvable_referral_commissions, sweep_payable_referral_commissions
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


@shared_task
def approve_referral_commissions():
    """Periodic sweep for the lifetime cash-commission track (separate
    from confirm_settled_referrals above — see
    apps.referrals.commission_services for why). PENDING -> APPROVED once
    REFERRAL_COMMISSION_SETTLEMENT_WINDOW_DAYS has elapsed."""
    return sweep_approvable_referral_commissions()


@shared_task
def pay_out_referral_commissions():
    """Periodic sweep that attempts a real payout for every APPROVED
    commission whose referrer has an ACTIVE payout account. See
    apps.referrals.commission_services.sweep_payable_referral_commissions
    / initiate_commission_payout for what "attempts" actually means per
    provider (Stripe settles synchronously here; Flutterwave stays
    APPROVED pending its own transfer.completed webhook)."""
    return sweep_payable_referral_commissions()
