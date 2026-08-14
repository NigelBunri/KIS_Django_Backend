from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_UP, Decimal

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.accounts.models import AuditLog, User

from .models import (
    AchievementDefinition,
    RedemptionPolicy,
    RepeatableRewardRule,
    RewardLedgerEntry,
)

logger = logging.getLogger(__name__)


class InsufficientRewardBalance(ValueError):
    """Raised when a redemption reservation would exceed the user's
    currently available (CONFIRMED/REDEEMED) coin balance."""


class RedemptionPolicyViolation(ValueError):
    """Raised only if the computed payable amount would somehow violate the
    policy's own floors/ceilings — indicates a misconfigured
    RedemptionPolicy, not a normal user-input condition. Surfaced loudly
    rather than silently clamped, per the economic-safety requirement that
    a policy bug must never quietly produce a wrong charge."""


def _period_key(frequency: str, when) -> str:
    if frequency == RepeatableRewardRule.FREQUENCY_DAILY:
        return when.strftime("%Y-%m-%d")
    if frequency == RepeatableRewardRule.FREQUENCY_WEEKLY:
        iso_year, iso_week, _ = when.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    if frequency == RepeatableRewardRule.FREQUENCY_MONTHLY:
        return when.strftime("%Y-%m")
    raise ValueError(f"_period_key does not handle frequency={frequency!r} — PER_EVENT uses event_id instead.")


def grant_achievement(user: User, code: str) -> RewardLedgerEntry | None:
    """
    Grants a one-time achievement reward. Idempotent per (user, code): a
    second call for an already-granted achievement (or an inactive/unknown
    code) silently no-ops and returns None — callers must not treat None as
    an error.
    """
    definition = AchievementDefinition.objects.filter(code=code, is_active=True).first()
    if not definition:
        return None

    idempotency_key = f"achievement:{user.id}:{code}"
    now = timezone.now()
    try:
        with transaction.atomic():
            entry = RewardLedgerEntry.objects.create(
                user=user,
                type=RewardLedgerEntry.TYPE_ACHIEVEMENT,
                source=code,
                amount=definition.coin_amount,
                status=RewardLedgerEntry.STATUS_CONFIRMED,
                idempotency_key=idempotency_key,
                description=definition.title,
                effective_at=now,
            )
    except IntegrityError:
        return None

    AuditLog.log(user, "reward.achievement_granted", {
        "code": code, "amount": definition.coin_amount, "entry_id": str(entry.id),
    })
    return entry


def grant_promo_bonus(user: User, promo_code: str, coins: int) -> RewardLedgerEntry | None:
    """
    Grants a promo code's coin bonus (called from
    apps.billing.services.redeem_promo_code, inside the same transaction as
    the rest of that redemption). Idempotent per (user, promo_code) — the
    idempotency key alone is sufficient dedup since PromoRedemption already
    enforces one redemption per (user, promo) at the DB level, so this can
    never legitimately be called twice for the same pair; the guard here is
    defense in depth, not the primary mechanism.

    coins <= 0 is a no-op (returns None) — some promos carry only a cash
    bonus (handled separately in apps.billing) with no coin component.
    """
    if coins <= 0:
        return None

    idempotency_key = f"promo:{user.id}:{promo_code}"
    try:
        with transaction.atomic():
            entry = RewardLedgerEntry.objects.create(
                user=user,
                type=RewardLedgerEntry.TYPE_PROMO,
                source=promo_code,
                amount=coins,
                status=RewardLedgerEntry.STATUS_CONFIRMED,
                idempotency_key=idempotency_key,
                description=f"Promo code {promo_code}",
                effective_at=timezone.now(),
            )
    except IntegrityError:
        return None

    AuditLog.log(user, "reward.promo_granted", {
        "promo_code": promo_code, "amount": coins, "entry_id": str(entry.id),
    })
    return entry


