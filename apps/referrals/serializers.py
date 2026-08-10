from __future__ import annotations

from rest_framework import serializers

from .models import Referral, ReferralCode


class ReferralHistoryEntrySerializer(serializers.Serializer):
    referred_display_name = serializers.SerializerMethodField()
    status = serializers.CharField()
    points_awarded = serializers.IntegerField(source="reward_points_awarded")
    created_at = serializers.DateTimeField()
    rewarded_at = serializers.DateTimeField(allow_null=True)

    def get_referred_display_name(self, obj: Referral) -> str:
        user = obj.referred_user
        return getattr(user, "display_name", None) or getattr(user, "phone", None) or "KIS member"


class ReferralSummarySerializer(serializers.Serializer):
    code = serializers.CharField()
    reward_points_per_referral = serializers.IntegerField()
    total_referred = serializers.IntegerField()
    total_rewarded = serializers.IntegerField()
    total_points_earned = serializers.IntegerField()
    history = ReferralHistoryEntrySerializer(many=True)
