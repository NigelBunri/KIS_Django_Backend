from __future__ import annotations

from rest_framework import serializers

from .models import Referral, ReferralCode, ReferralCommission, ReferralPayoutAccount


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
        # Never falls back to the referred user's phone number — this is
        # rendered directly in the referrer's own dashboard, and a phone
        # number is not the referrer's to see just because they hold the
        # referral relationship.
        user = obj.referred_user
        return getattr(user, "display_name", None) or "KIS member"

    def get_pending_points(self, obj: Referral) -> int:
        if obj.status == Referral.STATUS_QUALIFIED and obj.reward_ledger_entry_id:
            return obj.reward_ledger_entry.amount
        return 0


class ReferralSummarySerializer(serializers.Serializer):
    code = serializers.CharField()
    referral_link = serializers.CharField()
    current_referral_rate_percent = serializers.DecimalField(max_digits=5, decimal_places=2, allow_null=True)
    current_referral_rate_tier = serializers.CharField(allow_null=True)
    total_referred = serializers.IntegerField()
    total_qualified = serializers.IntegerField()
    total_rewarded = serializers.IntegerField()
    total_reversed = serializers.IntegerField()
    total_points_earned = serializers.IntegerField()
    total_points_pending = serializers.IntegerField()
    history = ReferralHistoryEntrySerializer(many=True)


class ReferralPayoutAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReferralPayoutAccount
        fields = [
            "provider", "status", "payout_account_name", "payout_bank_last4", "payout_country",
            "stripe_account_id", "stripe_charges_enabled", "stripe_payouts_enabled", "stripe_details_submitted",
        ]


class ReferralCommissionEntrySerializer(serializers.Serializer):
    """One row in the cash-commission dashboard history. Mirrors
    ReferralHistoryEntrySerializer's privacy posture exactly — only the
    referred user's display name, never phone/contact/private profile
    data."""
    referred_display_name = serializers.SerializerMethodField()
    status = serializers.CharField()
    gross_amount_cents = serializers.IntegerField()
    commission_rate_percent = serializers.DecimalField(max_digits=5, decimal_places=2)
    commission_amount_cents = serializers.IntegerField()
    currency = serializers.CharField()
    created_at = serializers.DateTimeField()
    paid_at = serializers.DateTimeField(allow_null=True)

    def get_referred_display_name(self, obj: ReferralCommission) -> str:
        # See ReferralHistoryEntrySerializer.get_referred_display_name — no
        # phone-number fallback; a referrer never sees a referred user's
        # phone number through this dashboard.
        user = obj.referred_user
        return getattr(user, "display_name", None) or "KIS member"


class ReferralCommissionSummarySerializer(serializers.Serializer):
    """Payload for GET /api/v1/referrals/commissions/ — the lifetime
    cash-commission dashboard, independent of (and additive to)
    ReferralSummarySerializer's one-time KIS-Coins summary above."""
    commission_rate_percent = serializers.DecimalField(max_digits=5, decimal_places=2)
    payout_account = ReferralPayoutAccountSerializer(allow_null=True)
    total_earned_cents = serializers.IntegerField()
    total_pending_cents = serializers.IntegerField()
    total_approved_cents = serializers.IntegerField()
    total_paid_cents = serializers.IntegerField()
    currency = serializers.CharField()
    history = ReferralCommissionEntrySerializer(many=True)