def grant_repeatable(user: User, code: str, *, event_id: str | None = None) -> RewardLedgerEntry | None:
    """
    Grants a repeatable reward, respecting the rule's frequency and
    max_per_period. Idempotent: each period (or, for PER_EVENT rules, each
    distinct event_id) can only ever produce up to max_per_period grants —
    enforced by trying sequential idempotency-key "slots" and letting the
    DB's unique constraint reject a slot a concurrent request already took,
    rather than relying on a row lock (there is nothing to lock before the
    first grant of a new period exists).

    PER_EVENT rules require event_id and always allow exactly one grant per
    (user, code, event_id) — max_per_period is not meaningful for them.
    """
    rule = RepeatableRewardRule.objects.filter(code=code, is_active=True).first()
    if not rule:
        return None

    now = timezone.now()
    if rule.frequency == RepeatableRewardRule.FREQUENCY_PER_EVENT:
        if not event_id:
            raise ValueError("event_id is required for PER_EVENT repeatable rewards.")
        period_key = f"event:{event_id}"
        max_allowed = 1
    else:
        period_key = _period_key(rule.frequency, now)
        max_allowed = max(1, rule.max_per_period)

    metadata = {"period_key": period_key}
    if event_id:
        metadata["event_id"] = event_id

    for seq in range(1, max_allowed + 1):
        idempotency_key = f"repeatable:{user.id}:{code}:{period_key}:{seq}"
        try:
            with transaction.atomic():
                entry = RewardLedgerEntry.objects.create(
                    user=user,
                    type=RewardLedgerEntry.TYPE_REPEATABLE,
                    source=code,
                    amount=rule.coin_amount,
                    status=RewardLedgerEntry.STATUS_CONFIRMED,
                    idempotency_key=idempotency_key,
                    description=rule.title,
                    effective_at=now,
                    metadata=metadata,
                )
        except IntegrityError:
            # This slot was already taken (by this request retried, or a
            # genuinely concurrent one) — try the next slot rather than
            # failing outright.
            continue
        AuditLog.log(user, "reward.repeatable_granted", {
            "code": code, "amount": rule.coin_amount, "entry_id": str(entry.id), "period_key": period_key,
        })
        return entry

    return None  # every slot for this period is taken — limit reached


def create_pending_entry(
    *,
    user: User,
    type: str,
    source: str,
    amount: int,
    reference_type: str = "",
    reference_id=None,
    idempotency_key: str | None = None,
    description: str = "",
    metadata: dict | None = None,
    effective_at=None,
    expires_at=None,
) -> RewardLedgerEntry:
    """
    Generic PENDING ledger entry creator for flows (e.g. referral
    qualification) that need a settlement window before a reward becomes
    spendable. Idempotent via idempotency_key: a duplicate call (retried
    webhook, concurrent request) returns the existing entry rather than
    creating a second one or raising.
    """
    try:
        with transaction.atomic():
            return RewardLedgerEntry.objects.create(
                user=user,
                type=type,
                source=source,
                amount=amount,
                status=RewardLedgerEntry.STATUS_PENDING,
                reference_type=reference_type,
                reference_id=reference_id,
                idempotency_key=idempotency_key,
                description=description,
                metadata=metadata or {},
                effective_at=effective_at or timezone.now(),
                expires_at=expires_at,
            )
    except IntegrityError:
        if not idempotency_key:
            raise
        existing = RewardLedgerEntry.objects.filter(idempotency_key=idempotency_key).first()
        if existing is None:
            raise
        return existing


def confirm_ledger_entry(entry: RewardLedgerEntry, *, actor: User | None = None) -> RewardLedgerEntry:
    """
    Transitions a PENDING entry to settled (CONFIRMED, or REDEEMED for
    TYPE_REDEMPTION entries — REDEEMED is functionally identical to
    CONFIRMED for balance purposes, it's just the correct label for a
    spend-type entry). Idempotent: confirming an already-settled entry is a
    no-op that returns it unchanged, so a retried scheduler tick or webhook
    redelivery is always safe. Raises for REVERSED/EXPIRED/CANCELLED —
    there's nothing meaningful to confirm.
    """
    with transaction.atomic():
        locked = RewardLedgerEntry.objects.select_for_update().get(pk=entry.pk)
        if locked.status in RewardLedgerEntry.BALANCE_STATUSES:
            return locked
        if locked.status != RewardLedgerEntry.STATUS_PENDING:
            raise ValueError(f"Cannot confirm an entry with status={locked.status!r}.")

        target_status = (
            RewardLedgerEntry.STATUS_REDEEMED
            if locked.type == RewardLedgerEntry.TYPE_REDEMPTION
            else RewardLedgerEntry.STATUS_CONFIRMED
        )
        locked.status = target_status
        locked.save(update_fields=["status", "updated_at"])

    AuditLog.log(actor or locked.user, "reward.entry_confirmed", {
        "entry_id": str(locked.id), "type": locked.type, "amount": locked.amount,
    })
    return locked


