from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    BillingReconciliationViewSet,
    InsuranceClaimViewSet,
    PaymentDisputeViewSet,
    PricingInsightsView,
    StripeWebhookView,
    WalletViewSet,
    WalletAdminViewSet,
    PromoCodeViewSet,
    RevenueLaunchEvidenceRecordViewSet,
    DirectPaymentAuditEventViewSet,
    DirectPaymentFlutterwaveWebhookView,
    DirectPaymentIntentView,
    FlutterwaveWebhookView,
    ProfitabilityBetaLaunchPlanView,
    ProfitabilityBetaOperationsView,
    ProfitabilityCommandCenterView,
    ProfitabilityEvidenceWorkflowPlanView,
    ProfitabilityEntitlementCatalogView,
    ProfitabilityLaunchGateView,
    ProfitabilityProductionGoNoGoView,
    ProfitabilityRevenueReadinessView,
    ProfitabilityRevenueOpsEvidenceView,
    ProfitabilitySubscriptionLifecycleView,
    ProfitabilityStagingProofWorkflowView,
)

router = DefaultRouter()
router.register(r"wallet", WalletViewSet, basename="wallet")
router.register(r"wallet-admin", WalletAdminViewSet, basename="wallet-admin")
router.register(r"promo-codes", PromoCodeViewSet, basename="promo-codes")
router.register(r"billing/revenue-launch-evidence", RevenueLaunchEvidenceRecordViewSet, basename="revenue-launch-evidence")
router.register(r"direct-payment-audit", DirectPaymentAuditEventViewSet, basename="direct-payment-audit")
router.register(r"billing/reconciliations", BillingReconciliationViewSet, basename="billing-reconciliations")
router.register(r"billing/claims", InsuranceClaimViewSet, basename="insurance-claims")
router.register(r"billing/disputes", PaymentDisputeViewSet, basename="payment-disputes")

urlpatterns = [
    path("", include(router.urls)),
    path("direct-payments/intents/", DirectPaymentIntentView.as_view(), name="direct-payment-intents"),
    path("direct-payments/webhook/flutterwave/", DirectPaymentFlutterwaveWebhookView.as_view(), name="direct-payment-flw-webhook"),
    path("wallet/webhook/flutterwave/", FlutterwaveWebhookView.as_view(), name="wallet-flw-webhook"),
    path("billing/stripe/webhook/", StripeWebhookView.as_view(), name="stripe-webhook"),
]

urlpatterns += [
    path("billing/pricing-insights/", PricingInsightsView.as_view(), name="billing-pricing-insights"),
    path("billing/profitability-entitlements/", ProfitabilityEntitlementCatalogView.as_view(), name="billing-profitability-entitlements"),
    path("billing/profitability-command-center/", ProfitabilityCommandCenterView.as_view(), name="billing-profitability-command-center"),
    path("billing/profitability-launch-gate/", ProfitabilityLaunchGateView.as_view(), name="billing-profitability-launch-gate"),
    path("billing/profitability-subscription-lifecycle/", ProfitabilitySubscriptionLifecycleView.as_view(), name="billing-profitability-subscription-lifecycle"),
    path("billing/profitability-revenue-ops-evidence/", ProfitabilityRevenueOpsEvidenceView.as_view(), name="billing-profitability-revenue-ops-evidence"),
    path("billing/profitability-evidence-workflow-plan/", ProfitabilityEvidenceWorkflowPlanView.as_view(), name="billing-profitability-evidence-workflow-plan"),
    path("billing/profitability-revenue-readiness/", ProfitabilityRevenueReadinessView.as_view(), name="billing-profitability-revenue-readiness"),
    path("billing/profitability-staging-proof-workflows/", ProfitabilityStagingProofWorkflowView.as_view(), name="billing-profitability-staging-proof-workflows"),
    path("billing/profitability-production-go-no-go/", ProfitabilityProductionGoNoGoView.as_view(), name="billing-profitability-production-go-no-go"),
    path("billing/profitability-beta-launch-plan/", ProfitabilityBetaLaunchPlanView.as_view(), name="billing-profitability-beta-launch-plan"),
    path("billing/profitability-beta-operations/", ProfitabilityBetaOperationsView.as_view(), name="billing-profitability-beta-operations"),
]
