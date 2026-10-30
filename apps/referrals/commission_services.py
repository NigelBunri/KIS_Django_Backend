# apps/referrals/commission_services.py
"""Lifetime cash-commission engine — a deliberately separate reward track
from apps.referrals.services' one-time, tier-rate, KIS-Coins engine. See
ReferralCommission's own docstring (models.py) for the full reasoning;
in short, the two systems pay out different currencies (real money vs. an
internal point), on different schedules (every qualifying payment,
forever, vs. exactly once), at different rates (a flat 24% vs. a 2-20%
tier schedule) — they cannot share a state machine, so this module does
not touch apps.referrals.services or its models beyond reading the same
Referral row to find a user's permanent referrer.
"""
from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import AuditLog, User

from .models import Referral, ReferralCommission, ReferralPayoutAccount
from .services import QUALIFYING_PAYMENT_SOURCES

logger = logging.getLogger(__name__)

# Flat lifetime rate — see docs/referrals/REFERRAL_COMMISSION_SYSTEM.md
# for why this is flat rather than reusing RATE_PERCENT_BY_TIER_RANK (the
# existing tiered schedule belongs to the one-time KIS-Coins engine only).
REFERRAL_COMMISSION_RATE_PERCENT = Decimal("24.00")

# Mirrors REFERRAL_SETTLEMENT_WINDOW_DAYS (apps.referrals.services) for the
# same reason: a referral-triggering payment should not be approved for
# payout while it could still be reversed by the payment provider itself
# (a chargeback/dispute window), which would otherwise require clawing
# back an already-APPROVED (or worse, already-PAID) commission instead of
# simply not approving it yet.
REFERRAL_COMMISSION_SETTLEMENT_WINDOW_DAYS = 14


def award_referral_commission(
    *,
    referred_user: User,
    subscription,
    source: str,
    gross_amount_cents: int,
    source_payment_reference: str,
) -> ReferralCommission | None:
    """
    Called from the exact same qualifying-payment webhook success branch
    as apps.referrals.services.qualify_referral (see
    apps.billing.views — the Flutterwave tier_upgrade success handler).
    Unlike qualify_referral, this has no "already qualified" guard to
    respect — every genuinely new qualifying payment gets its own row,
    for the lifetime of the referred account.

    No-ops (returns None) if: the payment source is not genuine external
    revenue (QUALIFYING_PAYMENT_SOURCES — "mock"/"wallet"/"credits"/"free"
    excluded, same list apps.referrals.services uses), there is no
    (unblocked) referrer for this user, the computed commission would be
    zero, or a commission for this exact payment reference already
    exists — the last case is the idempotency guard against webhook
    retries, enforced at the DB level via source_payment_reference's
    unique constraint, not just this check.
    """
    if source not in QUALIFYING_PAYMENT_SOURCES:
        return None

    referral = (
        Referral.objects.select_related("referrer")
        .filter(referred_user=referred_user)
        .exclude(status=Referral.STATUS_BLOCKED)
        .first()
    )
    if not referral:
        return None

    commission_amount_cents = int(
        (Decimal(gross_amount_cents) * REFERRAL_COMMISSION_RATE_PERCENT / Decimal("100"))
        .to_integral_value(rounding=ROUND_HALF_UP)
    )
    if commission_amount_cents <= 0:
        return None

    with transaction.atomic():
        commission, created = ReferralCommission.objects.get_or_create(
            source_payment_reference=source_payment_reference,
            defaults=dict(
                referrer=referral.referrer,
                referred_user=referred_user,
                referral=referral,
                subscription=subscription,
                source_provider=source,
                gross_amount_cents=gross_amount_cents,
                commission_rate_percent=REFERRAL_COMMISSION_RATE_PERCENT,
                commission_amount_cents=commission_amount_cents,
                currency="USD",
                status=ReferralCommission.STATUS_PENDING,
            ),
        )
    if not created:
        return None

    AuditLog.log(referral.referrer, "referral.commission.created", {
        "commission_id": str(commission.id),
        "referred_user_id": str(referred_user.id),
        "gross_amount_cents": gross_amount_cents,
        "commission_amount_cents": commission_amount_cents,
        "source_payment_reference": source_payment_reference,
    })
    return commission


