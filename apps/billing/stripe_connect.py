# apps/billing/stripe_connect.py
"""Stripe Connect account management — the Stripe-side counterpart to
apps.billing.payout_accounts' Flutterwave subaccount helpers. Shared by
every seller/provider payout-account connect endpoint (Market, Education,
Health, Broadcast), same as the Flutterwave helper is. Only the
platform's own STRIPE_SECRET_KEY is ever used — sellers/providers never
provide or store a provider secret key. A connected account's id
(`acct_...`) and onboarding/capability status flags are the only things
ever persisted by callers; Stripe holds all sensitive KYC/bank data on
its own onboarding-hosted pages, none of it ever passes through our
backend.

Uses Stripe's Accounts v2 API (POST /v2/core/accounts) directly via raw
HTTP, NOT the `stripe` Python SDK's v1 `stripe.Account.*` resource
methods — Stripe now rejects v1 account creation for new Connect
integrations ("Stripe no longer recommends Accounts v1 for new Connect
integrations"), and the pinned SDK version in requirements.txt (12.2.0)
has no Python bindings for v2 Accounts yet (confirmed: `stripe.v2.core`
exists but has no `Account`/`AccountService`). Raw HTTP against Stripe's
documented, stable v2 REST endpoints — the same `requests`-based pattern
already used for Flutterwave in payout_accounts.py — avoids depending on
whichever version of SDK bindings happens to exist, so this doesn't need
another rewrite the next time the SDK lags the API.

KIS uses Stripe's "recipient" account configuration specifically (not
"merchant"): sellers here only ever RECEIVE a transferred share of a
charge the platform's own account processes (Destination Charges — see
apps.billing.direct_payments._create_stripe_checkout_session's
transfer_data/application_fee_amount), they never process their own
card charges directly, so the merchant persona doesn't apply. The
relevant v2 capability is `stripe_balance.stripe_transfers`.

Payments themselves (PaymentIntent, Checkout Session, webhook signature
verification) are UNAFFECTED by any of this and keep using the `stripe`
SDK's v1 methods in stripe_payments.py — only Account creation was
deprecated, v1 payment endpoints remain fully supported indefinitely and
work identically against v2-created connected accounts (that's the
explicit interoperability point of the v2 Accounts redesign).
"""
from __future__ import annotations

import logging

import requests
from django.conf import settings
from rest_framework.exceptions import ValidationError

from .stripe_payments import is_configured  # noqa: F401 - re-exported for callers

logger = logging.getLogger(__name__)

STRIPE_API_BASE = "https://api.stripe.com"
# Pinned Stripe API version for all v2 calls — v2 endpoints don't fall
# back to an account-level default version the way v1 does, so every
# request must name one explicitly. Bump deliberately (test in sandbox
# first) rather than following "latest" automatically.
STRIPE_API_VERSION = "2026-07-29.dahlia"


def _stripe_v2_headers() -> dict[str, str]:
    secret = getattr(settings, "STRIPE_SECRET_KEY", "") or ""
    if not secret:
        raise RuntimeError("STRIPE_SECRET_KEY is not configured.")
    return {
        "Authorization": f"Bearer {secret}",
        "Stripe-Version": STRIPE_API_VERSION,
        "Content-Type": "application/json",
    }


def _v2_request(method: str, path: str, *, action: str, json_body: dict | None = None, params=None) -> dict:
    """Raises ValidationError with Stripe's own error message on any
    failure (provider not configured, network failure, provider
    rejection, or a malformed response) — mirrors
    apps.billing.payout_accounts.create_flutterwave_subaccount's error
    handling exactly, so every payout-connect endpoint fails the same
    clean way regardless of provider."""
    try:
        response = requests.request(
            method,
            f"{STRIPE_API_BASE}{path}",
            json=json_body,
            params=params,
            headers=_stripe_v2_headers(),
            timeout=30,
        )
        payload = response.json() if response.content else {}
    except (requests.RequestException, ValueError) as exc:
        raise ValidationError({"detail": f"Could not reach Stripe: {exc}"})

    if response.status_code >= 400:
        message = (payload.get("error") or {}).get("message") or f"Stripe {action} failed."
        logger.warning("[Stripe Connect v2] %s failed: %s", action, message)
        raise ValidationError({"detail": message})
    return payload


