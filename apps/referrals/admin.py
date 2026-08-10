from django.contrib import admin

from .models import Referral, ReferralCode


@admin.register(ReferralCode)
class ReferralCodeAdmin(admin.ModelAdmin):
    list_display = ("code", "user", "created_at")
    search_fields = ("code", "user__phone", "user__display_name")


@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    list_display = ("referrer", "referred_user", "status", "reward_points_awarded", "created_at", "rewarded_at")
    list_filter = ("status",)
    search_fields = ("referrer__phone", "referred_user__phone", "referral_code_used")
