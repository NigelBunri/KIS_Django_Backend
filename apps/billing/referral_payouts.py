# apps/billing/referral_payouts.py
"""Payout-execution helpers for the referral lifetime cash-commission
system (apps.referrals.commission_services). Sibling to payout_accounts.py
(Flutterwave subaccounts) and stripe_connect.py (Stripe Connect accounts),
but solving a different problem: those two exist to split ONE specific
transaction at charge time between a buyer and a shop/provider. A referral
commission has no such transaction to split — the referred user's payment
and the referrer's payout are unrelated events, and a referrer accumulates
many commissions over a lifetime — so payout here always happens as an
independent transfer, initiated after the fact, against a saved payout
destination.

Flutterwave: uses the Beneficiaries API
(https://developer.flutterwave.com/docs/beneficiaries), not a subaccount.
Only the provider-issued beneficiary id is ever persisted by callers
(ReferralPayoutAccount.flutterwave_beneficiary_id) — the raw bank account
number is never stored in our own database. At payout time, the
beneficiary's bank details are re-fetched from Flutterwave itself
(get_flutterwave_beneficiary) and passed straight into the Transfers call;
Flutterwave remains the sole custodian of that number between connection
and payout, exactly as it already is for the Shop/Education/Health
subaccount flows in payout_accounts.py.

Stripe: a standard Connect platform-to-connected-account Transfer
(`stripe.Transfer.create(destination=...)`) from KIS's own Stripe balance
to the Express account created in stripe_connect.py — the same mechanism
Stripe's own docs describe for paying referral/affiliate commissions, and
unrelated to how the underlying qualifying payment was originally
processed (today, exclusively via Flutterwave — see
apps.billing.views.WalletViewSet.upgrade). KIS funding these transfers out
of its own settled balance is ordinary platform revenue-sharing, not
money transmission on behalf of a third party.

NOT live-smoke-tested against Flutterwave's real Beneficiaries/Transfers
endpoints or a real Stripe Transfer as of this writing — covered here by
mocked-response unit tests only, matching this codebase's existing
convention for provider integrations (see
apps.billing.test_flutterwave_webhook_cross_reconciliation). Verify in
the provider sandbox before the first real payout run, the same way
apps.billing.payout_accounts/stripe_connect were originally verified.
"""
from __future__ import annotations

import logging

import requests
from django.conf import settings
from rest_framework.exceptions import ValidationError

from .direct_payments import FLW_BASE_URL
from .payout_accounts import flutterwave_headers

logger = logging.getLogger(__name__)


def create_flutterwave_beneficiary(
    *,
    account_bank: str,
    account_number: str,
    beneficiary_name: str,
) -> str:
    """Returns the new beneficiary id. Raises ValidationError with a
    user-facing message on any failure, matching
    create_flutterwave_subaccount's error-handling shape exactly."""
    if not getattr(settings, "FLW_SECRET_KEY", None):
        raise ValidationError({"detail": "Payment provider is not configured."})

    try:
        response = requests.post(
            f"{FLW_BASE_URL}/beneficiaries",
            json={
                "account_bank": account_bank,
                "account_number": account_number,
                "beneficiary_name": beneficiary_name or "KIS referrer",
            },
            headers=flutterwave_headers(),
            timeout=30,
        )
        payload = response.json() if response.content else {}
    except (requests.RequestException, ValueError) as exc:
        raise ValidationError({"detail": f"Could not reach the payment provider: {exc}"})

    if response.status_code >= 400 or payload.get("status") != "success":
        message = payload.get("message") or "Unable to connect payout account."
        raise ValidationError({"detail": message})

    data = payload.get("data") or {}
    beneficiary_id = str(data.get("id") or "")
    if not beneficiary_id:
        raise ValidationError({"detail": "Payment provider did not return a beneficiary id."})
    return beneficiary_id