def approve_referral_commission(commission: ReferralCommission, *, now=None) -> ReferralCommission:
    """PENDING -> APPROVED, once the settlement window has elapsed.
    Idempotent — a commission not currently PENDING is returned
    unchanged, matching apps.referrals.services.confirm_referral_reward's
    own idempotency style."""
    now = now or timezone.now()
    with transaction.atomic():
        locked = ReferralCommission.objects.select_for_update().get(pk=commission.pk)
        if locked.status != ReferralCommission.STATUS_PENDING:
            return locked
        locked.status = ReferralCommission.STATUS_APPROVED
        locked.approved_at = now
        locked.save(update_fields=["status", "approved_at", "updated_at"])

    AuditLog.log(locked.referrer, "referral.commission.approved", {"commission_id": str(locked.id)})
    return locked


def sweep_approvable_referral_commissions(limit: int = 500) -> dict:
    """Scheduled-sweep counterpart to approve_referral_commission — finds
    every PENDING commission older than the settlement window and
    approves it. Mirrors
    apps.referrals.services.sweep_settleable_referrals exactly (bounded
    batch, oldest-first, one failure logged and counted rather than
    aborting the run)."""
    cutoff = timezone.now() - timezone.timedelta(days=REFERRAL_COMMISSION_SETTLEMENT_WINDOW_DAYS)
    candidates = list(
        ReferralCommission.objects.filter(
            status=ReferralCommission.STATUS_PENDING,
            created_at__lte=cutoff,
        ).select_related("referrer").order_by("created_at")[:limit]
    )
    approved = 0
    errors = 0
    for commission in candidates:
        try:
            approve_referral_commission(commission)
            approved += 1
        except Exception:
            logger.exception("approve_referral_commission failed for commission_id=%s", commission.id)
            errors += 1
    return {"candidates": len(candidates), "approved": approved, "errors": errors}


def reverse_referral_commission(
    *,
    referred_user: User,
    subscription=None,
    source_payment_reference: str = "",
    reason: str,
) -> ReferralCommission | None:
    """
    Reverses the commission tied to one specific qualifying payment —
    looked up by subscription (what apps.billing.services.
    reverse_tier_upgrade_payment has on hand) or source_payment_reference,
    whichever the caller provides. Idempotent: reversing an
    already-REVERSED commission is a no-op.

    A commission already PAID cannot be silently clawed back here — that
    would require a second, separate provider operation (a reversing
    transfer) this function does not attempt, matching this codebase's
    doctrine of never silently reaching into a completed financial
    transfer (see apps.referrals.services.reverse_referral_reward's own
    equivalent note). It is still marked REVERSED (for correct dashboard/
    admin reporting) with was_already_paid recorded in the audit log, so
    a human can decide on a manual clawback.
    """
    qs = ReferralCommission.objects.filter(referred_user=referred_user)
    if subscription is not None:
        qs = qs.filter(subscription=subscription)
    elif source_payment_reference:
        qs = qs.filter(source_payment_reference=source_payment_reference)
    else:
        return None

    with transaction.atomic():
        commission = qs.select_for_update().first()
        if not commission:
            return None
        if commission.status == ReferralCommission.STATUS_REVERSED:
            return commission

        was_paid = commission.status == ReferralCommission.STATUS_PAID
        commission.status = ReferralCommission.STATUS_REVERSED
        commission.reversed_at = timezone.now()
        commission.reversal_reason = reason
        commission.save(update_fields=["status", "reversed_at", "reversal_reason", "updated_at"])

    AuditLog.log(commission.referrer, "referral.commission.reversed", {
        "commission_id": str(commission.id),
        "reason": reason,
        "was_already_paid": was_paid,
    })
    return commission


