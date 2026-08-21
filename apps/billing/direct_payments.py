from __future__ import annotations

import logging
import uuid
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.core.money import KISC_MICRO_PER_USD_CENT

from .models import DirectPaymentAuditEvent, DirectPaymentIntent

logger = logging.getLogger(__name__)

FLW_BASE_URL = "https://api.flutterwave.com/v3"

SENSITIVE_KEYS = {
    "authorization",
    "card",
    "cvv",
    "secret",
    "token",
    "pin",
    "otp",
    "flw_ref",
    "account_number",
    "customer_phone",
    "patient_phone",
    "patient_health_record",
    "health_record",
    "medical_record",
    "private_health_record",
}


def redact_payment_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in SENSITIVE_KEYS or any(token in key_text.lower() for token in ("secret", "token", "authorization")):
                redacted[key_text] = "[redacted]"
            else:
                redacted[key_text] = redact_payment_payload(item)
        return redacted
    if isinstance(value, list):
        return [redact_payment_payload(item) for item in value[:50]]
    return value


def write_direct_payment_audit(
    *,
    event: str,
    intent: DirectPaymentIntent | None = None,
    provider: str = "flutterwave",
    tx_ref: str = "",
    target_type: str = "",
    target_id: uuid.UUID | str | None = None,
    status: str = "",
    actor=None,
    metadata: dict | None = None,
) -> DirectPaymentAuditEvent:
    target_uuid = None
    if target_id:
        try:
            target_uuid = uuid.UUID(str(target_id))
        except (TypeError, ValueError):
            target_uuid = None
    return DirectPaymentAuditEvent.objects.create(
        intent=intent,
        event=event,
        provider=provider or (intent.provider if intent else "flutterwave"),
        tx_ref=tx_ref or (intent.tx_ref if intent else ""),
        target_type=target_type or (intent.target_type if intent else ""),
        target_id=target_uuid or (intent.target_id if intent else None),
        status=status or (intent.status if intent else ""),
        actor=actor,
        metadata=redact_payment_payload(metadata or {}),
    )


def _flutterwave_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.FLW_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def verify_flutterwave_transaction(transaction_id: str) -> dict:
    """Server-to-server verification against Flutterwave's own
    GET /transactions/:id/verify endpoint — the only trustworthy source of
    truth for what actually happened to a payment. Used as a fallback when
    a webhook hasn't (yet) landed — see PaymentStatusView, which the public
    payments/complete redirect page polls. Never construct a "successful"
    outcome from a client-supplied status query param; this function's
    return value, verified with our own secret key against Flutterwave
    directly, is the only thing allowed to drive reconciliation from a
    redirect landing page."""
    if not str(transaction_id or "").strip():
        raise ValueError("transaction_id is required.")
    response = requests.get(
        f"{FLW_BASE_URL}/transactions/{transaction_id}/verify",
        headers=_flutterwave_headers(),
        timeout=30,
    )
    data = response.json() if response.content else {}
    if response.status_code >= 300:
        raise ValueError(str(data.get("message") or "Unable to verify transaction with the payment provider."))
    return data.get("data") or {}


def _provider_links_enabled(provider: str) -> bool:
    if not getattr(settings, "KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED", False):
        return False
    if provider == "flutterwave":
        return bool(getattr(settings, "FLW_SECRET_KEY", ""))
    if provider == "stripe":
        try:
            from apps.billing.stripe_payments import is_configured as stripe_is_configured
        except Exception:
            return False
        return stripe_is_configured()
    return False


def resolve_payout_entity(target_type: str, target_id: uuid.UUID | str):
    """Resolves the seller/provider entity (Shop/EducationInstitution/
    HealthInstitution/BroadcastChannel) that owns a given payment target —
    one model instance per DirectPaymentIntent target type, each exposing
    the same flutterwave_subaccount_id/stripe_*/payout_account_status/
    owner shape. Returns None if the target isn't a type that has a
    payout-holding entity at all (e.g. an institution-owned broadcast
    channel, which settles via its owning institution instead — see the
    owner_type check below), regardless of whether that entity's payout
    account is actually ready yet. Callers that care about readiness
    (payout splitting, payment eligibility gating) check the resolved
    entity's own status fields themselves — this function only answers
    "which entity", not "is it ready"."""
    if target_type == DirectPaymentIntent.TARGET_EDUCATION_BOOKING:
        from apps.broadcasts.models import EducationInstitutionBooking

        booking = (
            EducationInstitutionBooking.objects.select_related("institution")
            .filter(id=target_id)
            .first()
        )
        if not booking or not booking.course_id:
            return None
        return booking.institution
    if target_type == DirectPaymentIntent.TARGET_MARKETPLACE_ORDER:
        from apps.commerce.models import MarketplaceOrder

        order = MarketplaceOrder.objects.select_related("shop").filter(id=target_id).first()
        return order.shop if order else None
    if target_type == DirectPaymentIntent.TARGET_SERVICE_BOOKING_PAYMENT:
        from apps.commerce.models import ServiceBookingPayment

        payment = (
            ServiceBookingPayment.objects.select_related("booking__shop")
            .filter(id=target_id)
            .first()
        )
        return payment.booking.shop if payment else None
    if target_type == DirectPaymentIntent.TARGET_HEALTH_BILLING_SESSION:
        from apps.health_ops.models import PaymentBillingSession

        session = (
            PaymentBillingSession.objects.select_related("institution")
            .filter(id=target_id)
            .first()
        )
        return session.institution if session else None
    if target_type == DirectPaymentIntent.TARGET_CHANNEL_MEMBERSHIP:
        from apps.broadcasts.models import BroadcastChannel, ChannelMembership

        membership = (
            ChannelMembership.objects.select_related("tier__channel")
            .filter(id=target_id)
            .first()
        )
        if not membership or membership.tier.channel.owner_type != BroadcastChannel.OwnerType.USER:
            return None
        return membership.tier.channel
    if target_type == DirectPaymentIntent.TARGET_CHANNEL_TIP:
        from apps.broadcasts.models import BroadcastChannel

        channel = _channel_for_tip_target(target_id)
        if not channel or channel.owner_type != BroadcastChannel.OwnerType.USER:
            return None
        return channel
    return None