def get_flutterwave_beneficiary(beneficiary_id: str) -> dict:
    """Re-fetches account_bank/account_number from Flutterwave at payout
    time — see module docstring for why these are never stored locally.
    Raises ValidationError if the beneficiary cannot be found/read."""
    try:
        response = requests.get(
            f"{FLW_BASE_URL}/beneficiaries/{beneficiary_id}",
            headers=flutterwave_headers(),
            timeout=30,
        )
        payload = response.json() if response.content else {}
    except (requests.RequestException, ValueError) as exc:
        raise ValidationError({"detail": f"Could not reach the payment provider: {exc}"})

    if response.status_code >= 400 or payload.get("status") != "success":
        message = payload.get("message") or "Unable to read payout destination."
        raise ValidationError({"detail": message})

    data = payload.get("data") or {}
    return {
        "account_bank": str(data.get("account_bank") or ""),
        "account_number": str(data.get("account_number") or ""),
        "full_name": str(data.get("full_name") or data.get("beneficiary_name") or ""),
    }


def find_flutterwave_transfer_by_reference(reference: str) -> dict | None:
    """Looks up a previously-initiated transfer by OUR merchant reference
    (`GET /v3/transfers?reference=...`) — the reconciliation step a crash
    or a lost HTTP response requires, per Flutterwave's own documented
    query support (the `reference` query param). Returns
    {"id": str, "status": str} for the first match, or None if no transfer
    with this reference exists yet (meaning it's genuinely safe to
    initiate one — the earlier attempt never reached Flutterwave at all,
    or hasn't been recorded yet). Raises ValidationError only on a real
    API error, never on "not found", which is a normal, expected result
    here, not a failure."""
    if not getattr(settings, "FLW_SECRET_KEY", None):
        raise ValidationError({"detail": "Payment provider is not configured."})

    try:
        response = requests.get(
            f"{FLW_BASE_URL}/transfers",
            params={"reference": reference},
            headers=flutterwave_headers(),
            timeout=30,
        )
        payload = response.json() if response.content else {}
    except (requests.RequestException, ValueError) as exc:
        raise ValidationError({"detail": f"Could not reach the payment provider: {exc}"})

    if response.status_code >= 400 or payload.get("status") != "success":
        message = payload.get("message") or "Unable to query payout status."
        raise ValidationError({"detail": message})

    data = payload.get("data") or []
    matches = [row for row in data if str(row.get("reference") or "") == reference]
    if not matches:
        return None
    # Most recent first — there should only ever be one match (reference
    # is meant to be unique per Flutterwave's own contract), but take the
    # latest defensively rather than assume.
    latest = max(matches, key=lambda row: str(row.get("id") or ""))
    return {"id": str(latest.get("id") or ""), "status": str(latest.get("status") or "").upper()}


def initiate_flutterwave_transfer(
    *,
    beneficiary_id: str,
    amount_cents: int,
    currency: str,
    reference: str,
    narration: str,
) -> str:
    """Initiates a transfer to a previously-connected beneficiary. Returns
    Flutterwave's transfer id (`data.id`) — NOT proof of completion; the
    transfer is only confirmed PAID once Flutterwave's own
    `transfer.completed` webhook (verif-hash verified, same as every other
    Flutterwave webhook in this codebase) confirms `status: SUCCESSFUL` —
    see apps.referrals.commission_services.confirm_commission_payout.
    Flutterwave settles most payouts in NGN/local currency regardless of
    the commission's own ledger currency (USD); `currency` here is the
    PAYOUT currency Flutterwave will actually disburse, which the caller
    is responsible for having already confirmed with the connected
    account's country/bank.

    The caller (apps.referrals.commission_services.initiate_commission_payout)
    is responsible for calling find_flutterwave_transfer_by_reference
    first and only calling this when nothing was found — this function
    itself does not do that lookup, so it must never be called twice with
    the same `reference` expecting Flutterwave to silently no-op; the
    X-Idempotency-Key header below is a second, provider-side safety net
    for the case where two callers race past that check concurrently, not
    a substitute for it."""
    beneficiary = get_flutterwave_beneficiary(beneficiary_id)
    if not beneficiary["account_bank"] or not beneficiary["account_number"]:
        raise ValidationError({"detail": "Payout destination is missing bank details."})

    try:
        response = requests.post(
            f"{FLW_BASE_URL}/transfers",
            json={
                "account_bank": beneficiary["account_bank"],
                "account_number": beneficiary["account_number"],
                "amount": amount_cents / 100,
                "currency": currency,
                "reference": reference,
                "narration": narration,
                "beneficiary_name": beneficiary["full_name"] or "KIS referrer",
            },
            headers={**flutterwave_headers(), "X-Idempotency-Key": reference},
            timeout=30,
        )
        payload = response.json() if response.content else {}
    except (requests.RequestException, ValueError) as exc:
        raise ValidationError({"detail": f"Could not reach the payment provider: {exc}"})

    if response.status_code >= 400 or payload.get("status") != "success":
        message = payload.get("message") or "Unable to initiate payout."
        raise ValidationError({"detail": message})

    data = payload.get("data") or {}
    transfer_id = str(data.get("id") or "")
    if not transfer_id:
        raise ValidationError({"detail": "Payment provider did not return a transfer id."})
    return transfer_id


