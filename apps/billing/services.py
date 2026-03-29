from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Tuple

from datetime import timedelta

from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import User, Subscription, AccountTier, AuditLog
from apps.partners.services import ensure_partner_profiles_for_user
from apps.commerce.models import LoyaltyPoint
from .models import WalletAccount, CreditAccount, WalletLedgerEntry, WalletTransaction

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
) -> None:
    record_ledger(
        user=user,
        kind="tier_upgrade",
        amount_cents=amount_cents,
        credits_delta=credits_delta,
        reference=reference or f"tier:{tier.id}",
        meta={"tier": tier.name, "source": source, **(meta or {})},
    )
    Subscription.objects.filter(user=user, status="active").update(status="superseded", ends_at=timezone.now())
    Subscription.objects.create(
        user=user,
        tier=tier,
        status="active",
        started_at=timezone.now(),
        ends_at=timezone.now() + timedelta(days=30),
        billing_meta={"source": source},
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


def adjust_points(user: User, points: int, reason: str) -> LoyaltyPoint:
    return LoyaltyPoint.objects.create(
        user=user,
        shop=None,
        points=points,
        earned_at=timezone.now(),
        expires_at=None,
        reason=reason,
    )
