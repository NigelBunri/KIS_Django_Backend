from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Tuple

from datetime import timedelta

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import User, Subscription, AccountTier, AuditLog
from apps.partners.services import ensure_partner_profiles_for_user
from apps.commerce.models import LoyaltyPoint
from .models import WalletAccount, CreditAccount, WalletLedgerEntry, WalletTransaction

logger = logging.getLogger(__name__)

CREDITS_PER_USD = 20
POINTS_PER_CREDIT = 1


def _display_phone(user: User) -> str:
    phone = str(getattr(user, "phone", "") or "").strip()
    if phone:
        return phone
    country = str(getattr(user, "phone_country_code", "") or "").strip()
    number = str(getattr(user, "phone_number", "") or "").strip()
    if country and number:
        return f"{country}{number}"
    return number


def _display_name(user: User) -> str:
    return (
        str(getattr(user, "display_name", "") or "").strip()
        or str(getattr(user, "username", "") or "").strip()
        or str(getattr(user, "email", "") or "").strip()
        or "KIS user"
    )


def _counterparty_meta(user: User) -> dict:
    return {
        "user_id": str(user.id),
        "name": _display_name(user),
        "phone": _display_phone(user),
    }


@dataclass
class ConversionResult:
    amount_cents: int
    credits: int


def cents_to_credits(amount_cents: int) -> int:
    return max(0, (amount_cents * CREDITS_PER_USD) // 100)


def credits_to_cents(credits: int) -> int:
    return max(0, (credits * 100) // CREDITS_PER_USD)


def cents_to_usd(amount_cents: int) -> Decimal:
    value = Decimal(int(amount_cents or 0)) / Decimal("100")
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _format_decimal(value: Decimal, *, places: int) -> str:
    quantizer = Decimal("1") if places <= 0 else Decimal(f"1.{'0' * places}")
    quantized = value.quantize(quantizer, rounding=ROUND_HALF_UP)
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def format_usd_compact(amount_usd: Decimal | int | float | str) -> str:
    try:
        amount = Decimal(str(amount_usd if amount_usd is not None else 0))
    except Exception:
        amount = Decimal("0")

    sign = "-" if amount < 0 else ""
    absolute = abs(amount)
    thresholds = (
        (Decimal("1000000000000"), "T"),
        (Decimal("1000000000"), "B"),
        (Decimal("1000000"), "M"),
        (Decimal("1000"), "K"),
    )

    for threshold, suffix in thresholds:
        if absolute >= threshold:
            scaled = absolute / threshold
            if scaled >= Decimal("100"):
                places = 0
            elif scaled >= Decimal("10"):
                places = 1
            else:
                places = 2
            return f"{sign}${_format_decimal(scaled, places=places)}{suffix}"

    return f"{sign}${_format_decimal(absolute, places=2)}"


def cents_to_usd_compact(amount_cents: int) -> str:
    return format_usd_compact(cents_to_usd(amount_cents))


def get_wallet_account(user: User) -> WalletAccount:
    wallet, _ = WalletAccount.objects.get_or_create(user=user, defaults={"balance_cents": 0, "currency": "USD"})
    return wallet


def get_credit_account(user: User) -> CreditAccount:
    credits, _ = CreditAccount.objects.get_or_create(user=user, defaults={"credits": 0})
    return credits


def record_ledger(
    *,
    user: User,
    kind: str,
    amount_cents: int = 0,
    credits_delta: int = 0,
    reference: str = "",
    meta: Optional[dict] = None,
    apply_balance_change: bool = True,
) -> WalletLedgerEntry:
    meta = meta or {}
    wallet = get_wallet_account(user)
    credit = get_credit_account(user)
    if apply_balance_change:
        wallet.balance_cents += amount_cents
        credit.credits += credits_delta
        wallet.save(update_fields=["balance_cents", "updated_at"])
        credit.save(update_fields=["credits", "updated_at"])
    return WalletLedgerEntry.objects.create(
        user=user,
        kind=kind,
        amount_cents=amount_cents,
        credits_delta=credits_delta,
        balance_after_cents=wallet.balance_cents,
        credits_after=credit.credits,
        reference=reference,
        meta=meta,
    )


def debit_wallet_balance(
    *,
    user: User,
    amount_cents: int,
    reference: str,
    kind: str = "purchase",
    meta: Optional[dict] = None,
) -> WalletLedgerEntry:
    if amount_cents <= 0:
        raise ValueError("Debit amount must be greater than zero.")

    wallet = get_wallet_account(user)
    if wallet.balance_cents < amount_cents:
        raise ValueError("Insufficient wallet balance.")

    return record_ledger(
        user=user,
        kind=kind,
        amount_cents=-amount_cents,
        reference=reference,
        meta=meta,
    )


def lock_wallet_funds_for_booking(
    *,
    user: User,
    amount_cents: int,
    reference: str,
    meta: Optional[dict] = None,
) -> tuple[WalletLedgerEntry, WalletTransaction]:
    if amount_cents <= 0:
        raise ValueError("Escrow amount must be greater than zero.")
    wallet = get_wallet_account(user)
    if wallet.balance_cents < amount_cents:
        raise ValueError("Insufficient wallet balance.")
    wallet.balance_cents -= amount_cents
    wallet.locked_cents += amount_cents
    wallet.save(update_fields=["balance_cents", "locked_cents", "updated_at"])
    entry = record_ledger(
        user=user,
        kind="purchase",
        amount_cents=-amount_cents,
        reference=reference,
        meta=meta,
        apply_balance_change=False,
    )
    transaction = WalletTransaction.objects.create(
        user=user,
        provider="internal",
        method="service_booking",
        amount_cents=amount_cents,
        currency="USD",
        status="success",
        tx_ref=reference,
        meta={"source": "service_booking", **(meta or {})},
    )
    return entry, transaction


def release_locked_booking_funds(
    *,
    payer: User,
    provider: User,
    amount_cents: int,
    reference: str,
    meta: Optional[dict] = None,
) -> None:
    if amount_cents <= 0:
        raise ValueError("Release amount must be greater than zero.")
    payer_wallet = get_wallet_account(payer)
    if payer_wallet.locked_cents < amount_cents:
        raise ValueError("Insufficient locked funds.")
    payer_wallet.locked_cents -= amount_cents
    payer_wallet.save(update_fields=["locked_cents", "updated_at"])
    record_ledger(
        user=provider,
        kind="service_payout",
        amount_cents=amount_cents,
        reference=reference,
        meta=meta,
    )


def refund_locked_booking_funds(
    *,
    payer: User,
    amount_cents: int,
    reference: str,
    meta: Optional[dict] = None,
) -> None:
    if amount_cents <= 0:
        raise ValueError("Refund amount must be greater than zero.")
    wallet = get_wallet_account(payer)
    if wallet.locked_cents < amount_cents:
        raise ValueError("Insufficient locked funds.")
    wallet.locked_cents -= amount_cents
    wallet.balance_cents += amount_cents
    wallet.save(update_fields=["locked_cents", "balance_cents", "updated_at"])
    record_ledger(
        user=payer,
        kind="refund",
        amount_cents=amount_cents,
        reference=reference,
        meta=meta,
        apply_balance_change=False,
    )


def convert_cash_to_credits(user: User, amount_cents: int) -> ConversionResult:
    if not getattr(settings, "KIS_LEGACY_CASH_CREDIT_CONVERSION_ENABLED", False):
        raise ValueError("Cash-to-credit conversion is disabled. KIS promotional credits cannot be bought or converted from wallet value.")
    if amount_cents <= 0:
        raise ValueError("Amount must be greater than 0.")
    credits = cents_to_credits(amount_cents)
    if credits <= 0:
        raise ValueError("Amount too small to convert to credits.")

    with transaction.atomic():
        wallet = get_wallet_account(user)
        if wallet.balance_cents < amount_cents:
            raise ValueError("Insufficient wallet balance.")
        record_ledger(
            user=user,
            kind="conversion_cash_to_credits",
            amount_cents=-amount_cents,
            credits_delta=credits,
            reference="cash_to_credits",
        )
    return ConversionResult(amount_cents=amount_cents, credits=credits)


def convert_credits_to_cash(user: User, credits: int) -> ConversionResult:
    if not getattr(settings, "KIS_LEGACY_CASH_CREDIT_CONVERSION_ENABLED", False):
        raise ValueError("Credit-to-cash conversion is disabled. KIS promotional credits are not redeemable for cash.")
    if credits <= 0:
        raise ValueError("Credits must be greater than 0.")
    amount_cents = credits_to_cents(credits)
    if amount_cents <= 0:
        raise ValueError("Credits too small to convert to cash.")

    with transaction.atomic():
        credit = get_credit_account(user)
        if credit.credits < credits:
            raise ValueError("Insufficient credits.")
        record_ledger(
            user=user,
            kind="conversion_credits_to_cash",
            amount_cents=amount_cents,
            credits_delta=-credits,
            reference="credits_to_cash",
        )
    return ConversionResult(amount_cents=amount_cents, credits=credits)


def convert_points_to_credits(user: User, points: int) -> ConversionResult:
    if points <= 0:
        raise ValueError("Points must be greater than 0.")
    available = LoyaltyPoint.objects.filter(user=user).aggregate(total=models.Sum("points")).get("total") or 0
    if available < points:
        raise ValueError("Insufficient points.")
    credits = max(0, points // POINTS_PER_CREDIT)
    if credits <= 0:
        raise ValueError("Points too small to convert to credits.")
    with transaction.atomic():
        adjust_points(user, -points, reason="points_to_credits")
        record_ledger(
            user=user,
            kind="conversion_points_to_credits",
            amount_cents=0,
            credits_delta=credits,
            reference="points_to_credits",
        )
    return ConversionResult(amount_cents=0, credits=credits)


def transfer_balance(
    *,
    sender: User,
    recipient: User,
    amount_cents: int = 0,
    credits: int = 0,
) -> Tuple[WalletLedgerEntry, WalletLedgerEntry]:
    if not getattr(settings, "KIS_LEGACY_WALLET_TRANSFER_ENABLED", False):
        raise ValueError("Peer-to-peer wallet and promotional-credit transfers are disabled.")
    if amount_cents <= 0 and credits <= 0:
        raise ValueError("Amount or credits must be greater than 0.")
    if sender.id == recipient.id:
        raise ValueError("You cannot transfer to your own account.")

    with transaction.atomic():
        sender_counterparty = _counterparty_meta(sender)
        recipient_counterparty = _counterparty_meta(recipient)
        outbound_reference_value = recipient_counterparty.get("phone") or recipient_counterparty.get("name") or "recipient"
        inbound_reference_value = sender_counterparty.get("phone") or sender_counterparty.get("name") or "sender"

        if amount_cents > 0:
            sender_wallet = get_wallet_account(sender)
            if sender_wallet.balance_cents < amount_cents:
                raise ValueError("Insufficient wallet balance.")
            record_ledger(
                user=sender,
                kind="transfer_out",
                amount_cents=-amount_cents,
                reference=f"transfer_to:{outbound_reference_value}",
                meta={
                    "counterparty": recipient_counterparty,
                    "direction": "outbound",
                    "transfer_type": "cash",
                },
            )
            inbound = record_ledger(
                user=recipient,
                kind="transfer_in",
                amount_cents=amount_cents,
                reference=f"transfer_from:{inbound_reference_value}",
                meta={
                    "counterparty": sender_counterparty,
                    "direction": "inbound",
                    "transfer_type": "cash",
                },
            )
            outbound = WalletLedgerEntry.objects.filter(user=sender).latest("created_at")
            return outbound, inbound

        sender_credit = get_credit_account(sender)
        if sender_credit.credits < credits:
            raise ValueError("Insufficient credits.")
        record_ledger(
            user=sender,
            kind="transfer_out",
            credits_delta=-credits,
            reference=f"credit_transfer_to:{outbound_reference_value}",
            meta={
                "counterparty": recipient_counterparty,
                "direction": "outbound",
                "transfer_type": "credits",
            },
        )
        inbound = record_ledger(
            user=recipient,
            kind="transfer_in",
            credits_delta=credits,
            reference=f"credit_transfer_from:{inbound_reference_value}",
            meta={
                "counterparty": sender_counterparty,
                "direction": "inbound",
                "transfer_type": "credits",
            },
        )
        outbound = WalletLedgerEntry.objects.filter(user=sender).latest("created_at")
        return outbound, inbound


def upgrade_with_credits(user: User, tier: AccountTier) -> dict:
    required_credits = cents_to_credits(tier.price_cents)
    if required_credits <= 0:
        required_credits = 0

    with transaction.atomic():
        credit = get_credit_account(user)
        if credit.credits < required_credits:
            raise ValueError("Insufficient credits for upgrade.")
        apply_tier_upgrade(
            user=user,
            tier=tier,
            source="credits",
            credits_delta=-required_credits,
            reference=f"tier:{tier.id}",
        )

    return {
        "tier": tier.name,
        "required_credits": required_credits,
    }


def apply_tier_upgrade(
    *,
    user: User,
    tier: AccountTier,
    source: str,
    amount_cents: int = 0,
    credits_delta: int = 0,
    reference: str = "",
    meta: dict | None = None,
    indefinite: bool = False,
) -> None:
    """
    indefinite=True creates the Subscription with ends_at=None instead of
    the usual +30 days — for administrative grants (e.g. the KCAN
    super-admin bootstrap) that must not lapse on a schedule meant for paid
    subscriptions. A null ends_at is already treated as "never finalize/
    expire" by finalize_expired_subscription's existing
    `if not sub.ends_at: return` guard, so this needs no further state-
    machine changes. Callers passing indefinite=True should also pass a
    `source` distinct from real payment sources (e.g. "admin_grant") so
    referral-commission logic can exclude it later.
    """
    record_ledger(
        user=user,
        kind="tier_upgrade",
        amount_cents=amount_cents,
        credits_delta=credits_delta,
        reference=reference or f"tier:{tier.id}",
        meta={"tier": tier.name, "source": source, **(meta or {})},
    )
    # Locks the user's active-subscription row (if any) for the duration of
    # this transaction — closes a real race window where two genuinely
    # concurrent callers (e.g. a webhook redelivered while the first
    # delivery is still being processed) could both read "no active
    # subscription yet superseded" and both create a new active row,
    # tripping the uniq_active_subscription_per_user constraint for the
    # loser instead of superseding cleanly.
    with transaction.atomic():
        Subscription.objects.select_for_update().filter(
            user=user, status=Subscription.STATUS_ACTIVE,
        ).update(status=Subscription.STATUS_SUPERSEDED, ends_at=timezone.now())
        period_days = tier.billing_period_days or 30
        # `reference` is stored here (not just on the WalletLedgerEntry) so
        # reverse_tier_upgrade_payment() can find the Subscription a given
        # WalletTransaction paid for — previously no such link existed.
        Subscription.objects.create(
            user=user,
            tier=tier,
            status=Subscription.STATUS_ACTIVE,
            started_at=timezone.now(),
            ends_at=None if indefinite else timezone.now() + timedelta(days=period_days),
            billing_meta={"source": source, "reference": reference or f"tier:{tier.id}"},
        )
    user.tier = tier.name
    user.save(update_fields=["tier", "updated_at"])

    AuditLog.log(
        user,
        "billing.tier_upgrade",
        {
            "tier_id": str(tier.id),
            "tier_name": tier.name,
            "source": source,
            "amount_cents": amount_cents,
            "credits_delta": credits_delta,
            "reference": reference,
        },
    )
    ensure_partner_profiles_for_user(user, tier.name)


def finalize_expired_subscription(sub: Subscription) -> Subscription:
    """
    The single authoritative subscription-expiry transition. Called from
    three places that must all behave identically: the request-time lazy
    check (WalletViewSet.subscription/billing_history), the scheduled
    Celery sweep, and the reconcile_subscriptions management command.

    Handles BOTH cases a subscription past its ends_at can be in:
      - cancel_at_period_end=True — an explicit cancel-at-period-end or
        scheduled downgrade. Transitions to the chosen pending_tier (or
        Free if the user only cancelled, with no downgrade target), and
        grants any proration credit computed at downgrade-request time —
        this is the correct moment to grant it, since the credit is for
        unused time on the old tier that has NOW actually elapsed.
      - cancel_at_period_end=False — the subscription simply lapsed with
        no action taken. Previously NOTHING processed this case: status
        stayed "active" and the user's paid tier stayed in effect
        indefinitely past ends_at. Now correctly reverts to Free, exactly
        like an explicit cancel would, just without a proration credit
        (there was no downgrade request to prorate).

    Idempotent: only acts on a Subscription still in STATUS_ACTIVE past its
    ends_at; already-finalized rows (or ones with no ends_at at all — e.g.
    an indefinite admin grant) are returned unchanged.
    """
    if not sub.ends_at or sub.ends_at > timezone.now():
        return sub
    if sub.status != Subscription.STATUS_ACTIVE:
        return sub

    scheduled_change = sub.cancel_at_period_end
    target = sub.pending_tier if scheduled_change else None
    if target is None:
        target = AccountTier.objects.filter(name__iexact="Free").first()

    proration_cents = 0
    if scheduled_change:
        proration_cents = int((sub.billing_meta or {}).get("proration_credit_cents") or 0)

    with transaction.atomic():
        sub.status = Subscription.STATUS_EXPIRED
        sub.cancel_at_period_end = False
        sub.pending_tier = None
        sub.save(update_fields=["status", "cancel_at_period_end", "pending_tier", "updated_at"])

        if target:
            period_days = target.billing_period_days or 30
            Subscription.objects.create(
                user=sub.user,
                tier=target,
                status=Subscription.STATUS_ACTIVE,
                started_at=timezone.now(),
                ends_at=timezone.now() + timedelta(days=period_days),
                billing_meta={"source": "downgrade" if scheduled_change else "expiry"},
            )
            sub.user.tier = target.name
            sub.user.save(update_fields=["tier", "updated_at"])
            ensure_partner_profiles_for_user(sub.user, target.name)

        if proration_cents > 0:
            record_ledger(
                user=sub.user,
                kind="downgrade_credit",
                amount_cents=proration_cents,
                reference=f"downgrade-proration:{sub.id}",
                meta={"subscription_id": str(sub.id), "reason": "downgrade_proration"},
            )
            AuditLog.log(
                sub.user,
                "billing.subscription.downgrade_proration_granted",
                {"subscription_id": str(sub.id), "amount_cents": proration_cents},
            )

    return sub


def sweep_expired_subscriptions(limit: int = 500) -> dict:
    """
    Finds every still-STATUS_ACTIVE Subscription past its ends_at and
    finalizes it via finalize_expired_subscription(). This is the scheduled
    -sweep counterpart to the request-time lazy check in
    WalletViewSet.subscription/billing_history — previously ONLY that lazy
    check existed, so a subscription that lapsed without the user ever
    hitting one of those two endpoints stayed "active" (and the user's
    paid tier stayed in effect) forever, regardless of ends_at.

    Called from apps.billing.tasks.sweep_expired_subscriptions (Celery,
    scheduled via CELERY_BEAT_SCHEDULE) and from the reconcile_subscriptions
    management command (manual/cron fallback, same pattern as
    apps.media.tasks / expire_media_uploads).
    """
    candidates = list(
        Subscription.objects.filter(
            status=Subscription.STATUS_ACTIVE,
            ends_at__isnull=False,
            ends_at__lte=timezone.now(),
        ).select_related("user", "tier", "pending_tier").order_by("ends_at")[:limit]
    )
    finalized = 0
    errors = 0
    for sub in candidates:
        try:
            finalize_expired_subscription(sub)
            finalized += 1
        except Exception:
            logger.exception("finalize_expired_subscription failed for subscription_id=%s", sub.id)
            errors += 1
    return {"candidates": len(candidates), "finalized": finalized, "errors": errors}


def reverse_tier_upgrade_payment(
    *,
    transaction_obj: WalletTransaction,
    reason: str,
    event_type: str = "refund",
) -> dict:
    """
    Reverses a settled tier-upgrade payment: ends the Subscription it paid
    for, reverts the user to Free if that subscription is still their
    current one, and writes a compensating NEGATIVE ledger entry — the
    original ledger entry is never edited or deleted, matching the
    "never rewrite financial history" requirement.

    Wired into two callers:
      - WalletViewSet.refund (self-service — the only live entry point
        today). Previously this called Flutterwave's refund API and marked
        the WalletTransaction cancelled, but did nothing to the
        Subscription it paid for — a user could pay for Partner Pro,
        immediately self-refund, and keep Partner Pro until natural expiry.
      - An admin-triggered equivalent for chargebacks: no webhook receiver
        for provider chargeback notifications exists in this codebase, and
        there's no verified payload contract to build one against safely.
        Support staff can call this for a chargeback they learn about
        out-of-band until/unless a real webhook contract is confirmed.

    Idempotent per transaction — a transaction already reversed (checked
    under a row lock) is a no-op on a second call, whether that's a retried
    admin action or a chargeback notification arriving for a payment that
    was already self-refunded.
    """
    user = transaction_obj.user

    with transaction.atomic():
        locked_tx = WalletTransaction.objects.select_for_update().get(id=transaction_obj.id)
        locked_meta = dict(locked_tx.meta or {})
        if locked_meta.get("reversed"):
            return {
                "already_reversed": True,
                "subscription_id": locked_meta.get("reversed_subscription_id"),
            }

        sub = (
            Subscription.objects.select_for_update()
            .filter(user=user, billing_meta__reference=locked_tx.tx_ref)
            .order_by("-created_at")
            .first()
        )

        locked_meta.update({
            "reversed": True,
            "reversed_reason": reason,
            "reversed_event_type": event_type,
            "reversed_at": timezone.now().isoformat(),
        })
        if sub:
            locked_meta["reversed_subscription_id"] = str(sub.id)
        locked_tx.meta = locked_meta
        locked_tx.save(update_fields=["meta", "updated_at"])

        if not sub:
            # A payment predating this linkage, or one that was never a
            # tier-upgrade payment in the first place — mark the payment
            # itself reversed (above) but there's no subscription to touch.
            AuditLog.log(
                user,
                "billing.subscription.reversal_no_subscription_found",
                {"transaction_id": str(locked_tx.id), "tx_ref": locked_tx.tx_ref, "event_type": event_type},
            )
            return {"subscription_found": False}

        was_current = sub.status == Subscription.STATUS_ACTIVE
        sub.status = Subscription.STATUS_REFUNDED
        sub.save(update_fields=["status", "updated_at"])

        reverted_to_tier = None
        if was_current:
            free_tier = AccountTier.objects.filter(name__iexact="Free").first()
            if free_tier:
                Subscription.objects.filter(user=user, status=Subscription.STATUS_ACTIVE).update(
                    status=Subscription.STATUS_SUPERSEDED, ends_at=timezone.now(),
                )
                Subscription.objects.create(
                    user=user,
                    tier=free_tier,
                    status=Subscription.STATUS_ACTIVE,
                    started_at=timezone.now(),
                    ends_at=timezone.now() + timedelta(days=free_tier.billing_period_days or 30),
                    billing_meta={"source": event_type},
                )
                user.tier = free_tier.name
                user.save(update_fields=["tier", "updated_at"])
                ensure_partner_profiles_for_user(user, free_tier.name)
                reverted_to_tier = free_tier.name
        # If this subscription had ALREADY been superseded by a later,
        # separate upgrade, the user's current tier is unaffected — only
        # the historical row this payment created is marked refunded.

        record_ledger(
            user=user,
            kind="subscription_reversal",
            amount_cents=-abs(int(locked_tx.amount_cents or 0)),
            reference=f"{event_type}:{locked_tx.tx_ref}",
            meta={
                "transaction_id": str(locked_tx.id),
                "subscription_id": str(sub.id),
                "reason": reason,
                "event_type": event_type,
            },
        )
        AuditLog.log(
            user,
            "billing.subscription.reversed",
            {
                "subscription_id": str(sub.id),
                "transaction_id": str(locked_tx.id),
                "event_type": event_type,
                "reason": reason,
                "reverted_to_tier": reverted_to_tier,
            },
        )

    return {"subscription_found": True, "subscription_id": str(sub.id), "reverted_to_tier": reverted_to_tier}


def adjust_points(user: User, points: int, reason: str) -> LoyaltyPoint:
    return LoyaltyPoint.objects.create(
        user=user,
        shop=None,
        points=points,
        earned_at=timezone.now(),
        expires_at=None,
        reason=reason,
    )