def _tip_for_target(target_id: uuid.UUID | str):
    """A channel_tip's target_id may belong to either ChannelContentTip
    (Super Thanks) or ChannelLiveStreamTip (Super Chat) — both routed
    through the same DirectPaymentIntent target_type since they're the
    same underlying concept (a one-off tip). UUID collision between the
    two tables is not a realistic concern."""
    from apps.broadcasts.models import ChannelContentTip, ChannelLiveStreamTip

    tip = ChannelContentTip.objects.select_related("content__channel", "user").filter(id=target_id).first()
    if tip is not None:
        return tip
    return ChannelLiveStreamTip.objects.select_related("live_stream__channel", "user").filter(id=target_id).first()


def _channel_for_tip_target(target_id: uuid.UUID | str):
    from apps.broadcasts.models import ChannelContentTip

    tip = _tip_for_target(target_id)
    if tip is None:
        return None
    if isinstance(tip, ChannelContentTip):
        return tip.content.channel
    return tip.live_stream.channel


def _payout_owner_for_intent(intent: DirectPaymentIntent):
    """Resolves the seller/provider whose connected Flutterwave payout
    account (if ACTIVE) this payment should split to, and who owns the
    commission-rate tier. Returns None if the target has no payout entity
    at all, or that entity's Flutterwave subaccount isn't ACTIVE — this is
    the Flutterwave-specific split-eligibility check; the Stripe
    equivalent lives in _create_stripe_checkout_session, and the
    provider-agnostic "can this seller receive money at all" check lives
    in apps.billing.eligibility.can_receive_payments."""
    entity = resolve_payout_entity(intent.target_type, intent.target_id)
    if entity is None:
        return None
    if entity.payout_account_status != _ACTIVE_PAYOUT_STATUS or not entity.flutterwave_subaccount_id:
        return None
    return entity


# Each payout-holder model (Shop/EducationInstitution/HealthInstitution/
# BroadcastChannel) defines its own PayoutAccountStatus TextChoices
# (deliberately not a shared cross-app enum, to avoid import-order risk
# between apps — see each model's own docstring) but ACTIVE is always the
# literal string "active" across all four, so comparing against this
# constant avoids needing an extra import per entity type.
_ACTIVE_PAYOUT_STATUS = "active"


def _payout_entity_owner(entity) -> Any:
    """BroadcastChannel exposes its owner as `owner_user`, not `owner`
    (it's polymorphically owned, unlike Shop/HealthInstitution/
    EducationInstitution) — normalize here so callers can treat every
    payout entity uniformly."""
    return getattr(entity, "owner", None) or getattr(entity, "owner_user", None)


def _commission_pct_for_entity(entity) -> float | None:
    """The platform commission rate for this payment's seller/provider,
    via the one existing rate resolver (apps.accounts.tiers.
    get_platform_commission_pct — tiered by the seller's own AccountTier,
    configurable via settings.PLATFORM_COMMISSION_BY_TIER). Takes an
    already-resolved payout entity rather than re-resolving it from the
    intent, since every caller already has one on hand (either
    _payout_owner_for_intent's Flutterwave-specific resolution, or
    resolve_payout_entity's provider-agnostic one) — avoids a redundant
    second DB lookup, and keeps this usable in tests that mock the entity
    directly without needing a real intent's target_type/target_id.
    Shared by both providers so there is exactly one place the rate is
    looked up — each provider's own payload-builder converts this
    whole-number percentage into whatever shape its own API wants (see
    _split_subaccounts_for_intent for Flutterwave's fraction convention,
    and the Stripe checkout-session builder for its absolute-cents
    application_fee_amount)."""
    if entity is None:
        return None
    from apps.accounts.tiers import get_platform_commission_pct

    return get_platform_commission_pct(_payout_entity_owner(entity))


def _commission_cents_for_entity(intent: DirectPaymentIntent, entity) -> int:
    """Commission amount in cents for this intent against the given
    payout entity, using the same rate as _commission_pct_for_entity —
    the absolute-cents shape Stripe's application_fee_amount wants. 0 if
    entity is None."""
    pct = _commission_pct_for_entity(entity)
    if not pct:
        return 0
    return int(round(int(intent.amount_cents or 0) * pct / 100))


