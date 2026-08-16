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
        from apps.commerce.models import ServiceBooking

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
        if booking.status in {ServiceBooking.STATUS_PENDING, "payment_pending", "provider_payment_pending"}:
            booking.status = ServiceBooking.STATUS_CONFIRMED
            booking_update_fields.append("status")
        if str((intent.metadata or {}).get("source") or "").strip().lower() == "service_booking_remaining":
            booking.deposit_cents = int(booking.price_cents or 0)
            booking.balance_cents = 0
            booking.metadata = {
                **(booking.metadata or {}),
                "remaining_payment_required": False,
                "remaining_payment_status": "paid",
            }
            booking_update_fields.extend(["deposit_cents", "balance_cents"])
        booking.save(update_fields=list(dict.fromkeys(booking_update_fields)))
    elif intent.target_type == DirectPaymentIntent.TARGET_EDUCATION_BOOKING:
        from apps.broadcasts.models import EducationBookingStatus
        from apps.broadcasts.views import _grant_education_enrollment_for_confirmed_booking

        target.status = EducationBookingStatus.CONFIRMED
        target.payment_method = intent.provider
        target.currency = "USD"
        target.confirmed_at = target.confirmed_at or timezone.now()
        target.metadata = {**(target.metadata or {}), **paid_metadata}
        target.save(update_fields=["status", "payment_method", "currency", "confirmed_at", "metadata", "updated_at"])
        # Course content (has_learning_access) only unlocks once the
        # enrollment record itself is ENROLLED, not merely because the
        # booking is CONFIRMED - see EducationContentEnrollmentView.post.
        _grant_education_enrollment_for_confirmed_booking(target)
    elif intent.target_type == DirectPaymentIntent.TARGET_HEALTH_BILLING_SESSION:
        from apps.health_ops.models import PaymentBillingStatus

        target.status = PaymentBillingStatus.PAID
        target.payment_provider = intent.provider
        target.payment_reference = intent.tx_ref
        target.amount_paid_micro = int((Decimal(intent.amount_cents) * Decimal(KISC_MICRO_PER_USD_CENT)).quantize(Decimal("1")))
        target.paid_at = target.paid_at or timezone.now()
        target.payload = {**(target.payload or {}), **paid_metadata}
        target.metadata = {**(target.metadata or {}), **paid_metadata, "currency": "USD"}
        target.save(update_fields=["status", "payment_provider", "payment_reference", "amount_paid_micro", "paid_at", "payload", "metadata", "updated_at"])


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
