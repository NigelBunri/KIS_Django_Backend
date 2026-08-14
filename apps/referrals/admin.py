from django.contrib import admin

from .models import Referral, ReferralCode, ReferralRateConfig


@admin.register(ReferralRateConfig)
class ReferralRateConfigAdmin(admin.ModelAdmin):
    list_display = ("tier", "rate_percent", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("tier__name",)


@admin.register(ReferralCode)
class ReferralCodeAdmin(admin.ModelAdmin):
    list_display = ("code", "user", "created_at")
    search_fields = ("code", "user__phone", "user__display_name")


@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    # Read-only (Phase 12): a Referral is a financial/audit record exactly
    # like RewardLedgerEntry (apps.rewards.admin.RewardLedgerEntryAdmin,
    # which this mirrors) — its status transitions are only ever valid via
    # apps.referrals.services (qualify_referral / confirm_referral_reward /
    # reverse_referral_reward), each of which keeps the linked
    # RewardLedgerEntry in sync. A raw admin edit here (e.g. flipping
    # status to REWARDED by hand) would desync it from its
    # reward_ledger_entry and would itself show up as an anomaly the next
    # time reconcile_rewards_and_referrals runs.
    list_display = ("referrer", "referred_user", "status", "reward_points_awarded", "qualified_at", "created_at", "rewarded_at")
    list_filter = ("status",)
    search_fields = ("referrer__phone", "referred_user__phone", "referral_code_used")
    readonly_fields = [f.name for f in Referral._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