def _split_subaccounts_for_intent(intent: DirectPaymentIntent) -> list[dict] | None:
    """Flutterwave native split: when this payment's seller/provider has
    an ACTIVE connected subaccount, settlement is split automatically at
    the provider level — their bank account receives its share directly,
    no internal transfer/payout code needed on our side. Covers Education
    course bookings, Market orders/service bookings, and Health billing
    sessions (Broadcast tips/memberships are wired separately once they
    route through DirectPaymentIntent — see apps/broadcasts/views.py).
    `transaction_charge` is the percentage kept by the platform's own
    (main) account; the remainder settles to the subaccount — confirmed
    against Flutterwave's v3 Split Payments docs (developer.flutterwave.com/
    v3.0/docs/split-payments), which give `transaction_charge_type:
    "percentage"` as "you (the platform) want to get a percentage of the
    settlement amount". That same doc's examples give `transaction_charge`
    as a decimal FRACTION of the percentage (0.09 for a 9% commission, 0.2
    for 20%), not the whole-number percentage — get_platform_commission_pct
    returns whole numbers (10, 7, 5 — see settings.PLATFORM_COMMISSION_BY_TIER)
    since that's the convention every other caller of it uses (e.g.
    apps.billing.services.release_locked_booking_funds_split divides by 100
    itself), so it's converted to a fraction here, at this API boundary,
    rather than changing what the shared helper returns everywhere else."""
    entity = _payout_owner_for_intent(intent)
    if entity is None:
        return None
    commission_pct = _commission_pct_for_entity(entity) or 0
    return [
        {
            "id": entity.flutterwave_subaccount_id,
            "transaction_charge_type": "percentage",
            "transaction_charge": round(commission_pct / 100, 4),
        }
    ]


def _create_flutterwave_payment_link(intent: DirectPaymentIntent) -> tuple[str, dict]:
    user = intent.user
    amount = (Decimal(int(intent.amount_cents or 0)) / Decimal("100")).quantize(Decimal("0.01"))
    customer = {
        "email": getattr(user, "email", "") or f"{user.id}@kis.local",
        "name": (getattr(user, "get_full_name", lambda: "")() or getattr(user, "username", "") or str(user.id)),
    }
    payload = {
        "tx_ref": intent.tx_ref,
        "amount": str(amount),
        "currency": intent.currency or "USD",
        "redirect_url": settings.FLW_REDIRECT_URL,
        "customer": customer,
        "customizations": {
            "title": "KIS secure checkout",
            "description": str((intent.metadata or {}).get("description") or intent.target_type).replace("_", " ").title(),
        },
        "meta": {
            "intent_id": str(intent.id),
            "target_type": intent.target_type,
            "target_id": str(intent.target_id),
        },
    }
    subaccounts = _split_subaccounts_for_intent(intent)
    if subaccounts:
        payload["subaccounts"] = subaccounts
    response = requests.post(f"{FLW_BASE_URL}/payments", json=payload, headers=_flutterwave_headers(), timeout=30)
    data = response.json() if response.content else {}
    if response.status_code >= 300:
        raise ValueError(str(data.get("message") or "Failed to create payment link"))
    link = str((data.get("data") or {}).get("link") or "")
    return link, redact_payment_payload(data)


def _create_stripe_checkout_session(intent: DirectPaymentIntent) -> tuple[str, dict]:
    from apps.billing.stripe_payments import create_checkout_session

    metadata = {
        "intent_id": str(intent.id),
        "tx_ref": intent.tx_ref,
        "target_type": intent.target_type,
        "target_id": str(intent.target_id),
        "user_id": str(intent.user_id),
    }
    product_name = str((intent.metadata or {}).get("description") or intent.target_type).replace("_", " ").title()
    success_url = (
        getattr(settings, "STRIPE_CHECKOUT_SUCCESS_URL", "")
        or getattr(settings, "FLW_REDIRECT_URL", "")
        or "https://kis.app/payments/complete"
    )
    cancel_url = (
        getattr(settings, "STRIPE_CHECKOUT_CANCEL_URL", "")
        or success_url
    )

    # Stripe Connect Destination Charge split — mirrors Flutterwave's
    # native subaccount split (_split_subaccounts_for_intent) using the
    # same commission-rate lookup, applied in Stripe's own shape
    # (absolute application_fee_amount cents rather than a percentage
    # fraction). Only attached when the seller's Stripe account is
    # actually ready to receive charges; otherwise this Checkout Session
    # settles 100% to the platform's own account exactly like today.
    destination_account_id = ""
    application_fee_amount = 0
    payout_entity = resolve_payout_entity(intent.target_type, intent.target_id)
    if payout_entity is not None and getattr(payout_entity, "stripe_charges_enabled", False):
        destination_account_id = payout_entity.stripe_account_id
        application_fee_amount = _commission_cents_for_entity(intent, payout_entity)

    data = create_checkout_session(
        amount_cents=int(intent.amount_cents or 0),
        currency=(intent.currency or "USD").lower(),
        product_name=product_name,
        success_url=success_url,
        cancel_url=cancel_url,
        target_type=intent.target_type,
        target_id=str(intent.target_id),
        user_id=str(intent.user_id),
        metadata=metadata,
        destination_account_id=destination_account_id,
        application_fee_amount=application_fee_amount,
    )
    link = str(data.get("checkout_url") or "")
    return link, redact_payment_payload(data)


