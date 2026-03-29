from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    BillingReconciliationViewSet,
    InsuranceClaimViewSet,
    PaymentDisputeViewSet,
    PricingInsightsView,
    WalletViewSet,
    WalletAdminViewSet,
    PromoCodeViewSet,
    FlutterwaveWebhookView,
)

router = DefaultRouter()
router.register(r"wallet", WalletViewSet, basename="wallet")
router.register(r"wallet-admin", WalletAdminViewSet, basename="wallet-admin")
router.register(r"promo-codes", PromoCodeViewSet, basename="promo-codes")
router.register(r"billing/reconciliations", BillingReconciliationViewSet, basename="billing-reconciliations")
router.register(r"billing/claims", InsuranceClaimViewSet, basename="insurance-claims")
router.register(r"billing/disputes", PaymentDisputeViewSet, basename="payment-disputes")

urlpatterns = [
    path("", include(router.urls)),
    path("wallet/webhook/flutterwave/", FlutterwaveWebhookView.as_view(), name="wallet-flw-webhook"),
]

urlpatterns += [
    path("billing/pricing-insights/", PricingInsightsView.as_view(), name="billing-pricing-insights"),
]
