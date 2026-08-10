from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import AuditLog, Device, User
from apps.billing.services import adjust_points

from .models import Referral, ReferralCode

# Matches the value already advertised to users in apps.commerce.views
# POINT_EARNING_RULES ("invite_friend") and the mobile wallet UI's own
# fallback copy — kept as a single source of truth here so a future change
# to the payout amount only needs to happen in one place; the advertised
# copy should be updated to read from this same value.
REFERRAL_REWARD_POINTS = 200


def _device_already_linked_to_referrer(referrer: User, device_id: str) -> bool:
    if not device_id:
        return False
    return Device.objects.filter(user=referrer, device_id=device_id).exists()


def register_referral(*, referred_user: User, referral_code: str, device_id: str = "") -> Referral | None:
    """
    Called once, at registration time, if the new user supplied a
    referral_code. Returns None (no-op) for a blank/unknown code — an
    invalid code must never block registration itself.

    Creates the Referral row in STATUS_PENDING, or straight to
    STATUS_BLOCKED if the new account's device_id is already linked to the
    referrer's own account (the most common referral-farming pattern: one
    person registering several throwaway accounts from the same device to
    repeatedly reward themselves).
    """
    code = (referral_code or "").strip().upper()
    if not code:
        return None

    code_record = ReferralCode.objects.select_related("user").filter(code=code).first()
    if not code_record:
        return None

    referrer = code_record.user
    if referrer.id == referred_user.id:
        return None

    status = Referral.STATUS_PENDING
    block_reason = ""
    if _device_already_linked_to_referrer(referrer, device_id):
        status = Referral.STATUS_BLOCKED
        block_reason = "referred_device_already_linked_to_referrer"

    referral = Referral.objects.create(
        referrer=referrer,
        referred_user=referred_user,
        referral_code_used=code,
        status=status,
        block_reason=block_reason,
    )
    AuditLog.log(
        referred_user,
        "referral.created",
        {
            "referral_id": str(referral.id),
            "referrer_id": str(referrer.id),
            "status": status,
        },
    )
    return referral


def apply_referral_reward_if_pending(referred_user: User) -> Referral | None:
    """
    The single authoritative reward-granting transition, called at whichever
    moment the referred user's account actually activates (immediately at
    registration when KIS_PHONE_VERIFICATION_ENABLED is off, or at OTP
    verification success when it's on). Idempotent: only acts on a Referral
    still in STATUS_PENDING for this user, under a row lock, so calling it
    more than once (or concurrently) is always safe.
    """
    with transaction.atomic():
        referral = (
            Referral.objects.select_for_update()
            .filter(referred_user=referred_user, status=Referral.STATUS_PENDING)
            .first()
        )
        if not referral:
            return None

        referral.status = Referral.STATUS_REWARDED
        referral.reward_points_awarded = REFERRAL_REWARD_POINTS
        referral.rewarded_at = timezone.now()
        referral.save(update_fields=["status", "reward_points_awarded", "rewarded_at", "updated_at"])

        adjust_points(
            referral.referrer,
            REFERRAL_REWARD_POINTS,
            reason=f"referral:{referred_user.id}",
        )
        AuditLog.log(
            referral.referrer,
            "referral.reward_granted",
            {
                "referral_id": str(referral.id),
                "referred_user_id": str(referred_user.id),
                "points": REFERRAL_REWARD_POINTS,
            },
        )

    return referral