def get_reward_balance(user: User) -> dict:
    """
    available = sum(amount) over CONFIRMED/REDEEMED rows — the only
    spendable balance. pending = sum(amount) over PENDING rows, shown
    separately, never spendable. This is the ONLY balance formula anywhere
    in the system; no cached/denormalized balance field exists to drift out
    of sync with it.
    """
    aggregates = (
        RewardLedgerEntry.objects.filter(user=user)
        .values("status")
        .annotate(total=Sum("amount"))
    )
    by_status = {row["status"]: row["total"] or 0 for row in aggregates}
    available = sum(by_status.get(s, 0) for s in RewardLedgerEntry.BALANCE_STATUSES)
    pending = by_status.get(RewardLedgerEntry.STATUS_PENDING, 0)
    return {"available": available, "pending": pending}


def reverse_ledger_entry(entry: RewardLedgerEntry, *, reason: str, actor: User | None = None) -> RewardLedgerEntry:
    """
    The single authoritative reversal path.

    - A still-PENDING entry (never confirmed, never counted toward balance)
      is flipped directly to REVERSED — nothing to compensate for.
    - A settled (CONFIRMED/REDEEMED) entry is never mutated. A new
      TYPE_REVERSAL entry is created instead, with `reversal_of` pointing
      back and an opposite-signed amount, so the balance nets out correctly
      while the original stays intact forever — mirrors
      apps.billing.services.reverse_tier_upgrade_payment's existing
      "never edit a settled financial row, write a compensating entry" rule.
    - Idempotent: reversing an already-reversed settled entry returns the
      existing reversal rather than creating a second one (enforced via the
      idempotency_key unique constraint, not a pre-check, to stay race-safe).
      Reversing an entry already in REVERSED/EXPIRED/CANCELLED (including a
      PENDING entry that was already flipped to REVERSED by an earlier
      call) is likewise a no-op that returns it unchanged — matching every
      other reversal path in this project (reverse_tier_upgrade_payment,
      the settled branch just below). Phase 6 found this was the one
      inconsistent case: two genuinely concurrent callers reversing the
      same still-PENDING entry (e.g. duplicate/out-of-order webhook
      delivery both trying to release the same abandoned reservation)
      previously raised an unhandled ValueError from whichever call lost
      the race, instead of idempotently no-opping like everywhere else.
    """
    with transaction.atomic():
        locked = RewardLedgerEntry.objects.select_for_update().get(pk=entry.pk)

        if locked.status in (
            RewardLedgerEntry.STATUS_REVERSED,
            RewardLedgerEntry.STATUS_EXPIRED,
            RewardLedgerEntry.STATUS_CANCELLED,
        ):
            return locked

        if locked.status == RewardLedgerEntry.STATUS_PENDING:
            locked.status = RewardLedgerEntry.STATUS_REVERSED
            locked.save(update_fields=["status", "updated_at"])
            AuditLog.log(actor or locked.user, "reward.entry_reversed", {
                "entry_id": str(locked.id), "reason": reason, "previous_status": "pending",
            })
            return locked

        idempotency_key = f"reversal:{locked.id}"
        try:
            with transaction.atomic():
                reversal = RewardLedgerEntry.objects.create(
                    user=locked.user,
                    type=RewardLedgerEntry.TYPE_REVERSAL,
                    source=locked.source,
                    amount=-locked.amount,
                    status=RewardLedgerEntry.STATUS_CONFIRMED,
                    reference_type=locked.reference_type,
                    reference_id=locked.reference_id,
                    reversal_of=locked,
                    idempotency_key=idempotency_key,
                    description=f"Reversal: {reason}",
                    metadata={"reason": reason},
                )
        except IntegrityError:
            return RewardLedgerEntry.objects.get(idempotency_key=idempotency_key)

        AuditLog.log(actor or locked.user, "reward.entry_reversed", {
            "entry_id": str(locked.id), "reversal_id": str(reversal.id), "reason": reason,
        })
        return reversal


