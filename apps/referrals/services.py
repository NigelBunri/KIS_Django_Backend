from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import AccountTier, AuditLog, Device, Subscription, User
from apps.rewards.models import RewardLedgerEntry
from apps.rewards.services import confirm_ledger_entry, create_pending_entry, reverse_ledger_entry

from .models import Referral, ReferralCode, ReferralRateConfig

logger = logging.getLogger(__name__)

# Matches the value already advertised to users in apps.commerce.views
# POINT_EARNING_RULES ("invite_friend") and the mobile wallet UI's own
# fallback copy — kept as a single source of truth here so a future change
# to the payout amount only needs to happen in one place; the advertised
# copy should be updated to read from this same value.
REFERRAL_REWARD_POINTS = 200


def _device_already_linked_to_referrer(referrer: User, device_id: str) -> bool:
    if not device_id:
        return False
    return Device.objects.filter(user=referrer, device_id=device_id).exists()


def register_referral(*, referred_user: User, referral_code: str, device_id: str = "") -> Referral | None:
    """
    Called once, at registration time, if the new user supplied a
    referral_code. Returns None (no-op) for a blank/unknown code — an
    invalid code must never block registration itself.

    Creates the Referral row in STATUS_PENDING, or straight to
    STATUS_BLOCKED if the new account's device_id is already linked to the
    referrer's own account (the most common referral-farming pattern: one
    person registering several throwaway accounts from the same device to
    repeatedly reward themselves).
    """
    code = (referral_code or "").strip().upper()
    if not code:
        return None

    code_record = ReferralCode.objects.select_related("user").filter(code=code).first()
    if not code_record:
        return None

    referrer = code_record.user
    if referrer.id == referred_user.id:
        return None

    status = Referral.STATUS_PENDING
    block_reason = ""
    if _device_already_linked_to_referrer(referrer, device_id):
        status = Referral.STATUS_BLOCKED
        block_reason = "referred_device_already_linked_to_referrer"

    referral = Referral.objects.create(
        referrer=referrer,
        referred_user=referred_user,
        referral_code_used=code,
        status=status,
        block_reason=block_reason,
    )
    AuditLog.log(
        referred_user,
        "referral.created",
        {
            "referral_id": str(referral.id),
            "referrer_id": str(referrer.id),
            "status": status,
        },
    )
    return referral


def apply_referral_reward_if_pending(referred_user: User) -> None:
    """
    RETIRED (pre-deployment hardening pass, billing/rewards project). This
    used to be the legacy reward-granting transition, called at account
    activation — before any qualifying payment ever happened. Because it
    shared the same STATUS_PENDING guard as qualify_referral(), it always
    won the race (activation always precedes any possible payment),
    permanently blocking the new tier-aware, payment-gated engine from
    ever qualifying that referral, and paid a flat, unconditional reward
    for a free registration with no revenue behind it — a real reward-
    farming exposure, not just redundant with the new engine.

    Retired as a hard no-op rather than merely un-called, so it remains
    structurally incapable of paying a reward even if some future change
    accidentally reintroduces a call to it. There is now exactly ONE
    authoritative referral reward engine: qualify_referral() (fired only
    from a genuine qualifying payment) -> sweep_settleable_referrals() /
    confirm_referral_reward() (settlement, after REFERRAL_SETTLEMENT_WINDOW_DAYS).

    Historical Referral rows already REWARDED by this function, and their
    corresponding legacy LoyaltyPoint rows, are untouched and remain a
    valid, permanent audit trail — this retirement does not rewrite the
    past, only prevents any future call from acting.
    """
    logger.warning(
        "apply_referral_reward_if_pending was called but is retired (no-op) — "
        "referrals are now only rewarded via qualify_referral/confirm_referral_reward. "
        "referred_user_id=%s",
        getattr(referred_user, "id", None),
    )
    return None


# ---------------------------------------------------------------------
# Qualification-based referral engine (new — additive to the legacy flat-
# 200-points-at-activation path above, which is untouched and keeps working
# as-is until Phase 5 wires this engine into the real payment webhook).
# ---------------------------------------------------------------------

