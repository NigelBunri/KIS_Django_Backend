from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .commission_services import REFERRAL_COMMISSION_RATE_PERCENT
from .models import Referral, ReferralCode, ReferralCommission, ReferralPayoutAccount
from .serializers import (
    ReferralCommissionSummarySerializer,
    ReferralPayoutAccountSerializer,
    ReferralSummarySerializer,
)
from .services import get_current_tier, get_referral_rate_percent


class MyReferralsView(APIView):
    """
    GET /api/v1/referrals/me/
    Own referral code plus a summary of everyone referred with it. Only
    display_name/status/points/timestamps are exposed for each referred
    user — no phone/contact/private profile data.

    current_referral_rate_percent/current_referral_rate_tier reflect the
    tier-aware rate that would apply to a NEW qualification right now (per
    apps.referrals.services.ReferralRateConfig) — purely informational, the
    rate actually applied to any given referral is the one snapshotted onto
    it at qualification time (reward_rate_percent in the history entries)
    and never changes retroactively even if the current rate here does.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        code_record = ReferralCode.get_or_create_for_user(request.user)
        referrals = (
            Referral.objects.filter(referrer=request.user)
            .select_related("referred_user", "reward_ledger_entry")
            .order_by("-created_at")
        )
        total_qualified = sum(1 for r in referrals if r.status == Referral.STATUS_QUALIFIED)
        total_rewarded = sum(1 for r in referrals if r.status == Referral.STATUS_REWARDED)
        total_reversed = sum(1 for r in referrals if r.status == Referral.STATUS_REVERSED)
        total_points_earned = sum(r.reward_points_awarded for r in referrals if r.status == Referral.STATUS_REWARDED)
        total_points_pending = sum(
            r.reward_ledger_entry.amount
            for r in referrals
            if r.status == Referral.STATUS_QUALIFIED and r.reward_ledger_entry_id
        )

        current_tier = get_current_tier(request.user)
        current_rate = get_referral_rate_percent(current_tier) if current_tier else None

        # Same canonical-domain source as apps.groups/communities/partners'
        # own invite_link actions (KIS_WEBSITE_PUBLIC_BASE_URL, resolved by
        # apps.core.link_resolver.PublicLinkResolveView) - never the API
        # host, which has no matching web route.
        base = getattr(settings, "KIS_WEBSITE_PUBLIC_BASE_URL", "").rstrip("/")
        referral_link = f"{base}/join/referral/{code_record.code}"

        payload = {
            "code": code_record.code,
            "referral_link": referral_link,
            "current_referral_rate_percent": current_rate,
            "current_referral_rate_tier": current_tier.name if current_tier else None,
            "total_referred": referrals.count(),
            "total_qualified": total_qualified,
            "total_rewarded": total_rewarded,
            "total_reversed": total_reversed,
            "total_points_earned": total_points_earned,
            "total_points_pending": total_points_pending,
            "history": list(referrals[:50]),
        }
        serializer = ReferralSummarySerializer(payload)
        return Response(serializer.data, status=status.HTTP_200_OK)


class MyReferralCommissionsView(APIView):
    """
    GET /api/v1/referrals/commissions/
    Lifetime cash-commission dashboard — independent of, and additive to,
    MyReferralsView's one-time KIS-Coins summary above. Shares the same
    referral_code/referral_link (not repeated here; the mobile client
    already has it from MyReferralsView) but reports on
    ReferralCommission rows, not Referral/RewardLedgerEntry.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        commissions = (
            ReferralCommission.objects.filter(referrer=request.user)
            .select_related("referred_user")
            .order_by("-created_at")
        )
        totals = {"pending": 0, "approved": 0, "paid": 0}
        total_earned = 0
        for c in commissions:
            if c.status in totals:
                totals[c.status] += c.commission_amount_cents
            if c.status in (ReferralCommission.STATUS_APPROVED, ReferralCommission.STATUS_PAID):
                total_earned += c.commission_amount_cents

        payout_account = ReferralPayoutAccount.objects.filter(user=request.user).first()

        payload = {
            "commission_rate_percent": REFERRAL_COMMISSION_RATE_PERCENT,
            "payout_account": payout_account,
            "total_earned_cents": total_earned,
            "total_pending_cents": totals["pending"],
            "total_approved_cents": totals["approved"],
            "total_paid_cents": totals["paid"],
            "currency": "USD",
            "history": list(commissions[:50]),
        }
        serializer = ReferralCommissionSummarySerializer(payload)
        return Response(serializer.data, status=status.HTTP_200_OK)