def create_stripe_express_account(*, email: str, country: str = "US") -> str:
    """Creates a new Stripe v2 Account configured as a "recipient" (can
    receive transfers into a Stripe balance) and returns its id.
    dashboard="express" gives the seller a self-serve hosted dashboard to
    check their payout status — Express dashboard access requires the
    platform (not Stripe) to be the fees/losses collector, which is what
    responsibilities.fees_collector/losses_collector="application" below
    declares.

    Also requests configuration.merchant.capabilities.card_payments
    alongside the recipient capability — confirmed live against this
    platform account that stripe_balance.stripe_transfers is rejected
    without it ("cannot be requested without the
    configuration.merchant.capabilities.card_payments capability").
    KIS still never routes a charge through the connected account
    directly (see the module docstring — Destination Charges keep the
    platform's own account as merchant of record); this is a
    platform-level activation precondition for holding a Stripe balance
    via transfers, not a change to how charges are actually processed."""
    body = {
        "contact_email": email or None,
        "dashboard": "express",
        "configuration": {
            "recipient": {
                "capabilities": {
                    "stripe_balance": {"stripe_transfers": {"requested": True}},
                },
            },
            "merchant": {
                "capabilities": {
                    "card_payments": {"requested": True},
                },
            },
        },
        "defaults": {
            "responsibilities": {"fees_collector": "application", "losses_collector": "application"},
        },
        "identity": {"country": (country or "US").lower()},
        "include": ["configuration.recipient", "configuration.merchant"],
    }
    data = _v2_request("POST", "/v2/core/accounts", action="account creation", json_body=body)
    account_id = str(data.get("id") or "")
    if not account_id:
        raise ValidationError({"detail": "Stripe did not return an account id."})
    return account_id


def create_account_onboarding_link(*, account_id: str, refresh_url: str, return_url: str) -> str:
    """Returns a one-time-use hosted onboarding URL for this account via
    the v2 Account Links API (POST /v2/core/account_links — a v2-native
    endpoint, not the older v1 /v1/account_links, since it natively
    understands v2 configurations like "recipient"). refresh_url is
    where Stripe sends the user back to if the link expires or
    onboarding needs to restart; return_url is where they land after
    completing (or abandoning) the flow — neither implies onboarding
    actually finished, which is why the caller should still treat status
    as unconfirmed until refresh_account_status is called."""
    body = {
        "account": account_id,
        "use_case": {
            "type": "account_onboarding",
            "account_onboarding": {
                # Both configurations requested at account-creation time
                # (see create_stripe_express_account) need their
                # requirements collected here, or onboarding will
                # complete only the recipient side and leave the
                # merchant.card_payments capability (a hard precondition
                # for stripe_balance.stripe_transfers on this platform)
                # permanently pending.
                "configurations": ["recipient", "merchant"],
                "return_url": return_url,
                "refresh_url": refresh_url,
            },
        },
    }
    data = _v2_request("POST", "/v2/core/account_links", action="onboarding link creation", json_body=body)
    url = str(data.get("url") or "")
    if not url:
        raise ValidationError({"detail": "Stripe did not return an onboarding URL."})
    return url


def refresh_account_status(account_id: str) -> dict:
    """Authoritative status check — queries Stripe directly rather than
    trusting anything the frontend, a redirect landing page, or a
    webhook payload claims, matching how
    apps.billing.direct_payments.verify_flutterwave_transaction is the
    only trusted source for Flutterwave. Returns a dict with the same
    field names persisted on each payout-holder model, so callers can
    apply it directly.

    stripe_charges_enabled and stripe_payouts_enabled are both mapped to
    the same underlying capability (stripe_balance.stripe_transfers) —
    for a recipient-only account there's one capability that actually
    matters to KIS: can this account receive a destination-charge
    transfer at all. That's a deliberate simplification versus v1's
    separate charges_enabled/payouts_enabled booleans (which meant
    something different for merchant-persona accounts); a future
    integration that also needs real bank-payout-method status would
    extend this rather than change what these two fields mean elsewhere
    in the codebase.
    """
    data = _v2_request(
        "GET",
        f"/v2/core/accounts/{account_id}",
        action="status refresh",
        params=[
            ("include[]", "configuration.recipient"),
            ("include[]", "configuration.merchant"),
            ("include[]", "requirements"),
        ],
    )
    capability = (
        (data.get("configuration") or {})
        .get("recipient", {})
        .get("capabilities", {})
        .get("stripe_balance", {})
        .get("stripe_transfers", {})
    )
    ready = capability.get("status") == "active"
    currently_due = (data.get("requirements") or {}).get("currently_due") or []
    return {
        "stripe_charges_enabled": ready,
        "stripe_payouts_enabled": ready,
        "stripe_details_submitted": len(currently_due) == 0,
    }


def onboarding_redirect_urls() -> tuple[str, str]:
    """(refresh_url, return_url) for onboarding links — same
    getattr-with-fallback pattern as STRIPE_CHECKOUT_SUCCESS_URL/
    STRIPE_CHECKOUT_CANCEL_URL in direct_payments.py, since neither var is
    required to be set in every environment. Deliberately NOT
    FLW_REDIRECT_URL as a fallback (that was tried and is wrong) — it
    points at /payments/complete, a buyer-checkout status page keyed by
    a tx_ref query param that a seller landing here from Stripe
    onboarding will never have, producing a confusing "we couldn't find
    your payment reference" message unrelated to what they just did. The
    website's own /payments/onboarding-complete page exists specifically
    for this landing instead."""
    refresh_url = getattr(settings, "STRIPE_CONNECT_REFRESH_URL", "") or "https://kingdomimpactventures.org/payments/onboarding-complete"
    return_url = getattr(settings, "STRIPE_CONNECT_RETURN_URL", "") or refresh_url
    return refresh_url, return_url
