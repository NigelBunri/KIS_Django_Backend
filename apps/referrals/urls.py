from django.urls import path

from .views import (
    MyReferralCommissionsView,
    MyReferralsView,
    ReferralPayoutAccountFlutterwaveConnectView,
    ReferralPayoutAccountStripeConnectView,
)

urlpatterns = [
    path("referrals/me/", MyReferralsView.as_view()),
    path("referrals/commissions/", MyReferralCommissionsView.as_view()),
    path("referrals/payout-account/flutterwave/", ReferralPayoutAccountFlutterwaveConnectView.as_view()),
    path("referrals/payout-account/stripe/", ReferralPayoutAccountStripeConnectView.as_view()),
]