def _claim_payout_attempt(commission_id) -> tuple[ReferralCommission | None, "ReferralPayoutAccount | None", str | None]:
    """Phase 1 of initiate_commission_payout — the ONLY part that touches
    the database before any external provider call, and the only part
    that holds a row lock. Persists a durable, deterministic reference
    for this attempt BEFORE returning, specifically so a crash between
    "the provider accepted the transfer" and "we saved the result" still
    leaves evidence of which reference may be in flight (see
    ReferralCommission.payout_attempt_reference's own docstring — a local
    DB transaction alone cannot make an external payment request atomic,
    so the durable marker has to exist before the request goes out, not
    after it comes back).

    select_for_update() here is also what makes two concurrent settlement
    workers safe against each other for the SAME commission: the second
    worker blocks until the first's claim transaction commits, then sees
    payout_attempt_reference already set and reuses it rather than
    minting a second, different reference — both workers then race the
    external call with the IDENTICAL reference/idempotency key, which the
    provider itself (not this codebase) is responsible for deduplicating
    (Stripe's idempotency_key does this by contract; Flutterwave's lookup
    step below plus its own X-Idempotency-Key header does the same).

    Returns (None, None, None) if there's nothing to do (wrong status,
    already has a captured payout_reference, or no active payout
    account) — the caller returns the locked row as-is in that case.
    """
    with transaction.atomic():
        locked = ReferralCommission.objects.select_for_update().get(pk=commission_id)
        if locked.status != ReferralCommission.STATUS_APPROVED or locked.payout_reference:
            return locked, None, None

        account = ReferralPayoutAccount.objects.filter(
            user=locked.referrer, status=ReferralPayoutAccount.STATUS_ACTIVE,
        ).first()
        if not account or not account.provider:
            return locked, None, None

        if not locked.payout_attempt_reference:
            locked.payout_attempts += 1
            locked.payout_attempt_reference = f"referral_commission:{locked.id}:{locked.payout_attempts}"
            locked.save(update_fields=["payout_attempts", "payout_attempt_reference", "updated_at"])
        return locked, account, locked.payout_attempt_reference


def _finalize_flutterwave_initiated(commission_id, transfer_id: str) -> ReferralCommission:
    with transaction.atomic():
        c = ReferralCommission.objects.select_for_update().get(pk=commission_id)
        if c.status != ReferralCommission.STATUS_APPROVED:
            return c  # moved on (e.g. reversed) while the provider call was in flight
        c.payout_provider = "flutterwave"
        c.payout_reference = transfer_id
        c.save(update_fields=["payout_provider", "payout_reference", "updated_at"])
    AuditLog.log(c.referrer, "referral.commission.payout_initiated", {
        "commission_id": str(c.id), "provider": "flutterwave", "payout_reference": transfer_id,
    })
    return c


def _finalize_stripe_paid(commission_id, transfer_id: str) -> ReferralCommission:
    with transaction.atomic():
        c = ReferralCommission.objects.select_for_update().get(pk=commission_id)
        if c.status != ReferralCommission.STATUS_APPROVED:
            return c
        c.payout_provider = "stripe"
        c.payout_reference = transfer_id
        c.status = ReferralCommission.STATUS_PAID
        c.paid_at = timezone.now()
        c.save(update_fields=["payout_provider", "payout_reference", "status", "paid_at", "updated_at"])
    AuditLog.log(c.referrer, "referral.commission.payout_initiated", {
        "commission_id": str(c.id), "provider": "stripe", "payout_reference": transfer_id, "marked_paid": True,
    })
    return c


def _finalize_payout_failed(commission_id, reason: str, provider: str = "") -> ReferralCommission:
    """`provider` must still be persisted even on failure — found via a
    test, not in production (good): without it, a Flutterwave attempt
    that fails locally (a timeout, say) but actually succeeded
    provider-side becomes permanently unrecoverable, because both the
    later webhook (filtered on payout_provider="flutterwave") and
    reconcile_commission_payout (which falls back to the CURRENT payout
    account's provider, which may have changed by the time anyone looks)
    would have nothing reliable to key off."""
    with transaction.atomic():
        c = ReferralCommission.objects.select_for_update().get(pk=commission_id)
        if c.status != ReferralCommission.STATUS_APPROVED:
            return c
        c.status = ReferralCommission.STATUS_FAILED_PAYOUT
        c.payout_failure_reason = reason[:255]
        update_fields = ["status", "payout_failure_reason", "updated_at"]
        if provider and not c.payout_provider:
            c.payout_provider = provider
            update_fields.append("payout_provider")
        c.save(update_fields=update_fields)
    AuditLog.log(c.referrer, "referral.commission.payout_failed", {
        "commission_id": str(c.id), "reason": c.payout_failure_reason, "provider": c.payout_provider,
    })
    return c