def expire_ledger_entry(entry: RewardLedgerEntry, *, actor: User | None = None) -> RewardLedgerEntry:
    """
    Expires a CONFIRMED-but-unspent entry whose expires_at has passed.
    Structurally identical to the settled branch of reverse_ledger_entry
    (same "never mutate a settled row, write a compensating entry" rule),
    but produces a TYPE_EXPIRATION entry instead of TYPE_REVERSAL, since
    "the reward lapsed unused" is a distinct, user-facing reason from "the
    reward was reversed because the underlying activity was reversed."

    Only CONFIRMED is eligible — REDEEMED means the coins were already
    spent (nothing left to lapse); PENDING/REVERSED/EXPIRED/CANCELLED are
    all no-ops, returned unchanged, matching reverse_ledger_entry's
    idempotency style so a retried scheduler tick is always safe.
    """
    with transaction.atomic():
        locked = RewardLedgerEntry.objects.select_for_update().get(pk=entry.pk)

        if locked.status != RewardLedgerEntry.STATUS_CONFIRMED:
            return locked

        idempotency_key = f"expiration:{locked.id}"
        try:
            with transaction.atomic():
                expiration = RewardLedgerEntry.objects.create(
                    user=locked.user,
                    type=RewardLedgerEntry.TYPE_EXPIRATION,
                    source=locked.source,
                    amount=-locked.amount,
                    status=RewardLedgerEntry.STATUS_CONFIRMED,
                    reference_type=locked.reference_type,
                    reference_id=locked.reference_id,
                    reversal_of=locked,
                    idempotency_key=idempotency_key,
                    description="Expired: unused before expiration date",
                )
        except IntegrityError:
            return RewardLedgerEntry.objects.get(idempotency_key=idempotency_key)

    AuditLog.log(actor or locked.user, "reward.entry_expired", {
        "entry_id": str(locked.id), "expiration_id": str(expiration.id),
    })
    return expiration


def expire_reward_entries(limit: int = 500) -> dict:
    """
    Finds every CONFIRMED RewardLedgerEntry past its expires_at and expires
    it via expire_ledger_entry(). Mirrors
    apps.billing.services.sweep_expired_subscriptions: a bounded batch,
    oldest-expired-first, one failure logged and counted rather than
    aborting the whole run. Rows with expires_at=NULL never match (they
    don't expire) — this is a query filter, not a special case to handle.
    Called from apps.rewards.tasks.expire_reward_ledger_entries (Celery,
    scheduled via CELERY_BEAT_SCHEDULE) and from the expire_reward_entries
    management command (manual/cron fallback, same pattern as
    apps.media.tasks / expire_media_uploads).
    """
    now = timezone.now()
    candidates = list(
        RewardLedgerEntry.objects.filter(
            status=RewardLedgerEntry.STATUS_CONFIRMED,
            expires_at__isnull=False,
            expires_at__lte=now,
        ).select_related("user").order_by("expires_at")[:limit]
    )
    expired = 0
    errors = 0
    for entry in candidates:
        try:
            expire_ledger_entry(entry)
            expired += 1
        except Exception:
            logger.exception("expire_ledger_entry failed for entry_id=%s", entry.id)
            errors += 1
    return {"candidates": len(candidates), "expired": expired, "errors": errors}


# ---------------------------------------------------------------------
# Redemption ceiling engine (Phase 5). Mirrors the existing
# WalletAccount.locked_cents reserve/release/refund pattern in
# apps.billing.services (lock_wallet_funds_for_booking /
# release_locked_booking_funds / refund_locked_booking_funds) — same idea,
# applied to KIS Coins instead of cash: reserve_redemption() locks coins by
# creating a PENDING ledger entry, confirm_redemption() settles it once
# payment succeeds, release_redemption() gives the coins back if payment
# never completes.
# ---------------------------------------------------------------------

