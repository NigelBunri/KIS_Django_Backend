from django.contrib import admin

from .models import Referral, ReferralCode, ReferralCommission, ReferralPayoutAccount, ReferralRateConfig


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


@admin.register(ReferralPayoutAccount)
class ReferralPayoutAccountAdmin(admin.ModelAdmin):
    """Editable (unlike ReferralCommission below) — a payout DESTINATION,
    not a financial record of a transaction that already happened. Staff
    can see provider/status for support/fraud investigation; the
    sensitive connect/disconnect flow itself still only ever runs through
    apps.referrals.views (the real Flutterwave beneficiary / Stripe
    Connect calls), never a raw admin field edit."""
    list_display = ("user", "provider", "status", "payout_account_name", "payout_bank_last4", "updated_at")
    list_filter = ("provider", "status")
    search_fields = ("user__phone", "user__display_name", "payout_account_name")
    readonly_fields = [
        "flutterwave_beneficiary_id", "stripe_account_id",
        "stripe_charges_enabled", "stripe_payouts_enabled", "stripe_details_submitted",
    ]


@admin.register(ReferralCommission)
class ReferralCommissionAdmin(admin.ModelAdmin):
    """Read-only (mirrors ReferralAdmin above exactly) — a
    ReferralCommission is a financial/audit record; every status
    transition must go through apps.referrals.commission_services so the
    audit trail (AuditLog) and any linked payout stay correct. The two
    allowed admin-initiated actions (`reverse_selected`,
    `reconcile_payout_selected`) still only ever call the real service
    functions (logging who/why) rather than letting staff flip the
    `status` field directly — satisfies "do not allow administrators to
    silently modify historical commission records" while still giving
    staff a way to act on confirmed fraud, a provider-reported dispute,
    or an unresolved/FAILED_PAYOUT commission."""
    list_display = (
        "referrer", "referred_user", "status", "commission_amount_cents", "currency",
        "source_provider", "payout_provider", "created_at", "paid_at",
    )
    list_filter = ("status", "source_provider", "payout_provider")
    search_fields = ("referrer__phone", "referred_user__phone", "source_payment_reference", "payout_reference")
    readonly_fields = [f.name for f in ReferralCommission._meta.fields]
    actions = ["reverse_selected", "reconcile_payout_selected"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Reverse selected commissions (fraud/dispute)")
    def reverse_selected(self, request, queryset):
        from .commission_services import reverse_referral_commission

        reversed_count = 0
        for commission in queryset:
            result = reverse_referral_commission(
                referred_user=commission.referred_user,
                source_payment_reference=commission.source_payment_reference,
                reason=f"admin:{request.user.id}",
            )
            if result and result.status == ReferralCommission.STATUS_REVERSED:
                reversed_count += 1
        self.message_user(request, f"Reversed {reversed_count} of {queryset.count()} selected commission(s).")

    @admin.action(description="Reconcile payout with provider (recover stuck/failed)")
    def reconcile_payout_selected(self, request, queryset):
        """Queries the actual payout provider (Flutterwave/Stripe) for
        each selected commission's payout_attempt_reference and applies
        whatever the provider says really happened — the auditable
        recovery path for a commission support learns is stuck
        FAILED_PAYOUT despite the referrer swearing they got paid, or
        vice versa. Never trusts or applies any status directly; every
        correction still runs through commission_services and is logged
        to AuditLog with the provider's own response."""
        from .commission_services import reconcile_commission_payout

        checked = corrected = 0
        for commission in queryset:
            before = commission.status
            result = reconcile_commission_payout(commission)
            checked += 1
            if result.status != before:
                corrected += 1
        self.message_user(
            request,
            f"Checked {checked} commission(s) with the provider; {corrected} status correction(s) applied.",
        )