def initiate_commission_payout(commission: ReferralCommission) -> ReferralCommission:
    """
    APPROVED -> PAID (Stripe) or APPROVED -> left APPROVED, pending a
    `transfer.completed` webhook (Flutterwave — see
    confirm_flutterwave_commission_payout below). Raises nothing to the
    caller on a provider failure — catches it, records
    payout_failure_reason, moves to FAILED_PAYOUT, and returns the
    updated row, so a batch caller (the payout-sweep management command)
    can continue past one failure exactly like every other sweep
    function in this codebase.

    Only ever acts on a currently-APPROVED commission with an ACTIVE
    payout account for its referrer — never trusts amounts or payout
    destinations from anywhere but this row and ReferralPayoutAccount
    itself (both backend-authoritative).

    Structured in two phases specifically so no DB row lock is ever held
    across a slow external HTTP call: _claim_payout_attempt (DB only) is
    followed by the provider call with NO lock held, followed by a
    separate, short _finalize_* transaction. See _claim_payout_attempt's
    own docstring for why this also makes concurrent settlement workers
    and process crashes mid-call both safe.
    """
    locked, account, reference = _claim_payout_attempt(commission.pk)
    if account is None:
        return locked

    if account.provider == ReferralPayoutAccount.PROVIDER_FLUTTERWAVE:
        from apps.billing.referral_payouts import find_flutterwave_transfer_by_reference, initiate_flutterwave_transfer

        # Reconciliation BEFORE ever attempting a new transfer: if a prior
        # call already reached Flutterwave (crashed/timed out locally
        # before we could save the result), this finds it instead of
        # blindly retrying. "Not found" here genuinely means Flutterwave
        # never received this reference — safe to proceed.
        try:
            existing = find_flutterwave_transfer_by_reference(reference)
        except Exception:
            existing = None  # lookup itself failed - fall through; the
            # X-Idempotency-Key header on the POST below is still a
            # provider-side backstop against a true duplicate.
        if existing:
            return _apply_flutterwave_reconciliation(commission.pk, existing)

        try:
            transfer_id = initiate_flutterwave_transfer(
                beneficiary_id=account.flutterwave_beneficiary_id,
                amount_cents=locked.commission_amount_cents,
                currency=locked.currency,
                reference=reference,
                narration="KIS referral commission",
            )
        except Exception as exc:
            return _finalize_payout_failed(commission.pk, str(exc), provider="flutterwave")
        # Deliberately NOT marked PAID here — Flutterwave transfers are
        # asynchronous; "initiated" is not "settled". Stays APPROVED
        # until confirm_flutterwave_commission_payout processes the
        # provider's own transfer.completed webhook, matching this
        # codebase's "provider webhook is the only source of truth for
        # a successful payment" doctrine applied to payouts.
        return _finalize_flutterwave_initiated(commission.pk, transfer_id)

    elif account.provider == ReferralPayoutAccount.PROVIDER_STRIPE:
        from apps.billing.referral_payouts import create_stripe_transfer
        try:
            # create_stripe_transfer passes `reference` as both
            # transfer_group and the idempotency_key — a retry with this
            # same reference (crash, or a race with another worker that
            # claimed the same attempt) returns Stripe's cached original
            # result rather than creating a second real transfer. This is
            # Stripe's own documented idempotency guarantee, not
            # something reconciled client-side the way Flutterwave is
            # above (Stripe Transfers settle synchronously — there is no
            # separate "did it actually happen" state to reconcile against
            # the way an async Flutterwave transfer has).
            transfer_id = create_stripe_transfer(
                destination_account_id=account.stripe_account_id,
                amount_cents=locked.commission_amount_cents,
                currency=locked.currency.lower(),
                reference=reference,
                description=f"KIS referral commission ({commission.id})",
            )
        except Exception as exc:
            return _finalize_payout_failed(commission.pk, str(exc), provider="stripe")
        return _finalize_stripe_paid(commission.pk, transfer_id)

    return locked


