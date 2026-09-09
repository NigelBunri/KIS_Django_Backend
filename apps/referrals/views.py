from __future__ import annotations

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Referral, ReferralCode
from .serializers import ReferralSummarySerializer
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