# Only these WalletTransaction/apply_tier_upgrade `source` values represent
# genuine new external revenue. "free", "admin_grant", "credits", "mock",
# and "wallet" (paying with an existing internal balance, not new money)
# must never trigger a referral qualification — there's no real economic
# event to reward a referral for.
QUALIFYING_PAYMENT_SOURCES = frozenset({"flutterwave", "stripe"})

# Provisional: 1 KIS Coin per whole cent of qualifying net revenue
# attributed to the referrer, pending the same real coin-valuation business
# decision flagged on RedemptionPolicy.coin_value_cents (Phase 1 report,
# §16 item 5). Change this single constant, not call sites, once a real
# ratio is decided.
_PROVISIONAL_CENTS_TO_COINS_RATE = Decimal("1")


# Live source of truth for referral rates — mirrors the exact pattern
# apps.accounts.tier_presets.TIER_PRESETS + ensure_default_account_tiers()
# already establishes for AccountTier itself: a plain constant plus a
# self-healing function, reused by the `seed_referral_rates` management
# command. The one-time data migration (0003_seed_referral_rate_config) has
# its own frozen snapshot of this same mapping, matching this codebase's
# existing "migration is a point-in-time seed, the service function/command
# is the living, re-runnable tool" convention — intentional duplication of a
# small dict, not drift risk, since AccountTier rows may not exist yet at
# migration time (self-healed lazily on first /api/v1/tiers/ access) and
# this needs to be re-runnable whenever they do.
RATE_PERCENT_BY_TIER_RANK = {
    0: Decimal("2.00"),
    1: Decimal("5.00"),
    2: Decimal("8.00"),
    3: Decimal("10.00"),
    4: Decimal("15.00"),
    5: Decimal("20.00"),
}


def ensure_referral_rate_configs() -> dict:
    """
    Idempotent, self-healing: creates or corrects a ReferralRateConfig row
    for every AccountTier whose rank has an entry in
    RATE_PERCENT_BY_TIER_RANK. Safe to call any number of times (e.g. from
    the seed_referral_rates management command, or after seed_tiers has
    just created AccountTier rows that didn't exist yet).
    """
    created = 0
    updated = 0
    unchanged = 0
    for tier in AccountTier.objects.filter(rank__in=RATE_PERCENT_BY_TIER_RANK.keys()):
        rate_percent = RATE_PERCENT_BY_TIER_RANK[tier.rank]
        config, was_created = ReferralRateConfig.objects.get_or_create(
            tier=tier, defaults={"rate_percent": rate_percent, "is_active": True},
        )
        if was_created:
            created += 1
        elif config.rate_percent != rate_percent or not config.is_active:
            config.rate_percent = rate_percent
            config.is_active = True
            config.save(update_fields=["rate_percent", "is_active", "updated_at"])
            updated += 1
        else:
            unchanged += 1
    return {"created": created, "updated": updated, "unchanged": unchanged}


def get_current_tier(user: User) -> AccountTier | None:
    sub = (
        Subscription.objects.filter(user=user, status=Subscription.STATUS_ACTIVE)
        .select_related("tier")
        .first()
    )
    if sub and sub.tier:
        return sub.tier
    return AccountTier.objects.filter(name__iexact=user.tier).first()


def get_referral_rate_percent(tier: AccountTier | None) -> Decimal:
    """Returns 0 (never guesses a rate) if the tier is unset or has no
    active ReferralRateConfig — a missing config is an operational gap to
    surface via the caller's audit log, not paper over with a default."""
    if not tier:
        return Decimal("0")
    config = ReferralRateConfig.objects.filter(tier=tier, is_active=True).first()
    if not config:
        return Decimal("0")
    return config.rate_percent