@dataclass
class RedemptionQuote:
    coins_to_spend: int
    discount_cents: int
    payable_cents: int


def calculate_redemption(
    user: User,
    gross_amount_cents: int,
    *,
    context: str = "subscription_upgrade",
    already_discounted_cents: int = 0,
) -> RedemptionQuote:
    """
    The ONLY place a KIS-Coins discount amount is ever computed. Never
    trusts a client-supplied discount/coin amount — the caller only signals
    intent ("apply my rewards"); this always derives the actual number from
    the live RedemptionPolicy and the user's live balance.

    `already_discounted_cents` lets a caller that already applied a promo
    discount tell this function how much room is left under the absolute
    ceiling, so coins + promo can never together exceed
    absolute_max_discount_percent of the ORIGINAL gross price.

    No active RedemptionPolicy for `context` -> zero discount (fails safe,
    never guesses a ceiling).
    """
    if gross_amount_cents <= 0:
        return RedemptionQuote(coins_to_spend=0, discount_cents=0, payable_cents=max(gross_amount_cents, 0))

    policy = RedemptionPolicy.objects.filter(context=context, is_active=True).first()
    if not policy:
        payable = max(gross_amount_cents - already_discounted_cents, 0)
        return RedemptionQuote(coins_to_spend=0, discount_cents=0, payable_cents=payable)

    gross = Decimal(gross_amount_cents)
    normal_ceiling_cents = int((gross * policy.normal_max_discount_percent / Decimal("100")).to_integral_value(rounding=ROUND_DOWN))
    absolute_ceiling_cents = int((gross * policy.absolute_max_discount_percent / Decimal("100")).to_integral_value(rounding=ROUND_DOWN))
    min_cash_cents = int((gross * policy.min_cash_contribution_percent / Decimal("100")).to_integral_value(rounding=ROUND_UP))

    remaining_absolute_room = max(absolute_ceiling_cents - already_discounted_cents, 0)
    coins_ceiling_cents = max(min(normal_ceiling_cents, remaining_absolute_room), 0)

    balance_coins = get_reward_balance(user)["available"]
    if policy.coin_value_cents > 0 and balance_coins > 0:
        balance_value_cents = int((Decimal(balance_coins) * policy.coin_value_cents).to_integral_value(rounding=ROUND_DOWN))
    else:
        balance_value_cents = 0

    affordable_discount_cents = min(coins_ceiling_cents, balance_value_cents)
    if policy.coin_value_cents > 0 and affordable_discount_cents > 0:
        coins_to_spend = int((Decimal(affordable_discount_cents) / policy.coin_value_cents).to_integral_value(rounding=ROUND_DOWN))
    else:
        coins_to_spend = 0
    # Recompute the discount from the whole-coin amount actually being
    # spent — never charge a discount for a fractional coin the user
    # doesn't have.
    discount_cents = int((Decimal(coins_to_spend) * policy.coin_value_cents).to_integral_value(rounding=ROUND_DOWN)) if coins_to_spend else 0

    payable_cents = gross_amount_cents - already_discounted_cents - discount_cents

    if payable_cents < 0:
        raise RedemptionPolicyViolation(
            f"Computed payable_cents={payable_cents} is negative for context={context!r} — "
            f"check RedemptionPolicy configuration."
        )
    if payable_cents < min_cash_cents:
        raise RedemptionPolicyViolation(
            f"Computed payable_cents={payable_cents} is below the configured minimum cash "
            f"contribution ({min_cash_cents} cents) for context={context!r} — "
            f"check RedemptionPolicy configuration (percentages may be inconsistent)."
        )

    return RedemptionQuote(coins_to_spend=coins_to_spend, discount_cents=discount_cents, payable_cents=payable_cents)


