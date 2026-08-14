"""
Regression tests for a real production incident: two genuinely successful
Flutterwave sandbox payments for a tier upgrade (Flutterwave transaction
ids 10430039 and 10431224, both "Approved. Successful") never upgraded the
paying user's account -- WalletTransaction stayed "pending" indefinitely.

Flutterwave only supports one configured webhook URL per merchant account,
but this codebase exposes two separate webhook endpoints:
  - /api/v1/wallet/webhook/flutterwave/          (FlutterwaveWebhookView)
  - /api/v1/direct-payments/webhook/flutterwave/ (DirectPaymentFlutterwaveWebhookView)
FlutterwaveWebhookView already unifies both transaction kinds (it checks
DirectPaymentIntent first, falls back to WalletTransaction). But
DirectPaymentFlutterwaveWebhookView previously only understood
DirectPaymentIntent-based flows (commerce orders, bookings, health
billing) -- a WalletTransaction-based event (tier upgrade, tip, deposit)
landing on that URL was met with a 404 and silently dropped, leaving a
real successful payment permanently unreconciled and the user stuck on
the free tier. It now falls back to the exact same WalletTransaction
reconciliation FlutterwaveWebhookView uses.

Run:
  python3 manage.py test apps.billing.test_flutterwave_webhook_cross_reconciliation --keepdb -v 2
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import AccountTier, Subscription
from apps.billing.models import WalletTransaction

User = get_user_model()
WEBHOOK_SECRET = "flw-webhook-cross-reconcile-test-secret"


def _api_url(route_name: str) -> str:
    url = reverse(route_name)
    return url if url.endswith("/") else f"{url}/"


class DirectPaymentWebhookFallsBackToWalletReconciliationTests(TestCase):
    """Reproduces the incident: a WalletTransaction-only tx_ref delivered
    to the direct-payments webhook URL must still be reconciled, not
    dropped as 'unknown transaction'."""

    def setUp(self):
        self.user = User.objects.create_user(phone="+237699700001", country="CM", password="pass1234")
        self.tier = AccountTier.objects.create(
            name="Cross-Reconcile Pro", price_cents=3500, rank=2, billing_period_days=30,
        )
        self.tx = WalletTransaction.objects.create(
            user=self.user, provider="flutterwave", method="card", amount_cents=3500,
            currency="USD", status="pending", tx_ref="kis_upgrade_crossreconcile",
            meta={"intent": "tier_upgrade", "tier_id": str(self.tier.id), "tier_name": self.tier.name},
        )
        self.client = APIClient()

    def _flw_payload(self, status_value: str = "successful"):
        return {
            "data": {
                "id": 99887766,
                "tx_ref": self.tx.tx_ref,
                "status": status_value,
                "amount": 35,
                "currency": "USD",
            }
        }

    def test_wallet_tier_upgrade_delivered_to_direct_payments_url_is_reconciled(self):
        with override_settings(FLW_WEBHOOK_SECRET=WEBHOOK_SECRET):
            response = self.client.post(
                _api_url("direct-payment-flw-webhook"),
                self._flw_payload(),
                format="json",
                secure=True,
                HTTP_VERIF_HASH=WEBHOOK_SECRET,
            )

        self.assertEqual(response.status_code, 200, response.data)

        self.tx.refresh_from_db()
        self.assertEqual(self.tx.status, "success")
        self.assertEqual(self.tx.provider_ref, "99887766")

        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, self.tier.name)
        self.assertTrue(
            Subscription.objects.filter(user=self.user, status=Subscription.STATUS_ACTIVE, tier=self.tier).exists()
        )

    def test_still_rejects_an_invalid_signature_on_the_fallback_path(self):
        with override_settings(FLW_WEBHOOK_SECRET=WEBHOOK_SECRET):
            response = self.client.post(
                _api_url("direct-payment-flw-webhook"),
                self._flw_payload(),
                format="json",
                secure=True,
                HTTP_VERIF_HASH="not-the-real-secret",
            )

        self.assertEqual(response.status_code, 403)
        self.tx.refresh_from_db()
        self.assertEqual(self.tx.status, "pending")
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.tier, self.tier.name)

    def test_a_tx_ref_matching_neither_system_still_returns_not_found(self):
        with override_settings(FLW_WEBHOOK_SECRET=WEBHOOK_SECRET):
            response = self.client.post(
                _api_url("direct-payment-flw-webhook"),
                {"data": {"id": 1, "tx_ref": "kis_upgrade_totally_unknown_ref", "status": "successful"}},
                format="json",
                secure=True,
                HTTP_VERIF_HASH=WEBHOOK_SECRET,
            )

        self.assertEqual(response.status_code, 404)
