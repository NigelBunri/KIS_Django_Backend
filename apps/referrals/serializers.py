from __future__ import annotations

from rest_framework import serializers

from .models import Referral, ReferralCode


class ReferralHistoryEntrySerializer(serializers.Serializer):
    referred_display_name = serializers.SerializerMethodField()
    status = serializers.CharField()
    # For a QUALIFIED referral, reward_points_awarded is still 0 (only set
    # once confirm_referral_reward settles it) — pending_points exposes the
    # snapshotted ledger-entry amount even before settlement, so the UI can
    # show "550 pending" rather than nothing.
    points_awarded = serializers.IntegerField(source="reward_points_awarded")
    pending_points = serializers.SerializerMethodField()
    reward_rate_percent = serializers.DecimalField(
        source="reward_rate_percent_snapshot", max_digits=5, decimal_places=2, allow_null=True,
    )
    qualifying_net_amount_cents = serializers.IntegerField(allow_null=True)
    created_at = serializers.DateTimeField()
    rewarded_at = serializers.DateTimeField(allow_null=True)

    def get_referred_display_name(self, obj: Referral) -> str:
        user = obj.referred_user
        return getattr(user, "display_name", None) or getattr(user, "phone", None) or "KIS member"

    def get_pending_points(self, obj: Referral) -> int:
        if obj.status == Referral.STATUS_QUALIFIED and obj.reward_ledger_entry_id:
            return obj.reward_ledger_entry.amount
        return 0


class ReferralSummarySerializer(serializers.Serializer):
    code = serializers.CharField()
    current_referral_rate_percent = serializers.DecimalField(max_digits=5, decimal_places=2, allow_null=True)
    current_referral_rate_tier = serializers.CharField(allow_null=True)
    total_referred = serializers.IntegerField()
    total_qualified = serializers.IntegerField()
    total_rewarded = serializers.IntegerField()
    total_reversed = serializers.IntegerField()
    total_points_earned = serializers.IntegerField()
    total_points_pending = serializers.IntegerField()
    history = ReferralHistoryEntrySerializer(many=True)