def _apply_flutterwave_reconciliation(commission_id, found: dict) -> ReferralCommission:
    """Applies the ground-truth result of a provider lookup
    (find_flutterwave_transfer_by_reference) to a commission — shared by
    the automatic retry-avoidance path in initiate_commission_payout and
    the admin-triggered reconcile_commission_payout below. `found` is
    {"id": str, "status": "SUCCESSFUL"|"FAILED"|<anything else pending>}.

    The provider's own record always wins: a "SUCCESSFUL" lookup result
    corrects a commission even if it's currently FAILED_PAYOUT (scenario:
    initiate_flutterwave_transfer raised — a timeout, say — but the
    request had already reached Flutterwave and succeeded there). A PAID
    commission is never touched here regardless of what's found — once
    earned and paid, status is terminal, immutable going forward."""
    with transaction.atomic():
        c = ReferralCommission.objects.select_for_update().get(pk=commission_id)
        if c.status in (ReferralCommission.STATUS_PAID, ReferralCommission.STATUS_REVERSED):
            return c
        if c.status not in (ReferralCommission.STATUS_APPROVED, ReferralCommission.STATUS_FAILED_PAYOUT):
            return c

        update_fields = ["updated_at"]
        if not c.payout_reference and found.get("id"):
            c.payout_provider = "flutterwave"
            c.payout_reference = found["id"]
            update_fields += ["payout_provider", "payout_reference"]

        provider_status = found.get("status") or ""
        if provider_status == "SUCCESSFUL":
            c.status = ReferralCommission.STATUS_PAID
            c.paid_at = timezone.now()
            c.payout_failure_reason = ""
            update_fields += ["status", "paid_at", "payout_failure_reason"]
        elif provider_status == "FAILED" and c.status == ReferralCommission.STATUS_APPROVED:
            c.status = ReferralCommission.STATUS_FAILED_PAYOUT
            c.payout_failure_reason = "Reconciled with provider: transfer failed"
            update_fields += ["status", "payout_failure_reason"]
        # Any other provider status (PENDING/NEW/...) — leave the status
        # field alone; we may now have captured payout_reference above,
        # which is still useful (the next transfer.completed webhook, or
        # the next reconciliation call, can use it).
        c.save(update_fields=update_fields)

    was_correction = provider_status == "SUCCESSFUL"
    AuditLog.log(c.referrer, "referral.commission.payout_reconciled", {
        "commission_id": str(c.id), "provider_status": provider_status,
        "payout_reference": c.payout_reference,
    })
    return c


def reconcile_commission_payout(commission: ReferralCommission) -> ReferralCommission:
    """Admin-triggered (or operator/cron-triggered) recovery for a
    commission whose payout outcome is uncertain — typically
    FAILED_PAYOUT, where the local failure might not reflect what the
    provider actually did (see _apply_flutterwave_reconciliation's
    docstring for the canonical example). Always queries the provider
    directly; never guesses or trusts anything already in this row beyond
    which provider/reference to look up. A commission with no
    payout_attempt_reference yet (payout was never actually attempted)
    or one already PAID/REVERSED is returned unchanged — nothing to
    reconcile."""
    if not commission.payout_attempt_reference:
        return commission
    if commission.status in (ReferralCommission.STATUS_PAID, ReferralCommission.STATUS_REVERSED):
        return commission

    provider = commission.payout_provider or ""
    if not provider:
        # Never got far enough to know which provider was used (the
        # claim step persists payout_attempt_reference before the
        # provider branch runs) - infer it from the referrer's current
        # payout account, same as initiate_commission_payout would.
        account = ReferralPayoutAccount.objects.filter(
            user=commission.referrer, status=ReferralPayoutAccount.STATUS_ACTIVE,
        ).first()
        provider = account.provider if account else ""

    if provider == ReferralPayoutAccount.PROVIDER_FLUTTERWAVE:
        from apps.billing.referral_payouts import find_flutterwave_transfer_by_reference
        found = find_flutterwave_transfer_by_reference(commission.payout_attempt_reference)
        if not found:
            return commission
        return _apply_flutterwave_reconciliation(commission.pk, found)

    if provider == ReferralPayoutAccount.PROVIDER_STRIPE:
        from apps.billing.referral_payouts import find_stripe_transfer_by_reference
        found = find_stripe_transfer_by_reference(commission.payout_attempt_reference)
        if not found:
            return commission
        with transaction.atomic():
            c = ReferralCommission.objects.select_for_update().get(pk=commission.pk)
            if c.status in (ReferralCommission.STATUS_PAID, ReferralCommission.STATUS_REVERSED):
                return c
            c.payout_provider = "stripe"
            c.payout_reference = found["id"]
            if found["status"] == "SUCCESSFUL":
                c.status = ReferralCommission.STATUS_PAID
                c.paid_at = timezone.now()
                c.payout_failure_reason = ""
            c.save(update_fields=["payout_provider", "payout_reference", "status", "paid_at", "payout_failure_reason", "updated_at"])
        AuditLog.log(c.referrer, "referral.commission.payout_reconciled", {
            "commission_id": str(c.id), "provider_status": found["status"], "payout_reference": c.payout_reference,
        })
        return c

    return commission


