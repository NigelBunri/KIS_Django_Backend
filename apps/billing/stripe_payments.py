"""
Stripe payment integration for KIS.
Supports PaymentIntent (card payments) and Checkout Sessions (hosted page).
All functions are no-ops if STRIPE_SECRET_KEY is not configured.
"""
from __future__ import annotations

import logging
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)


def _stripe():
    """Lazy-import stripe and configure the API key."""
    try:
        import stripe as _stripe_lib
    except ImportError:
        raise RuntimeError("stripe package is not installed. Add stripe to requirements.txt.")
    key = getattr(settings, "STRIPE_SECRET_KEY", "") or ""
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY is not configured.")
    _stripe_lib.api_key = key
    return _stripe_lib


def is_configured() -> bool:
    return bool(getattr(settings, "STRIPE_SECRET_KEY", "") and getattr(settings, "KIS_STRIPE_ENABLED", True))


def create_payment_intent(
    *,
    amount_cents: int,
    currency: str = "usd",
    target_type: str = "",
    target_id: str = "",
    user_id: str = "",
    description: str = "",
    metadata: dict | None = None,
) -> dict[str, Any]:
    """Create a Stripe PaymentIntent. Returns dict with client_secret and intent id."""
    stripe = _stripe()
    intent = stripe.PaymentIntent.create(
        amount=int(amount_cents),
        currency=currency.lower(),
        payment_method_types=["card"],
        description=description or f"KIS {target_type} payment",
        metadata={
            "target_type": target_type,
            "target_id": str(target_id),
            "user_id": str(user_id),
            **(metadata or {}),
        },
    )
    return {
        "provider": "stripe",
        "intent_id": intent.id,
        "client_secret": intent.client_secret,
        "amount": amount_cents,
        "currency": currency,
        "status": intent.status,
    }


def create_checkout_session(
    *,
    amount_cents: int,
    currency: str = "usd",
    product_name: str = "KIS Purchase",
    success_url: str,
    cancel_url: str,
    target_type: str = "",
    target_id: str = "",
    user_id: str = "",
    metadata: dict | None = None,
    destination_account_id: str = "",
    application_fee_amount: int = 0,
) -> dict[str, Any]:
    """Create a Stripe Checkout Session (hosted payment page). Returns
    checkout URL. destination_account_id/application_fee_amount are
    optional Stripe Connect Destination Charge params — when
    destination_account_id is set, the seller's connected account
    receives the payment directly and application_fee_amount (cents) is
    what the platform keeps, mirroring the marketplace split Flutterwave
    already does for its own subaccounts (see
    apps.billing.direct_payments._split_subaccounts_for_intent)."""
    stripe = _stripe()
    payment_metadata = {
        "target_type": target_type,
        "target_id": str(target_id),
        "user_id": str(user_id),
        **(metadata or {}),
    }
    payment_intent_data: dict[str, Any] = {"metadata": payment_metadata}
    if destination_account_id:
        payment_intent_data["transfer_data"] = {"destination": destination_account_id}
        if application_fee_amount > 0:
            payment_intent_data["application_fee_amount"] = int(application_fee_amount)
    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": currency.lower(),
                "product_data": {"name": product_name},
                "unit_amount": int(amount_cents),
            },
            "quantity": 1,
        }],
        mode="payment",
        success_url=success_url,
        cancel_url=cancel_url,
        metadata=payment_metadata,
        payment_intent_data=payment_intent_data,
    )
    return {
        "provider": "stripe",
        "session_id": session.id,
        "checkout_url": session.url,
        "amount": amount_cents,
        "currency": currency,
    }


def verify_webhook(payload: bytes, sig_header: str) -> dict | None:
    """Verify and parse a Stripe webhook event. Returns event dict or None on failure."""
    stripe = _stripe()
    secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "") or ""
    if not secret:
        logger.error(
            "STRIPE_WEBHOOK_SECRET is not configured. Refusing to process unverified webhook. "
            "Set STRIPE_WEBHOOK_SECRET in your environment to enable Stripe webhooks."
        )
        return None  # Reject — never accept unverified payment events in production
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
        return dict(event)
    except Exception as exc:
        logger.warning("Stripe webhook verification failed: %s", exc)
        return None


def retrieve_payment_intent(intent_id: str) -> dict | None:
    """Retrieve a PaymentIntent by ID to check its status."""
    try:
        stripe = _stripe()
        intent = stripe.PaymentIntent.retrieve(intent_id)
        return {"status": intent.status, "amount": intent.amount, "currency": intent.currency, "id": intent.id}
    except Exception as exc:
        logger.warning("Stripe retrieve PaymentIntent %s failed: %s", intent_id, exc)
        return None
