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


def _provider_links_enabled(provider: str) -> bool:
    return (
        provider == "flutterwave"
        and getattr(settings, "KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED", False)
        and bool(getattr(settings, "FLW_SECRET_KEY", ""))
    )


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
        target.metadata = {**(target.metadata or {}), **paid_metadata}
        target.currency = "USD"
        target.save(update_fields=["metadata", "currency", "updated_at"])
    elif intent.target_type == DirectPaymentIntent.TARGET_SERVICE_BOOKING_PAYMENT:
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
        booking.save(update_fields=["metadata", "payment_tx_ref", "updated_at"])
    elif intent.target_type == DirectPaymentIntent.TARGET_EDUCATION_BOOKING:
        from apps.broadcasts.models import EducationBookingStatus

        target.status = EducationBookingStatus.CONFIRMED
        target.payment_method = intent.provider
        target.currency = "USD"
        target.confirmed_at = target.confirmed_at or timezone.now()
        target.metadata = {**(target.metadata or {}), **paid_metadata}
        target.save(update_fields=["status", "payment_method", "currency", "confirmed_at", "metadata", "updated_at"])
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
