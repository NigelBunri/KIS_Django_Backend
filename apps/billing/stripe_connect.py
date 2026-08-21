# apps/billing/stripe_connect.py
"""Stripe Connect (Express accounts) — the Stripe-side counterpart to
apps.billing.payout_accounts' Flutterwave subaccount helpers. Shared by
every seller/provider payout-account connect endpoint (Market, Education,
Health, Broadcast), same as the Flutterwave helper is. Only the
platform's own STRIPE_SECRET_KEY is ever used — sellers/providers never
provide or store a provider secret key. A connected account's id
(`acct_...`) and onboarding/capability status flags are the only things
ever persisted by callers; Stripe holds all sensitive KYC/bank data on
its own onboarding-hosted pages, none of it ever passes through our
backend.
"""
from __future__ import annotations

import logging

from django.conf import settings
from rest_framework.exceptions import ValidationError

from .stripe_payments import _stripe, is_configured  # noqa: F401 - is_configured re-exported for callers

logger = logging.getLogger(__name__)


def _stripe_call(fn, *, action: str):
    """Runs a Stripe SDK call, translating any stripe.StripeError into a
    user-facing ValidationError instead of letting it bubble up as an
    unhandled 500 — mirrors apps.billing.payout_accounts.
    create_flutterwave_subaccount's own try/except around its provider
    call. Stripe's own error message (e.g. "you haven't signed up for
    Connect yet") is almost always more actionable to the caller than a
    generic one, so it's surfaced directly rather than replaced."""
    import stripe as stripe_lib

    try:
        return fn()
    except stripe_lib.StripeError as exc:
        message = getattr(exc, "user_message", None) or str(exc) or f"Stripe {action} failed."
        logger.warning("[Stripe Connect] %s failed: %s", action, message)
        raise ValidationError({"detail": message})


def create_stripe_express_account(*, email: str, country: str = "US") -> str:
    """Creates a new Stripe Express connected account and returns its id.
    Express (not Standard/Custom) matches the platform's needs: Stripe
    hosts the full onboarding/KYC flow (see create_account_onboarding_link),
    we only need the resulting charges_enabled/payouts_enabled status."""
    stripe = _stripe()

    def _create():
        return stripe.Account.create(
            type="express",
            country=(country or "US").upper()[:2],
            email=email or None,
            capabilities={
                "card_payments": {"requested": True},
                "transfers": {"requested": True},
            },
        )

    account = _stripe_call(_create, action="account creation")
    return str(account.id)


def create_account_onboarding_link(*, account_id: str, refresh_url: str, return_url: str) -> str:
    """Returns a one-time-use hosted onboarding URL for this Express
    account. refresh_url is where Stripe sends the user back to if the
    link expires or onboarding needs to restart; return_url is where they
    land after completing (or abandoning) the flow — neither implies
    onboarding actually finished, which is why the caller should still
    treat status as unconfirmed until account.updated arrives or
    refresh_account_status is called."""
    stripe = _stripe()

    def _create_link():
        return stripe.AccountLink.create(
            account=account_id,
            refresh_url=refresh_url,
            return_url=return_url,
            type="account_onboarding",
        )

    link = _stripe_call(_create_link, action="onboarding link creation")
    return str(link.url)


def refresh_account_status(account_id: str) -> dict:
    """Authoritative status check — queries Stripe directly rather than
    trusting anything the frontend or a redirect landing page claims,
    matching how apps.billing.direct_payments.verify_flutterwave_transaction
    is the only trusted source for Flutterwave. Returns a dict with the
    same field names persisted on each payout-holder model, so callers can
    apply it directly."""
    stripe = _stripe()
    account = _stripe_call(lambda: stripe.Account.retrieve(account_id), action="status refresh")
    return {
        "stripe_charges_enabled": bool(getattr(account, "charges_enabled", False)),
        "stripe_payouts_enabled": bool(getattr(account, "payouts_enabled", False)),
        "stripe_details_submitted": bool(getattr(account, "details_submitted", False)),
    }


def onboarding_redirect_urls() -> tuple[str, str]:
    """(refresh_url, return_url) for onboarding links — same
    getattr-with-fallback pattern as STRIPE_CHECKOUT_SUCCESS_URL/
    STRIPE_CHECKOUT_CANCEL_URL in direct_payments.py, since neither var is
    required to be set in every environment."""
    refresh_url = getattr(settings, "STRIPE_CONNECT_REFRESH_URL", "") or getattr(
        settings, "FLW_REDIRECT_URL", ""
    ) or "https://kis.app/payments/complete"
    return_url = getattr(settings, "STRIPE_CONNECT_RETURN_URL", "") or refresh_url
    return refresh_url, return_url
