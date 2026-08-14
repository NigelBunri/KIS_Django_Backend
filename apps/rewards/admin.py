from django import forms
from django.contrib import admin

from .models import (
    AchievementDefinition,
    RedemptionPolicy,
    RepeatableRewardRule,
    RewardLedgerEntry,
)


@admin.register(RewardLedgerEntry)
class RewardLedgerEntryAdmin(admin.ModelAdmin):
    # Read-only: the ledger must never be edited by hand. Legitimate manual
    # corrections go through an admin_adjustment-type entry created via
    # services.py (Phase 12), preserving the audit trail, not a raw edit here.
    list_display = ("user", "type", "source", "amount", "status", "effective_at", "created_at")
    list_filter = ("type", "status")
    search_fields = ("user__phone", "user__email", "source", "idempotency_key", "reference_id")
    readonly_fields = [f.name for f in RewardLedgerEntry._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AchievementDefinition)
class AchievementDefinitionAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "coin_amount", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code", "title")


@admin.register(RepeatableRewardRule)
class RepeatableRewardRuleAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "coin_amount", "frequency", "max_per_period", "is_active")
    list_filter = ("frequency", "is_active")
    search_fields = ("code", "title")


class RedemptionPolicyForm(forms.ModelForm):
    """
    Sanity-checks the economic-safety ceilings on save (Phase 12).
    apps.rewards.services.calculate_redemption already fails loudly
    (RedemptionPolicyViolation) if a misconfigured policy would compute a
    payable amount below the minimum cash contribution — this form catches
    the same class of misconfiguration earlier, at save time in the admin,
    instead of at the next live checkout attempt.
    """

    class Meta:
        model = RedemptionPolicy
        fields = [
            "context", "normal_max_discount_percent", "absolute_max_discount_percent",
            "min_cash_contribution_percent", "coin_value_cents", "is_active",
        ]

    def clean(self):
        cleaned = super().clean()
        normal_max = cleaned.get("normal_max_discount_percent")
        absolute_max = cleaned.get("absolute_max_discount_percent")
        min_cash = cleaned.get("min_cash_contribution_percent")
        coin_value = cleaned.get("coin_value_cents")

        for field_name, value in (
            ("normal_max_discount_percent", normal_max),
            ("absolute_max_discount_percent", absolute_max),
            ("min_cash_contribution_percent", min_cash),
        ):
            if value is not None and not (0 <= value <= 100):
                self.add_error(field_name, "Must be between 0 and 100.")

        if coin_value is not None and coin_value < 0:
            self.add_error("coin_value_cents", "Cannot be negative.")

        if normal_max is not None and absolute_max is not None and normal_max > absolute_max:
            self.add_error(
                "normal_max_discount_percent",
                "Cannot exceed absolute_max_discount_percent — the normal ceiling is meant to "
                "be the everyday cap, with the absolute ceiling as the higher exceptional cap.",
            )

        if absolute_max is not None and min_cash is not None and absolute_max + min_cash > 100:
            self.add_error(
                "absolute_max_discount_percent",
                f"absolute_max_discount_percent ({absolute_max}%) + min_cash_contribution_percent "
                f"({min_cash}%) exceeds 100% — this combination makes every redemption at the "
                f"absolute ceiling impossible to satisfy (calculate_redemption would raise "
                f"RedemptionPolicyViolation at checkout instead of computing a valid discount).",
            )

        return cleaned


@admin.register(RedemptionPolicy)
class RedemptionPolicyAdmin(admin.ModelAdmin):
    form = RedemptionPolicyForm
    list_display = (
        "context", "normal_max_discount_percent", "absolute_max_discount_percent",
        "min_cash_contribution_percent", "coin_value_cents", "is_active",
    )
