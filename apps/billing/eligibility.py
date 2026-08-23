# apps/billing/eligibility.py
"""Centralized "can this seller receive payments" check, shared by every
domain (Market, Education, Health, Broadcast) rather than each app
re-implementing its own version. Sits on top of the existing per-target
payout-entity resolution in apps.billing.direct_payments.resolve_payout_entity
— it doesn't introduce a new cross-app seller model, it just asks the
existing Shop/EducationInstitution/HealthInstitution/BroadcastChannel
payout fields (flutterwave_subaccount_id/payout_account_status,
stripe_account_id/stripe_charges_enabled) whether either connected
provider is actually ready to receive money.

Enforcement itself lives in two places:
  - apps.billing.direct_payments.create_direct_payment_intent — the
    universal backstop every domain already funnels through, so gating it
    there covers all four domains with one check.
  - Individual domain serializers/views (Product/ShopService,
    EducationInstitutionCourse) call can_receive_payments directly at
    listing/publish time, so a seller finds out before a customer ever
    reaches checkout, not just at the point of payment.
"""
from __future__ import annotations

from dataclasses import dataclass

from rest_framework import status
from rest_framework.exceptions import APIException

_ACTIVE_PAYOUT_STATUS = "active"


@dataclass(frozen=True)
class EligibilityResult:
    eligible: bool
    provider: str | None = None
    reason: str | None = None
    action: str | None = None


def _flutterwave_ready(entity) -> bool:
    return (
        getattr(entity, "payout_account_status", None) == _ACTIVE_PAYOUT_STATUS
        and bool(getattr(entity, "flutterwave_subaccount_id", ""))
    )


def _stripe_ready(entity) -> bool:
    return bool(getattr(entity, "stripe_charges_enabled", False))


def can_receive_payments(entity) -> EligibilityResult:
    """entity is one of Shop/EducationInstitution/HealthInstitution/
    BroadcastChannel (anything resolve_payout_entity can return) — never
    None; callers that get None back from resolve_payout_entity have
    nothing to check (that target type has no payout-holding entity at
    all, e.g. an institution-owned broadcast channel settling via its
    institution instead) and should skip calling this entirely."""
    if _flutterwave_ready(entity):
        return EligibilityResult(eligible=True, provider="flutterwave")
    if _stripe_ready(entity):
        return EligibilityResult(eligible=True, provider="stripe")

    has_flutterwave_attempt = bool(getattr(entity, "flutterwave_subaccount_id", "")) or getattr(
        entity, "payout_account_status", _ACTIVE_PAYOUT_STATUS
    ) not in (None, "", "not_connected")
    has_stripe_attempt = bool(getattr(entity, "stripe_account_id", ""))

    if not has_flutterwave_attempt and not has_stripe_attempt:
        return EligibilityResult(eligible=False, reason="NOT_CONNECTED", action="COMPLETE_PAYMENT_SETUP")
    return EligibilityResult(eligible=False, reason="ONBOARDING_INCOMPLETE", action="COMPLETE_PAYMENT_SETUP")


class PaymentSetupRequiredError(APIException):
    status_code = status.HTTP_402_PAYMENT_REQUIRED
    default_code = "PAYMENT_SETUP_REQUIRED"

    def __init__(self, result: EligibilityResult | None = None, *, buyer_facing: bool = False):
        result = result or EligibilityResult(eligible=False, reason="NOT_CONNECTED", action="COMPLETE_PAYMENT_SETUP")
        if buyer_facing:
            # create_direct_payment_intent's raise is always hit mid-checkout
            # by the *buyer* — the seller-facing "Complete your payment
            # account setup" wording (correct at publish/listing time, the
            # other two call sites of this exception) told a buyer to fix a
            # setting they have no access to, on a purchase they couldn't
            # actually complete either way.
            message = "This seller hasn't finished setting up how they get paid yet, so this can't be purchased right now. Please check back later."
        else:
            message = (
                "Complete your payment account setup before you can charge customers for this."
                if result.reason == "NOT_CONNECTED"
                else "Finish your payment account setup — a step is still incomplete before you can charge customers."
            )
        super().__init__(
            detail={
                "code": "PAYMENT_SETUP_REQUIRED",
                "message": message,
                "eligible": result.eligible,
                "provider": result.provider,
                "reason": result.reason,
                "action": result.action,
            }
        )