def find_stripe_transfer_by_reference(reference: str) -> dict | None:
    """Looks up a previously-created Transfer by OUR reference, via
    Stripe's documented `transfer_group` list filter
    (stripe.Transfer.list(transfer_group=...)) — create_stripe_transfer
    below always sets transfer_group=reference. Not required for the
    automatic retry path (create_stripe_transfer's idempotency_key
    already makes a same-reference retry safe at the Stripe API level —
    this is Stripe's own documented idempotency guarantee, not something
    this codebase has to implement itself), but gives admin recovery
    (apps.referrals.commission_services.reconcile_commission_payout) the
    same provider-lookup capability Flutterwave has, for a commission
    where payout_reference was never captured locally."""
    from .stripe_payments import _stripe

    stripe = _stripe()
    try:
        transfers = stripe.Transfer.list(transfer_group=reference, limit=1)
    except Exception as exc:
        message = getattr(exc, "user_message", None) or str(exc)
        raise ValidationError({"detail": f"Stripe transfer lookup failed: {message}"})

    rows = list(transfers.get("data") or [])
    if not rows:
        return None
    transfer = rows[0]
    # Stripe Transfers have no separate "status" field the way Flutterwave
    # transfers do — existing and not `reversed` is the success signal;
    # a created-but-unreversed Transfer already moved the funds (Stripe
    # Transfers settle synchronously, see create_stripe_transfer's own
    # docstring).
    reversed_amount = int(getattr(transfer, "amount_reversed", 0) or 0)
    return {
        "id": str(getattr(transfer, "id", "") or ""),
        "status": "REVERSED" if reversed_amount > 0 else "SUCCESSFUL",
    }


def create_stripe_transfer(
    *,
    destination_account_id: str,
    amount_cents: int,
    currency: str = "usd",
    reference: str = "",
    description: str = "",
) -> str:
    """Platform-to-connected-account Transfer from KIS's own Stripe
    balance — see module docstring. Returns the Transfer id (`tr_...`).
    Unlike the Flutterwave path above, Stripe Transfers settle
    synchronously against the platform balance (no separate confirmation
    webhook needed to know the transfer itself succeeded — a thrown
    StripeError here IS the failure signal); whether the CONNECTED
    account can subsequently pay itself out to its own bank is governed
    by that account's own payouts_enabled status, tracked separately via
    the existing account.updated webhook handling in
    apps.billing.views._sync_stripe_connect_account_status."""
    from .stripe_payments import _stripe

    stripe = _stripe()
    try:
        transfer = stripe.Transfer.create(
            amount=amount_cents,
            currency=currency,
            destination=destination_account_id,
            transfer_group=reference or None,
            description=description or None,
            idempotency_key=f"referral_commission_payout:{reference}" if reference else None,
        )
    except Exception as exc:  # stripe.error.StripeError and friends
        message = getattr(exc, "user_message", None) or str(exc)
        raise ValidationError({"detail": f"Stripe transfer failed: {message}"})

    transfer_id = str(getattr(transfer, "id", "") or "")
    if not transfer_id:
        raise ValidationError({"detail": "Stripe did not return a transfer id."})
    return transfer_id
