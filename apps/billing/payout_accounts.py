# apps/billing/payout_accounts.py
"""Shared Flutterwave subaccount-connection logic, reused by every
seller/provider payout-account connect endpoint (Education, Market,
Health, Broadcast). Only the platform's own FLW_SECRET_KEY is ever used —
sellers/providers never provide or store a provider secret key, matching
Flutterwave's Subaccounts API
(https://developer.flutterwave.com/docs/subaccounts): creating a
subaccount on our platform account returns a subaccount id that later
payment links can reference in their `subaccounts` split array. Only that
id (and display-safe fields) are ever persisted by callers — the raw bank
account number submitted to this function is never stored.

apps.broadcasts.views.EducationInstitutionPayoutAccountConnectView predates
this module and has its own equivalent inline implementation — left
untouched (already implemented, tested, and in the plan/verification
record) rather than refactored onto this shared helper, to avoid any risk
to that already-working path; it does import find_existing_flutterwave_subaccount
below for the duplicate-subaccount fallback so both call sites recover the
same way. Every new connect view added after it should use this shared
helper instead of duplicating the Flutterwave call again.
"""
from __future__ import annotations

import requests
from django.conf import settings
from rest_framework.exceptions import ValidationError

from .direct_payments import FLW_BASE_URL


def flutterwave_headers() -> dict[str, str]:
    secret = getattr(settings, "FLW_SECRET_KEY", "")
    return {
        "Authorization": f"Bearer {secret}",
        "Content-Type": "application/json",
    }


def find_existing_flutterwave_subaccount(account_bank: str, account_number: str) -> str | None:
    """Flutterwave enforces global (account_bank, account_number)
    uniqueness across ALL subaccounts on our platform account — two
    different KIS sellers/providers who happen to share a real bank
    account (a family, a church's shared account, or two of our own test
    fixtures reusing the same sandbox test account number) get a hard
    "subaccount already exists" rejection on the second attempt, with no
    id returned to fall back to. Flutterwave does not expose a
    lookup-by-account-number endpoint, so this pages through
    GET /subaccounts (their only listing endpoint) to find the existing
    one and hand back its id instead of failing the second seller's setup
    outright. Returns None if not found (surfaces the original error)."""
    page = 1
    while page <= 20:  # hard cap - this list only grows by our own connects
        try:
            response = requests.get(
                f"{FLW_BASE_URL}/subaccounts",
                params={"page": page},
                headers=flutterwave_headers(),
                timeout=30,
            )
            payload = response.json() if response.content else {}
        except (requests.RequestException, ValueError):
            return None
        if response.status_code >= 400 or payload.get("status") != "success":
            return None
        for entry in payload.get("data") or []:
            if (
                str(entry.get("account_number") or "") == account_number
                and str(entry.get("account_bank") or "") == account_bank
            ):
                subaccount_id = str(entry.get("subaccount_id") or entry.get("id") or "")
                return subaccount_id or None
        total_pages = int((payload.get("meta") or {}).get("page_info", {}).get("total_pages") or 1)
        if page >= total_pages:
            return None
        page += 1
    return None


def create_flutterwave_subaccount(
    *,
    account_bank: str,
    account_number: str,
    business_name: str,
    business_email: str,
    country: str = "NG",
) -> str:
    """Returns the new subaccount id. Raises ValidationError with a
    user-facing message on any failure (provider not configured, network
    failure, provider rejection, or a malformed success response)."""
    if not getattr(settings, "FLW_SECRET_KEY", None):
        raise ValidationError({"detail": "Payment provider is not configured."})

    try:
        response = requests.post(
            f"{FLW_BASE_URL}/subaccounts",
            json={
                "account_bank": account_bank,
                "account_number": account_number,
                "business_name": business_name,
                "business_email": business_email,
                "country": country,
                "split_type": "percentage",
                # Flutterwave's own share of each split transaction; the
                # platform's cut is applied per-transaction via the
                # `subaccounts[].transaction_charge` on the payment link
                # itself, not here, so this default doesn't double-charge.
                "split_value": 0,
            },
            headers=flutterwave_headers(),
            timeout=30,
        )
        payload = response.json() if response.content else {}
    except (requests.RequestException, ValueError) as exc:
        raise ValidationError({"detail": f"Could not reach the payment provider: {exc}"})

    if response.status_code >= 400 or payload.get("status") != "success":
        message = payload.get("message") or "Unable to connect payout account."
        if "already exist" in message.lower():
            existing_id = find_existing_flutterwave_subaccount(account_bank, account_number)
            if existing_id:
                return existing_id
        raise ValidationError({"detail": message})

    data = payload.get("data") or {}
    subaccount_id = str(data.get("subaccount_id") or data.get("id") or "")
    if not subaccount_id:
        raise ValidationError({"detail": "Payment provider did not return a subaccount id."})
    return subaccount_id