def create_flutterwave_payment_link(
    *,
    amount: float,
    currency: str = "USD",
    email: str,
    name: str,
    title: str,
    meta: dict | None = None,
) -> str | None:
    """
    Create a Flutterwave payment link without requiring a DirectPaymentIntent model.
    Returns the payment URL string, or None on failure.
    """
    flw_key = getattr(settings, "FLW_SECRET_KEY", "") or ""
    redirect_url = getattr(settings, "FLW_REDIRECT_URL", "") or ""
    if not flw_key:
        logger.warning("create_flutterwave_payment_link: FLW_SECRET_KEY not configured.")
        return None
    tx_ref = f"kis_tip_{uuid.uuid4().hex}"
    payload = {
        "tx_ref": tx_ref,
        "amount": str(round(float(amount), 2)),
        "currency": currency.upper(),
        "redirect_url": redirect_url,
        "customer": {"email": email or f"anon_{tx_ref}@kis.local", "name": name or "KIS User"},
        "customizations": {"title": title[:100]},
        "meta": meta or {},
    }
    try:
        response = requests.post(
            f"{FLW_BASE_URL}/payments",
            json=payload,
            headers={"Authorization": f"Bearer {flw_key}", "Content-Type": "application/json"},
            timeout=30,
        )
        data = response.json() if response.content else {}
        if response.status_code >= 300:
            logger.warning("create_flutterwave_payment_link failed: %s", data.get("message"))
            return None
        return str((data.get("data") or {}).get("link") or "") or None
    except Exception as exc:
        logger.warning("create_flutterwave_payment_link exception: %s", exc)
        return None


def _ensure_provider_payment_link(intent: DirectPaymentIntent, *, actor=None) -> DirectPaymentIntent:
    if intent.payment_url or not _provider_links_enabled(intent.provider):
        return intent
    try:
        if intent.provider == "stripe":
            payment_url, provider_payload = _create_stripe_checkout_session(intent)
        else:
            payment_url, provider_payload = _create_flutterwave_payment_link(intent)
    except Exception as exc:  # pragma: no cover - live provider path is disabled in local validation.
        write_direct_payment_audit(
            event="intent.provider_link_failed",
            intent=intent,
            actor=actor,
            metadata={"error": str(exc)},
        )
        return intent
    intent.payment_url = payment_url
    intent.provider_payload = provider_payload
    intent.save(update_fields=["payment_url", "provider_payload", "updated_at"])
    write_direct_payment_audit(event="intent.provider_link_created", intent=intent, actor=actor)
    return intent