def qualify_referral(referred_user: User, subscription: Subscription, net_amount_cents: int) -> Referral | None:
    """
    The qualification transition: PENDING -> QUALIFIED. Called when the
    referred user's subscription payment actually succeeds (Phase 5 wires
    this to the real payment webhook — not called from anywhere live yet).

    Idempotent and concurrency-safe the same way apply_referral_reward_if_pending
    is: only acts on a Referral still in STATUS_PENDING, under a row lock —
    a second call (retried webhook, or a genuinely concurrent one) finds no
    matching PENDING row once the first has committed and cleanly no-ops.

    No-ops (returns None) if: there's no pending Referral for this user, or
    the referrer's current tier has no active ReferralRateConfig (logged,
    not guessed), or the computed reward would be zero.
    """
    with transaction.atomic():
        referral = (
            Referral.objects.select_for_update()
            .filter(referred_user=referred_user, status=Referral.STATUS_PENDING)
            .first()
        )
        if not referral:
            return None

        referrer_tier = get_current_tier(referral.referrer)
        rate_percent = get_referral_rate_percent(referrer_tier)
        if not referrer_tier or rate_percent <= 0:
            AuditLog.log(
                referral.referrer,
                "referral.qualification_skipped_no_rate",
                {
                    "referral_id": str(referral.id),
                    "referred_user_id": str(referred_user.id),
                    "tier": referrer_tier.name if referrer_tier else None,
                },
            )
            return None

        reward_amount = int(
            (Decimal(net_amount_cents) * rate_percent / Decimal("100") * _PROVISIONAL_CENTS_TO_COINS_RATE)
            .to_integral_value(rounding=ROUND_HALF_UP)
        )
        if reward_amount <= 0:
            return None

        ledger_entry = create_pending_entry(
            user=referral.referrer,
            type=RewardLedgerEntry.TYPE_REFERRAL,
            source="referral",
            amount=reward_amount,
            reference_type="referral",
            reference_id=referral.id,
            idempotency_key=f"referral_reward:{referral.id}",
            description=f"Referral reward for {referred_user.display_name or referred_user.phone}",
            metadata={
                "referred_user_id": str(referred_user.id),
                "subscription_id": str(subscription.id),
                "net_amount_cents": net_amount_cents,
                "rate_percent": str(rate_percent),
            },
        )

        referral.status = Referral.STATUS_QUALIFIED
        referral.qualifying_subscription = subscription
        referral.qualifying_net_amount_cents = net_amount_cents
        referral.reward_rate_percent_snapshot = rate_percent
        referral.reward_ledger_entry = ledger_entry
        referral.qualified_at = timezone.now()
        referral.save(update_fields=[
            "status", "qualifying_subscription", "qualifying_net_amount_cents",
            "reward_rate_percent_snapshot", "reward_ledger_entry", "qualified_at", "updated_at",
        ])

    AuditLog.log(
        referral.referrer,
        "referral.qualified",
        {
            "referral_id": str(referral.id),
            "referred_user_id": str(referred_user.id),
            "rate_percent": str(rate_percent),
            "reward_amount": reward_amount,
            "net_amount_cents": net_amount_cents,
        },
    )
    return referral


def confirm_referral_reward(referral: Referral, *, now=None) -> Referral:
    """
    The settlement transition: QUALIFIED -> REWARDED, called after the
    configured settlement window has elapsed (the window itself and the
    scheduled job that calls this are Phase 11 work — this function is
    correct and callable now, just not yet scheduled). Idempotent: a
    referral not currently QUALIFIED (already REWARDED, REVERSED, or never
    qualified) is returned unchanged rather than erroring.
    """
    now = now or timezone.now()
    with transaction.atomic():
        locked = Referral.objects.select_for_update().get(pk=referral.pk)
        if locked.status != Referral.STATUS_QUALIFIED:
            return locked
        if not locked.reward_ledger_entry_id:
            raise ValueError("Qualified referral has no associated reward ledger entry.")

        entry = confirm_ledger_entry(locked.reward_ledger_entry, actor=locked.referrer)

        locked.status = Referral.STATUS_REWARDED
        locked.reward_points_awarded = entry.amount
        locked.rewarded_at = now
        locked.save(update_fields=["status", "reward_points_awarded", "rewarded_at", "updated_at"])

    AuditLog.log(locked.referrer, "referral.reward_confirmed", {
        "referral_id": str(locked.id), "amount": entry.amount,
    })
    return locked


