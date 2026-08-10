from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Referral, ReferralCode
from .serializers import ReferralSummarySerializer
from .services import REFERRAL_REWARD_POINTS


class MyReferralsView(APIView):
    """
    GET /api/v1/referrals/me/
    Own referral code plus a summary of everyone referred with it. Only
    display_name/status/points/timestamps are exposed for each referred
    user — no phone/contact/private profile data.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        code_record = ReferralCode.get_or_create_for_user(request.user)
        referrals = (
            Referral.objects.filter(referrer=request.user)
            .select_related("referred_user")
            .order_by("-created_at")
        )
        total_rewarded = sum(1 for r in referrals if r.status == Referral.STATUS_REWARDED)
        total_points_earned = sum(r.reward_points_awarded for r in referrals)

        payload = {
            "code": code_record.code,
            "reward_points_per_referral": REFERRAL_REWARD_POINTS,
            "total_referred": referrals.count(),
            "total_rewarded": total_rewarded,
            "total_points_earned": total_points_earned,
            "history": list(referrals[:50]),
        }
        serializer = ReferralSummarySerializer(payload)
        return Response(serializer.data, status=status.HTTP_200_OK)