def reserve_redemption(
    user: User,
    coins_to_spend: int,
    *,
    reference_type: str,
    reference_id=None,
    idempotency_key: str,
    description: str = "",
    metadata: dict | None = None,
) -> RewardLedgerEntry | None:
    """
    Locks `coins_to_spend` coins by creating a PENDING TYPE_REDEMPTION
    entry, after re-verifying (under a lock) that the user's RESERVABLE
    balance actually covers it — closing two distinct double-spend windows:

    1. Concurrent requests reading the same stale balance before either
       commits — closed by locking the User row as a serialization anchor,
       so a second concurrent call blocks here until the first commits its
       new PENDING row.
    2. A sequential second reservation attempt while an EARLIER reservation
       is still outstanding (PENDING, not yet confirmed/released) — a plain
       `available` check (CONFIRMED/REDEEMED only, by design) would miss
       this entirely, since a PENDING reservation doesn't reduce
       `available` until it settles. Reservable balance is therefore
       `available - already_reserved`, where already_reserved is the total
       magnitude of this user's own outstanding PENDING negative
       (redemption-type) entries — not all PENDING entries, since a
       positive-amount PENDING entry (e.g. a referral reward awaiting
       settlement) isn't spendable yet either and must not inflate
       reservable capacity.

    coins_to_spend <= 0 is a no-op (returns None) — nothing to reserve.
    Idempotent via idempotency_key like every other ledger mutation here.
    """
    if coins_to_spend <= 0:
        return None

    with transaction.atomic():
        User.objects.select_for_update().get(pk=user.pk)
        available = get_reward_balance(user)["available"]
        already_reserved = -(
            RewardLedgerEntry.objects.filter(
                user=user, status=RewardLedgerEntry.STATUS_PENDING, amount__lt=0,
            ).aggregate(total=Sum("amount"))["total"] or 0
        )
        reservable = available - already_reserved
        if reservable < coins_to_spend:
            raise InsufficientRewardBalance(
                f"User has {reservable} reservable coins ({available} available, "
                f"{already_reserved} already reserved), cannot reserve {coins_to_spend}."
            )

        return create_pending_entry(
            user=user,
            type=RewardLedgerEntry.TYPE_REDEMPTION,
            source="subscription_discount",
            amount=-coins_to_spend,
            reference_type=reference_type,
            reference_id=reference_id,
            idempotency_key=idempotency_key,
            description=description,
            metadata=metadata or {},
        )


def confirm_redemption(entry: RewardLedgerEntry, *, actor: User | None = None) -> RewardLedgerEntry:
    """Settles a redemption reservation once the payment it discounted has
    succeeded. Thin, named wrapper around confirm_ledger_entry (which
    already transitions a TYPE_REDEMPTION entry to REDEEMED, not CONFIRMED,
    per the Phase 2 status semantics) — kept as a separate name so call
    sites read as "confirm this redemption" rather than the more generic
    "confirm this ledger entry"."""
    return confirm_ledger_entry(entry, actor=actor)


def release_redemption(entry: RewardLedgerEntry, *, reason: str, actor: User | None = None) -> RewardLedgerEntry:
    """Releases a redemption reservation whose payment failed, was
    cancelled, or never completed — the coins become spendable again. A
    still-PENDING entry (the only state this should ever be called on)
    flips straight to REVERSED with no compensating row, per
    reverse_ledger_entry's existing rule."""
    return reverse_ledger_entry(entry, reason=reason, actor=actor)


# ---------------------------------------------------------------------
# Reconciliation (Phase 11). Read-only: reports anomalies via logger.
# warning and in the returned dict, never writes anything. Financial-state
# repair, if an anomaly is ever found, is a deliberate, reviewed action an
# operator takes afterward (e.g. via the Django admin or a one-off script)
# — never something a scheduled job silently does to a user's balance.
# Uses deferred imports for apps.referrals/apps.billing to avoid the
# circular import both of those apps already avoid the same way when
# importing apps.rewards (see apps.referrals.services, apps.billing.
# services.credit_bonus).
# ---------------------------------------------------------------------