class ReferralPayoutAccountFlutterwaveConnectView(APIView):
    """
    POST /api/v1/referrals/payout-account/flutterwave/
    DELETE /api/v1/referrals/payout-account/flutterwave/
    Connects (or disconnects) the authenticated user's own Flutterwave
    payout destination for referral commissions, using a Beneficiary
    (apps.billing.referral_payouts) — never a subaccount; see that
    module's docstring for why a referral payout needs a different
    Flutterwave capability than the Shop/Education/Health connect flows.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # Deliberately NOT wrapped in @transaction.atomic for the whole
        # method, unlike ShopPayoutAccountConnectView (apps.commerce.views)
        # that this otherwise mirrors: a Shop row always pre-exists before
        # that view runs, so a rollback on failure only reverts its status
        # field. Here, get_or_create may be creating the ONLY row for this
        # user inside this same request — wrapping the provider call (and
        # the except-and-re-raise below it) in one atomic block would roll
        # that creation back too the moment create_flutterwave_beneficiary
        # raises, silently discarding the "we tried, it failed" PENDING
        # state this code is trying to persist in the first place.
        from apps.billing.referral_payouts import create_flutterwave_beneficiary

        account_bank = str(request.data.get("account_bank") or "").strip()
        account_number = str(request.data.get("account_number") or "").strip()
        beneficiary_name = str(request.data.get("beneficiary_name") or request.user.display_name or "").strip()
        country = str(request.data.get("country") or "NG").strip().upper()
        if not account_bank or not account_number:
            raise ValidationError({"detail": "Bank and account number are required."})

        account, _ = ReferralPayoutAccount.objects.get_or_create(user=request.user)
        account.status = ReferralPayoutAccount.STATUS_PENDING
        account.save(update_fields=["status", "updated_at"])

        try:
            beneficiary_id = create_flutterwave_beneficiary(
                account_bank=account_bank,
                account_number=account_number,
                beneficiary_name=beneficiary_name or "KIS referrer",
            )
        except ValidationError:
            account.status = ReferralPayoutAccount.STATUS_NOT_CONNECTED
            account.save(update_fields=["status", "updated_at"])
            raise

        account.provider = ReferralPayoutAccount.PROVIDER_FLUTTERWAVE
        account.flutterwave_beneficiary_id = beneficiary_id
        account.status = ReferralPayoutAccount.STATUS_ACTIVE
        account.payout_account_name = beneficiary_name or "KIS referrer"
        account.payout_bank_last4 = account_number[-4:] if len(account_number) >= 4 else account_number
        account.payout_country = country
        # Switching provider away from Stripe — clear its fields so the
        # dashboard never shows two "connected" providers at once (see
        # ReferralPayoutAccount's docstring: exactly one active provider).
        account.stripe_account_id = ""
        account.stripe_charges_enabled = False
        account.stripe_payouts_enabled = False
        account.stripe_details_submitted = False
        account.save()

        return Response(ReferralPayoutAccountSerializer(account).data, status=status.HTTP_200_OK)

    def delete(self, request):
        account = ReferralPayoutAccount.objects.filter(user=request.user).first()
        if not account:
            return Response(ReferralPayoutAccountSerializer(ReferralPayoutAccount(user=request.user)).data)
        account.provider = ""
        account.status = ReferralPayoutAccount.STATUS_NOT_CONNECTED
        account.flutterwave_beneficiary_id = ""
        account.payout_account_name = ""
        account.payout_bank_last4 = ""
        account.stripe_account_id = ""
        account.stripe_charges_enabled = False
        account.stripe_payouts_enabled = False
        account.stripe_details_submitted = False
        account.save()
        return Response(ReferralPayoutAccountSerializer(account).data, status=status.HTTP_200_OK)


class ReferralPayoutAccountStripeConnectView(APIView):
    """
    GET /api/v1/referrals/payout-account/stripe/
    POST /api/v1/referrals/payout-account/stripe/
    Connects the authenticated user's own Stripe Express account for
    referral commissions, using apps.billing.stripe_connect's shared
    helpers — the identical pattern as
    apps.commerce.views.ShopStripeConnectAccountView, just keyed on the
    authenticated user directly rather than a shop/institution the user
    manages. GET re-queries Stripe directly for current status (never
    trusts stale local state); POST creates the account on first call
    (idempotent — reuses account.stripe_account_id on repeat calls) and
    returns a fresh onboarding link.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.billing.stripe_connect import refresh_account_status

        account = ReferralPayoutAccount.objects.filter(user=request.user).first()
        if account and account.stripe_account_id and account.provider == ReferralPayoutAccount.PROVIDER_STRIPE:
            fields = refresh_account_status(account.stripe_account_id)
            for field_name, value in fields.items():
                setattr(account, field_name, value)
            account.status = (
                ReferralPayoutAccount.STATUS_ACTIVE if fields.get("stripe_payouts_enabled")
                else ReferralPayoutAccount.STATUS_PENDING
            )
            account.save(update_fields=[*fields.keys(), "status", "updated_at"])

        return Response(ReferralPayoutAccountSerializer(
            account or ReferralPayoutAccount(user=request.user)
        ).data)

    @transaction.atomic
    def post(self, request):
        from apps.billing.stripe_connect import (
            create_account_onboarding_link,
            create_stripe_express_account,
            onboarding_redirect_urls,
        )

        account, _ = ReferralPayoutAccount.objects.get_or_create(user=request.user)

        if not account.stripe_account_id:
            country = str(request.data.get("country") or "US").strip().upper()
            account.stripe_account_id = create_stripe_express_account(
                email=request.user.email or "", country=country,
            )
            account.provider = ReferralPayoutAccount.PROVIDER_STRIPE
            account.status = ReferralPayoutAccount.STATUS_PENDING
            # Switching provider away from Flutterwave — same "exactly one
            # active provider" rule as the Flutterwave connect view above.
            account.flutterwave_beneficiary_id = ""
            account.payout_account_name = ""
            account.payout_bank_last4 = ""
            account.save()

        refresh_url, return_url = onboarding_redirect_urls()
        onboarding_url = create_account_onboarding_link(
            account_id=account.stripe_account_id, refresh_url=refresh_url, return_url=return_url,
        )
        return Response(
            {"onboarding_url": onboarding_url, "stripe_account_id": account.stripe_account_id},
            status=status.HTTP_200_OK,
        )