def confirm_flutterwave_commission_payout(
    *, payout_reference: str, merchant_reference: str = "", successful: bool, failure_reason: str = "",
) -> ReferralCommission | None:
    """Processes a Flutterwave `transfer.completed` webhook event for a
    commission payout — the APPROVED -> PAID (or -> FAILED_PAYOUT)
    transition that initiate_commission_payout's Flutterwave branch
    deliberately does not make itself.

    Looks up primarily by `merchant_reference` (the `reference` field
    Flutterwave's webhook payload echoes back — OUR
    payout_attempt_reference, persisted before the transfer was ever
    requested) rather than only `payout_reference` (Flutterwave's own
    transfer id): if initiate_flutterwave_transfer raised before
    returning a transfer id (a timeout, say, even though the request had
    already reached Flutterwave), payout_reference was never saved
    locally, and a lookup keyed only on it would never find this
    commission at all, silently dropping a legitimate webhook. Falls
    back to payout_reference if no merchant_reference is provided (older
    callers / defensive).

    Idempotent, and ground-truth-wins on reconciliation (scenario:
    provider confirms success after this codebase had already given up
    and marked FAILED_PAYOUT): a "successful" event can promote
    FAILED_PAYOUT -> PAID, matching _apply_flutterwave_reconciliation's
    behavior. A PAID commission is never changed by any later event,
    successful or not — status is terminal once earned. REVERSED is
    likewise never touched here; the referred user's own refund path
    owns that transition independently."""
    with transaction.atomic():
        qs = ReferralCommission.objects.select_for_update()
        commission = None
        if merchant_reference:
            commission = qs.filter(payout_attempt_reference=merchant_reference, payout_provider="flutterwave").first()
        if commission is None and payout_reference:
            commission = qs.filter(payout_reference=payout_reference, payout_provider="flutterwave").first()
        if not commission:
            return None
        if commission.status in (ReferralCommission.STATUS_PAID, ReferralCommission.STATUS_REVERSED):
            # PAID is terminal (a later "failed" event must never downgrade
            # it — webhooks can arrive out of order); REVERSED is owned by
            # the refund path, not payout confirmation.
            return commission
        if commission.status not in (ReferralCommission.STATUS_APPROVED, ReferralCommission.STATUS_FAILED_PAYOUT):
            return commission

        if payout_reference and not commission.payout_reference:
            commission.payout_reference = payout_reference

        if successful:
            commission.status = ReferralCommission.STATUS_PAID
            commission.paid_at = timezone.now()
            commission.payout_failure_reason = ""
            commission.save(update_fields=["status", "paid_at", "payout_reference", "payout_failure_reason", "updated_at"])
        else:
            if commission.status != ReferralCommission.STATUS_APPROVED:
                # Already FAILED_PAYOUT and this is just another "failed"
                # delivery (duplicate, or out-of-order) - idempotent no-op
                # beyond possibly backfilling payout_reference above.
                commission.save(update_fields=["payout_reference", "updated_at"])
                return commission
            commission.status = ReferralCommission.STATUS_FAILED_PAYOUT
            commission.payout_failure_reason = failure_reason[:255]
            commission.save(update_fields=["status", "payout_failure_reason", "updated_at"])

    AuditLog.log(commission.referrer, "referral.commission.payout_confirmed", {
        "commission_id": str(commission.id), "successful": successful,
    })
    return commission


def sweep_payable_referral_commissions(limit: int = 200) -> dict:
    """Scheduled-sweep counterpart to initiate_commission_payout — finds
    every APPROVED commission whose referrer has an ACTIVE payout
    account and attempts payout for each. Mirrors every other sweep
    function in this module/codebase: bounded batch, oldest-approved-
    first, one failure logged and counted rather than aborting the run."""
    candidates = list(
        ReferralCommission.objects.filter(status=ReferralCommission.STATUS_APPROVED)
        .select_related("referrer").order_by("approved_at")[:limit]
    )
    paid = initiated = skipped = errors = 0
    for commission in candidates:
        try:
            result = initiate_commission_payout(commission)
            if result.status == ReferralCommission.STATUS_PAID:
                paid += 1
            elif result.status == ReferralCommission.STATUS_APPROVED and result.payout_reference:
                initiated += 1
            elif result.status == ReferralCommission.STATUS_APPROVED:
                skipped += 1
        except Exception:
            logger.exception("initiate_commission_payout failed for commission_id=%s", commission.id)
            errors += 1
    return {"candidates": len(candidates), "paid": paid, "initiated": initiated, "skipped": skipped, "errors": errors}