def _target_owner_and_amount(target_type: str, target_id: uuid.UUID | str) -> tuple[Any, int, str, Any]:
    target_uuid = uuid.UUID(str(target_id))
    if target_type == DirectPaymentIntent.TARGET_MARKETPLACE_ORDER:
        from apps.commerce.models import MarketplaceOrder

        order = MarketplaceOrder.objects.select_related("buyer").get(id=target_uuid)
        amount = int((Decimal(str(order.total_amount or 0)) * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return order.buyer, amount, "Marketplace order payment", order
    if target_type == DirectPaymentIntent.TARGET_SERVICE_BOOKING_PAYMENT:
        from apps.commerce.models import ServiceBookingPayment

        payment = ServiceBookingPayment.objects.select_related("booking__user").get(id=target_uuid)
        return payment.booking.user, int(payment.amount_cents or 0), "Service booking payment", payment
    if target_type == DirectPaymentIntent.TARGET_EDUCATION_BOOKING:
        from apps.broadcasts.models import EducationInstitutionBooking

        booking = EducationInstitutionBooking.objects.select_related("user").get(id=target_uuid)
        return booking.user, int(booking.amount_cents or 0), "Education booking payment", booking
    if target_type == DirectPaymentIntent.TARGET_HEALTH_BILLING_SESSION:
        from apps.health_ops.models import PaymentBillingSession

        session = PaymentBillingSession.objects.select_related("user").get(id=target_uuid)
        amount = int((Decimal(int(session.payable_amount_micro or 0)) / Decimal(KISC_MICRO_PER_USD_CENT)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return session.user, amount, "Health billing payment", session
    if target_type == DirectPaymentIntent.TARGET_CHANNEL_MEMBERSHIP:
        from apps.broadcasts.models import ChannelMembership

        membership = ChannelMembership.objects.select_related("user", "tier").get(id=target_uuid)
        return membership.user, int(membership.tier.price_cents or 0), "Channel membership payment", membership
    if target_type == DirectPaymentIntent.TARGET_CHANNEL_TIP:
        tip = _tip_for_target(target_uuid)
        if tip is None:
            raise DirectPaymentIntent.DoesNotExist("Tip not found")
        return tip.user, int(tip.amount_cents or 0), "Channel tip payment", tip
    raise ValueError("Unsupported payment target type")


def create_direct_payment_intent(
    *,
    user,
    target_type: str,
    target_id: uuid.UUID | str,
    provider: str = "flutterwave",
    idempotency_key: str = "",
    metadata: dict | None = None,
) -> DirectPaymentIntent:
    owner, amount_cents, description, target = _target_owner_and_amount(target_type, target_id)
    if owner.id != user.id:
        raise PermissionError("Payment target does not belong to this user.")
    if amount_cents <= 0:
        raise ValueError("Payment amount must be greater than zero.")

    # Universal payment-readiness backstop — every domain (Market,
    # Education, Health, Broadcast) already calls this one function to
    # create a payment, so gating it here covers all of them with a single
    # check rather than each domain re-implementing its own. Entities that
    # resolve_payout_entity can't resolve to anything (e.g. an
    # institution-owned broadcast channel, which settles via its
    # institution instead) have nothing to gate here.
    payout_entity = resolve_payout_entity(target_type, target_id)
    if payout_entity is not None:
        from .eligibility import PaymentSetupRequiredError, can_receive_payments

        eligibility = can_receive_payments(payout_entity)
        if not eligibility.eligible:
            raise PaymentSetupRequiredError(eligibility)
    provider = (provider or "flutterwave").strip().lower()
    target_uuid = uuid.UUID(str(target_id))
    existing = DirectPaymentIntent.objects.filter(
        user=user,
        target_type=target_type,
        target_id=target_uuid,
        status=DirectPaymentIntent.STATUS_PENDING,
    ).order_by("-created_at").first()
    if existing:
        changed_fields: list[str] = []
        if int(existing.amount_cents or 0) != int(amount_cents or 0):
            existing.amount_cents = amount_cents
            changed_fields.append("amount_cents")
        if provider and existing.provider != provider:
            existing.provider = provider
            changed_fields.append("provider")
        if idempotency_key and existing.idempotency_key != idempotency_key:
            existing.idempotency_key = idempotency_key
            changed_fields.append("idempotency_key")
        next_metadata = {**(existing.metadata or {}), **(metadata or {}), "description": description}
        if existing.metadata != next_metadata:
            existing.metadata = next_metadata
            changed_fields.append("metadata")
        if changed_fields:
            existing.save(update_fields=[*changed_fields, "updated_at"])
            write_direct_payment_audit(event="intent.updated", intent=existing, actor=user)
        existing = _ensure_provider_payment_link(existing, actor=user)
        _attach_intent_to_target(existing, target)
        return existing

    tx_ref = f"kis_direct_{target_type}_{uuid.uuid4().hex}"
    intent = DirectPaymentIntent.objects.create(
        user=user,
        provider=provider,
        target_type=target_type,
        target_id=target_uuid,
        amount_cents=amount_cents,
        currency="USD",
        status=DirectPaymentIntent.STATUS_PENDING,
        tx_ref=tx_ref,
        idempotency_key=idempotency_key or "",
        metadata={**(metadata or {}), "description": description},
    )
    write_direct_payment_audit(event="intent.created", intent=intent, actor=user)
    _attach_intent_to_target(intent, target)

    intent = _ensure_provider_payment_link(intent, actor=user)
    if intent.payment_url:
        _attach_intent_to_target(intent, target)
    return intent


def _attach_intent_to_target(intent: DirectPaymentIntent, target: Any | None = None) -> None:
    if target is None:
        _owner, _amount, _description, target = _target_owner_and_amount(intent.target_type, intent.target_id)
    metadata = getattr(target, "metadata", None)
    if not isinstance(metadata, dict):
        metadata = {}
    metadata.update(
        {
            "payment_status": "pending",
            "payment_provider": intent.provider,
            "payment_required": True,
            "payment_reference": intent.tx_ref,
            "payment_url": intent.payment_url or "",
            "direct_payment_intent_id": str(intent.id),
        }
    )
    if intent.target_type == DirectPaymentIntent.TARGET_MARKETPLACE_ORDER:
        target.metadata = metadata
        target.currency = "USD"
        target.save(update_fields=["metadata", "currency", "updated_at"])
    elif intent.target_type == DirectPaymentIntent.TARGET_SERVICE_BOOKING_PAYMENT:
        target.payment_method = intent.provider
        target.currency = "USD"
        target.transaction_reference = intent.tx_ref
        target.payment_status = target.STATUS_PENDING
        target.notes = "USD provider checkout pending."
        target.save(update_fields=["payment_method", "currency", "transaction_reference", "payment_status", "notes", "updated_at"])
        booking = target.booking
        booking.payment_tx_ref = intent.tx_ref
        booking.metadata = {**(booking.metadata or {}), **metadata}
        booking.save(update_fields=["payment_tx_ref", "metadata", "updated_at"])
    elif intent.target_type == DirectPaymentIntent.TARGET_EDUCATION_BOOKING:
        from apps.broadcasts.models import EducationBookingStatus

        target.payment_method = intent.provider
        target.currency = "USD"
        target.status = EducationBookingStatus.PAYMENT_PENDING
        target.metadata = metadata
        target.save(update_fields=["payment_method", "currency", "status", "metadata", "updated_at"])
    elif intent.target_type == DirectPaymentIntent.TARGET_HEALTH_BILLING_SESSION:
        from apps.health_ops.models import PaymentBillingStatus

        target.payment_provider = intent.provider
        target.payment_reference = intent.tx_ref
        target.status = PaymentBillingStatus.PAYMENT_PENDING
        target.metadata = {**(target.metadata or {}), **metadata, "currency": "USD"}
        target.save(update_fields=["payment_provider", "payment_reference", "status", "metadata", "updated_at"])
    elif intent.target_type == DirectPaymentIntent.TARGET_CHANNEL_MEMBERSHIP:
        # ChannelMembership has no updated_at field, unlike the other
        # targets above — only update the fields it actually has.
        target.payment_reference = intent.tx_ref
        target.status = "pending_payment"
        target.metadata = {**(target.metadata or {}), **metadata}
        target.save(update_fields=["payment_reference", "status", "metadata"])


def reconcile_direct_payment_callback(*, payload: dict, signature: str = "") -> tuple[bool, str, DirectPaymentIntent | None]:
    secret = getattr(settings, "FLW_WEBHOOK_SECRET", "")
    if not secret or signature != secret:
        write_direct_payment_audit(event="callback.signature_invalid", metadata={"provider": "flutterwave"})
        return False, "invalid_signature", None
    body = payload if isinstance(payload, dict) else {}
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    tx_ref = str(data.get("tx_ref") or "").strip()
    if not tx_ref:
        write_direct_payment_audit(event="callback.missing_tx_ref", metadata=redact_payment_payload(body))
        return False, "missing_tx_ref", None
    intent = DirectPaymentIntent.objects.filter(tx_ref=tx_ref).first()
    if not intent:
        write_direct_payment_audit(event="callback.unmatched", tx_ref=tx_ref, metadata=redact_payment_payload(body))
        return False, "unmatched", None

    status_flag = str(data.get("status") or "").strip().lower()
    with transaction.atomic():
        intent = DirectPaymentIntent.objects.select_for_update().get(id=intent.id)
        intent.raw_callback = redact_payment_payload(body)
        intent.provider_ref = str(data.get("id") or data.get("transaction_id") or intent.provider_ref or "")
        if status_flag in {"successful", "success", "succeeded", "paid"}:
            if intent.status != DirectPaymentIntent.STATUS_PAID:
                intent.status = DirectPaymentIntent.STATUS_PAID
                intent.processed_at = timezone.now()
                intent.save(update_fields=["status", "provider_ref", "raw_callback", "processed_at", "updated_at"])
                _mark_target_paid(intent, data)
                write_direct_payment_audit(event="callback.paid", intent=intent, metadata=redact_payment_payload(body))
            else:
                intent.save(update_fields=["raw_callback", "updated_at"])
                write_direct_payment_audit(event="callback.duplicate_paid", intent=intent, metadata=redact_payment_payload(body))
            return True, "paid", intent
        if status_flag in {"failed", "cancelled", "canceled"}:
            next_status = DirectPaymentIntent.STATUS_CANCELLED if status_flag in {"cancelled", "canceled"} else DirectPaymentIntent.STATUS_FAILED
            if intent.status == DirectPaymentIntent.STATUS_PENDING:
                intent.status = next_status
                intent.processed_at = timezone.now()
                intent.save(update_fields=["status", "provider_ref", "raw_callback", "processed_at", "updated_at"])
                _mark_target_failed(intent, next_status)
                write_direct_payment_audit(event=f"callback.{next_status}", intent=intent, metadata=redact_payment_payload(body))
            return True, next_status, intent
        intent.save(update_fields=["provider_ref", "raw_callback", "updated_at"])
        write_direct_payment_audit(event="callback.ignored_status", intent=intent, metadata={"status": status_flag})
        return True, "ignored", intent


def _stripe_event_target(event: dict) -> tuple[str, dict, DirectPaymentIntent | None]:
    body = event if isinstance(event, dict) else {}
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    obj = data.get("object") if isinstance(data.get("object"), dict) else {}
    metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
    intent = None
    intent_id = str(metadata.get("intent_id") or "").strip()
    tx_ref = str(metadata.get("tx_ref") or "").strip()
    if intent_id:
        try:
            intent = DirectPaymentIntent.objects.filter(id=uuid.UUID(intent_id)).first()
        except (TypeError, ValueError):
            intent = None
    if intent is None and tx_ref:
        intent = DirectPaymentIntent.objects.filter(tx_ref=tx_ref).first()
    return str(body.get("type") or "").strip(), obj, intent


def reconcile_stripe_direct_payment_event(event: dict) -> tuple[bool, str, DirectPaymentIntent | None]:
    event_type, obj, intent = _stripe_event_target(event)
    if event_type not in {"checkout.session.completed", "payment_intent.succeeded"}:
        return True, "ignored", None
    if not intent:
        write_direct_payment_audit(
            event="stripe.callback.unmatched",
            provider="stripe",
            metadata=redact_payment_payload(event if isinstance(event, dict) else {}),
        )
        return False, "unmatched", None
    if intent.provider != "stripe":
        write_direct_payment_audit(
            event="stripe.callback.provider_mismatch",
            intent=intent,
            provider="stripe",
            metadata={"intent_provider": intent.provider, "stripe_event_type": event_type},
        )
        return False, "provider_mismatch", intent

    with transaction.atomic():
        intent = DirectPaymentIntent.objects.select_for_update().get(id=intent.id)
        provider_ref = str(obj.get("payment_intent") or obj.get("id") or intent.provider_ref or "")
        intent.raw_callback = redact_payment_payload(event if isinstance(event, dict) else {})
        intent.provider_ref = provider_ref
        if intent.status != DirectPaymentIntent.STATUS_PAID:
            intent.status = DirectPaymentIntent.STATUS_PAID
            intent.processed_at = timezone.now()
            intent.save(update_fields=["status", "provider_ref", "raw_callback", "processed_at", "updated_at"])
            _mark_target_paid(intent, {"id": provider_ref})
            write_direct_payment_audit(
                event="stripe.callback.paid",
                intent=intent,
                provider="stripe",
                metadata=redact_payment_payload(event if isinstance(event, dict) else {}),
            )
            return True, "paid", intent
        intent.save(update_fields=["provider_ref", "raw_callback", "updated_at"])
        write_direct_payment_audit(
            event="stripe.callback.duplicate_paid",
            intent=intent,
            provider="stripe",
            metadata=redact_payment_payload(event if isinstance(event, dict) else {}),
        )
        return True, "paid", intent


def _mark_target_paid(intent: DirectPaymentIntent, data: dict) -> None:
    _owner, _amount, _description, target = _target_owner_and_amount(intent.target_type, intent.target_id)
    paid_metadata = {
        "payment_status": "paid",
        "payment_provider": intent.provider,
        "payment_required": False,
        "payment_reference": intent.tx_ref,
        "provider_transaction_id": str(data.get("id") or data.get("transaction_id") or ""),
        "paid_at": timezone.now().isoformat(),
        "payment_url": intent.payment_url or "",
        "direct_payment_intent_id": str(intent.id),
    }
    if intent.target_type == DirectPaymentIntent.TARGET_MARKETPLACE_ORDER:
        from apps.commerce.models import MarketplaceOrderStatus
        from apps.commerce.services import _schedule_marketplace_order_auto_satisfaction

        target.metadata = {**(target.metadata or {}), **paid_metadata}
        target.currency = "USD"
        update_fields = ["metadata", "currency", "updated_at"]
        pending_payment_statuses = {
            MarketplaceOrderStatus.TEMPORAL,
            "payment_pending",
            "pending",
            "provider_payment_pending",
        }
        if target.status in pending_payment_statuses:
            target.status = MarketplaceOrderStatus.AWAITING_SATISFACTION
            target.metadata = {
                **(target.metadata or {}),
                "awaiting_satisfaction_until": (
                    timezone.now() + timezone.timedelta(days=3)
                ).isoformat(),
            }
            update_fields.append("status")
        target.save(update_fields=update_fields)
        if "status" in update_fields:
            _schedule_marketplace_order_auto_satisfaction(target.id)
    elif intent.target_type == DirectPaymentIntent.TARGET_SERVICE_BOOKING_PAYMENT:
        from apps.commerce.models import ServiceBooking, ServiceBookingReceipt

        target.payment_status = target.STATUS_PAID
        target.payment_method = intent.provider
        target.currency = "USD"
        target.transaction_reference = intent.tx_ref
        target.paid_at = target.paid_at or timezone.now()
        target.notes = "USD provider payment confirmed."
        target.save(update_fields=["payment_status", "payment_method", "currency", "transaction_reference", "paid_at", "notes", "updated_at"])
        booking = target.booking
        booking.metadata = {**(booking.metadata or {}), **paid_metadata}
        booking.payment_tx_ref = intent.tx_ref
        booking_update_fields = ["metadata", "payment_tx_ref", "updated_at"]
        is_remaining_payment = str((intent.metadata or {}).get("source") or "").strip().lower() == "service_booking_remaining"
        if booking.status in {ServiceBooking.STATUS_PENDING, "payment_pending", "provider_payment_pending"}:
            booking.status = ServiceBooking.STATUS_CONFIRMED
            booking_update_fields.append("status")
        if is_remaining_payment:
            booking.deposit_cents = int(booking.price_cents or 0)
            booking.balance_cents = 0
            booking.metadata = {
                **(booking.metadata or {}),
                "remaining_payment_required": False,
                "remaining_payment_status": "paid",
            }
            booking_update_fields.extend(["deposit_cents", "balance_cents"])
        booking.save(update_fields=list(dict.fromkeys(booking_update_fields)))
        # The legacy wallet-lock path already records a ServiceBookingReceipt
        # at booking-creation time (apps.commerce.views._record_booking_receipt);
        # the default USD/direct-payment path never did, so paid bookings on
        # that path had no receipt at all. Record one here, at the moment
        # payment is actually confirmed.
        if target.amount_cents and not ServiceBookingReceipt.objects.filter(
            booking=booking, transaction_reference=intent.tx_ref
        ).exists():
            ServiceBookingReceipt.objects.create(
                booking=booking,
                amount_cents=int(target.amount_cents or 0),
                currency="USD",
                transaction_reference=intent.tx_ref,
                phase=ServiceBookingReceipt.PHASE_REMAINING if is_remaining_payment else ServiceBookingReceipt.PHASE_DEPOSIT,
            )
    elif intent.target_type == DirectPaymentIntent.TARGET_EDUCATION_BOOKING:
        from apps.broadcasts.models import EducationBookingStatus
        from apps.broadcasts.views import _grant_education_enrollment_for_confirmed_booking
        from .documents import ensure_direct_payment_intent_receipt_documents

        target.status = EducationBookingStatus.CONFIRMED
        target.payment_method = intent.provider
        target.currency = "USD"
        target.confirmed_at = target.confirmed_at or timezone.now()
        try:
            receipt_html_rel, receipt_pdf_rel = ensure_direct_payment_intent_receipt_documents(
                intent, kind_label="Course enrollment"
            )
        except Exception:
            # A receipt-storage failure must never block the actual
            # payment confirmation/enrollment grant below — log it and
            # leave the receipt paths unset; a future read can retry.
            logger.exception("Receipt generation failed for education booking intent_id=%s", intent.id)
            receipt_html_rel, receipt_pdf_rel = None, None
        target.metadata = {
            **(target.metadata or {}),
            **paid_metadata,
            "receipt_html_path": receipt_html_rel,
            "receipt_pdf_path": receipt_pdf_rel,
        }
        target.save(update_fields=["status", "payment_method", "currency", "confirmed_at", "metadata", "updated_at"])
        # Course content (has_learning_access) only unlocks once the
        # enrollment record itself is ENROLLED, not merely because the
        # booking is CONFIRMED - see EducationContentEnrollmentView.post.
        _grant_education_enrollment_for_confirmed_booking(target)
    elif intent.target_type == DirectPaymentIntent.TARGET_HEALTH_BILLING_SESSION:
        from apps.health_ops.models import PaymentBillingStatus
        from .documents import ensure_direct_payment_intent_receipt_documents

        try:
            receipt_html_rel, receipt_pdf_rel = ensure_direct_payment_intent_receipt_documents(
                intent, kind_label="Health billing session"
            )
        except Exception:
            logger.exception("Receipt generation failed for health billing session intent_id=%s", intent.id)
            receipt_html_rel, receipt_pdf_rel = None, None
        paid_metadata = {
            **paid_metadata,
            "receipt_html_path": receipt_html_rel,
            "receipt_pdf_path": receipt_pdf_rel,
        }
        target.status = PaymentBillingStatus.PAID
        target.payment_provider = intent.provider
        target.payment_reference = intent.tx_ref
        target.amount_paid_micro = int((Decimal(intent.amount_cents) * Decimal(KISC_MICRO_PER_USD_CENT)).quantize(Decimal("1")))
        target.paid_at = target.paid_at or timezone.now()
        target.payload = {**(target.payload or {}), **paid_metadata}
        target.metadata = {**(target.metadata or {}), **paid_metadata, "currency": "USD"}
        target.save(update_fields=["status", "payment_provider", "payment_reference", "amount_paid_micro", "paid_at", "payload", "metadata", "updated_at"])
    elif intent.target_type == DirectPaymentIntent.TARGET_CHANNEL_TIP:
        from apps.broadcasts.models import ChannelLiveStreamTip

        # target is either a ChannelContentTip or ChannelLiveStreamTip
        # (see _tip_for_target) — both share the same Status.COMPLETED /
        # payment_reference shape.
        target.status = target.Status.COMPLETED
        target.payment_reference = intent.tx_ref
        update_fields = ["status", "payment_reference", "updated_at"]
        # Super Chat pin ($10+ tips get a highlighted chat message) only
        # takes effect once payment is actually confirmed, not at the
        # moment the tip was merely requested (see ChannelLiveStreamTipView.post).
        if isinstance(target, ChannelLiveStreamTip) and int(target.amount_cents or 0) >= 1000:
            target.is_pinned = True
            target.pinned_until = timezone.now() + timezone.timedelta(minutes=2)
            update_fields.extend(["is_pinned", "pinned_until"])
        target.save(update_fields=update_fields)


def _mark_target_failed(intent: DirectPaymentIntent, status_value: str) -> None:
    _owner, _amount, _description, target = _target_owner_and_amount(intent.target_type, intent.target_id)
    metadata = {
        "payment_status": status_value,
        "payment_provider": intent.provider,
        "payment_required": True,
        "payment_reference": intent.tx_ref,
        "payment_url": intent.payment_url or "",
        "direct_payment_intent_id": str(intent.id),
    }
    if intent.target_type == DirectPaymentIntent.TARGET_MARKETPLACE_ORDER:
        target.metadata = {**(target.metadata or {}), **metadata}
        target.save(update_fields=["metadata", "updated_at"])
    elif intent.target_type == DirectPaymentIntent.TARGET_SERVICE_BOOKING_PAYMENT:
        target.payment_status = target.STATUS_FAILED if status_value == DirectPaymentIntent.STATUS_FAILED else target.STATUS_PENDING
        target.notes = f"USD provider payment {status_value}."
        target.save(update_fields=["payment_status", "notes", "updated_at"])
    elif intent.target_type == DirectPaymentIntent.TARGET_EDUCATION_BOOKING:
        target.metadata = {**(target.metadata or {}), **metadata}
        target.save(update_fields=["metadata", "updated_at"])
    elif intent.target_type == DirectPaymentIntent.TARGET_HEALTH_BILLING_SESSION:
        target.metadata = {**(target.metadata or {}), **metadata}
        target.payload = {**(target.payload or {}), **metadata}
        target.save(update_fields=["metadata", "payload", "updated_at"])