def reconcile_rewards_and_referrals(limit: int = 1000) -> dict:
    """
    Bounded, read-only consistency checks across Referral <-> RewardLedgerEntry
    <-> WalletTransaction. Each check is independently bounded by `limit`
    and logged individually via logger.warning so an operator can find
    anomalies in the log/alerting pipeline without needing this function's
    return value. Called from apps.rewards.tasks.reconcile_rewards_and_referrals
    (Celery, scheduled via CELERY_BEAT_SCHEDULE) and from the
    reconcile_rewards management command (manual/cron fallback).
    """
    from apps.billing.models import WalletTransaction
    from apps.referrals.models import Referral

    anomalies: list[dict] = []

    def _flag(kind: str, **details):
        anomalies.append({"kind": kind, **details})
        logger.warning("reward/referral reconciliation anomaly: %s %s", kind, details)
        # AuditLog (not just the log line) so anomalies are queryable and
        # admin-visible (apps.accounts.admin.AuditLogAdmin already supports
        # filtering/searching by action) instead of only living in
        # whatever log pipeline happens to be watching. actor=None is the
        # documented convention for a system-detected event, not a
        # user-initiated one.
        AuditLog.log(None, "reward.reconciliation_anomaly", {
            "kind": kind, "severity": "warning", **details,
        })

    # 1) REWARDED referral whose ledger entry never actually settled.
    rewarded = (
        Referral.objects.filter(status=Referral.STATUS_REWARDED)
        .select_related("reward_ledger_entry")
        .exclude(reward_ledger_entry__status__in=RewardLedgerEntry.BALANCE_STATUSES)
        [:limit]
    )
    for referral in rewarded:
        _flag(
            "referral_rewarded_but_ledger_not_settled",
            referral_id=str(referral.id),
            ledger_status=referral.reward_ledger_entry.status if referral.reward_ledger_entry_id else None,
        )

    # 2) QUALIFIED referral whose ledger entry isn't still PENDING (should
    # only ever move to settled via confirm_referral_reward, which also
    # advances the referral's own status in the same transaction).
    qualified = (
        Referral.objects.filter(status=Referral.STATUS_QUALIFIED)
        .select_related("reward_ledger_entry")
        .exclude(reward_ledger_entry__status=RewardLedgerEntry.STATUS_PENDING)
        [:limit]
    )
    for referral in qualified:
        _flag(
            "referral_qualified_but_ledger_not_pending",
            referral_id=str(referral.id),
            ledger_status=referral.reward_ledger_entry.status if referral.reward_ledger_entry_id else None,
        )

    # 3) REVERSED referral whose ledger entry is still CONFIRMED with no
    # compensating reversal/expiration entry pointing back at it.
    reversed_referrals = (
        Referral.objects.filter(status=Referral.STATUS_REVERSED, reward_ledger_entry__isnull=False)
        .select_related("reward_ledger_entry")
        .filter(reward_ledger_entry__status=RewardLedgerEntry.STATUS_CONFIRMED)
        [:limit]
    )
    for referral in reversed_referrals:
        has_compensating_entry = RewardLedgerEntry.objects.filter(
            reversal_of_id=referral.reward_ledger_entry_id,
        ).exists()
        if not has_compensating_entry:
            _flag(
                "referral_reversed_but_ledger_not_reversed",
                referral_id=str(referral.id),
                ledger_entry_id=str(referral.reward_ledger_entry_id),
            )

    # 4) Settled redemption entry whose backing WalletTransaction is missing
    # or never actually succeeded.
    redemption_entries = RewardLedgerEntry.objects.filter(
        type=RewardLedgerEntry.TYPE_REDEMPTION,
        status=RewardLedgerEntry.STATUS_REDEEMED,
        reference_type="wallet_transaction",
        reference_id__isnull=False,
    )[:limit]
    wallet_tx_by_id = {
        str(tx.id): tx
        for tx in WalletTransaction.objects.filter(
            id__in=[e.reference_id for e in redemption_entries],
        )
    }
    for entry in redemption_entries:
        tx = wallet_tx_by_id.get(str(entry.reference_id))
        if tx is None:
            _flag("redemption_entry_missing_wallet_transaction", entry_id=str(entry.id))
        elif tx.status != "success":
            _flag(
                "redemption_entry_settled_but_wallet_transaction_not_successful",
                entry_id=str(entry.id), wallet_transaction_id=str(tx.id), wallet_transaction_status=tx.status,
            )

    return {
        "checked": {
            "rewarded_referrals": len(rewarded),
            "qualified_referrals": len(qualified),
            "reversed_referrals": len(reversed_referrals),
            "redemption_entries": len(redemption_entries),
        },
        "anomalies": anomalies,
    }