def reverse_referral_reward(referral: Referral, *, reason: str) -> Referral:
    """
    Reverses a QUALIFIED or REWARDED referral (refund/chargeback on the
    qualifying subscription). Delegates the actual ledger-entry reversal to
    apps.rewards.services.reverse_ledger_entry, which already handles both
    cases correctly (a still-PENDING ledger entry is flipped to REVERSED
    directly; a CONFIRMED one gets a compensating negative entry, per the
    Phase 1/2 reversal doctrine). Idempotent: reversing an already-REVERSED
    referral is a no-op, matching
    apps.billing.services.reverse_tier_upgrade_payment's idempotency style.
    """
    with transaction.atomic():
        locked = Referral.objects.select_for_update().get(pk=referral.pk)
        if locked.status == Referral.STATUS_REVERSED:
            return locked
        if locked.status not in (Referral.STATUS_QUALIFIED, Referral.STATUS_REWARDED):
            raise ValueError(f"Cannot reverse a referral with status={locked.status!r}.")

        if locked.reward_ledger_entry_id:
            reverse_ledger_entry(locked.reward_ledger_entry, reason=reason, actor=locked.referrer)

        locked.status = Referral.STATUS_REVERSED
        locked.save(update_fields=["status", "updated_at"])

    AuditLog.log(locked.referrer, "referral.reward_reversed", {
        "referral_id": str(locked.id), "reason": reason,
    })
    return locked


# ---------------------------------------------------------------------
# Settlement sweep (Phase 11). REFERRAL_SETTLEMENT_WINDOW_DAYS is a
# proposed default, NOT a business decision already made — see the Phase
# 11 report's decisions-requiring-approval section. 14 days mirrors a
# typical payment-dispute/chargeback window, so a referral isn't rewarded
# before its underlying qualifying payment could still be reversed by the
# payment provider (which would otherwise require clawing back an already
# -settled REWARDED referral instead of simply not qualifying it yet).
# ---------------------------------------------------------------------
REFERRAL_SETTLEMENT_WINDOW_DAYS = 14


def sweep_settleable_referrals(limit: int = 500) -> dict:
    """
    Finds every QUALIFIED Referral whose qualified_at is older than
    REFERRAL_SETTLEMENT_WINDOW_DAYS and settles it via
    confirm_referral_reward(), which is idempotent and does the real
    ledger-confirmation work. Mirrors
    apps.billing.services.sweep_expired_subscriptions: a bounded batch,
    oldest-qualified-first, one failure logged and counted rather than
    aborting the whole run. Called from
    apps.referrals.tasks.confirm_settled_referrals (Celery, scheduled via
    CELERY_BEAT_SCHEDULE) and from the confirm_settled_referrals
    management command (manual/cron fallback, same pattern as
    apps.media.tasks / expire_media_uploads).

    A QUALIFIED referral with qualified_at still NULL (pre-Phase-11 data
    the migration backfill couldn't reach, e.g. a row created between
    qualify_referral() shipping and this sweep shipping in a rolling
    deploy) is skipped rather than treated as immediately settleable —
    it will be picked up once qualified_at is set by a later, unrelated
    save, or can be backfilled manually; settling it early would bypass
    the settlement window entirely.
    """
    cutoff = timezone.now() - timezone.timedelta(days=REFERRAL_SETTLEMENT_WINDOW_DAYS)
    candidates = list(
        Referral.objects.filter(
            status=Referral.STATUS_QUALIFIED,
            qualified_at__isnull=False,
            qualified_at__lte=cutoff,
        ).select_related("referrer", "reward_ledger_entry").order_by("qualified_at")[:limit]
    )
    settled = 0
    errors = 0
    for referral in candidates:
        try:
            confirm_referral_reward(referral)
            settled += 1
        except Exception:
            logger.exception("confirm_referral_reward failed for referral_id=%s", referral.id)
            errors += 1
    return {"candidates": len(candidates), "settled": settled, "errors": errors}
