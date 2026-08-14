"""
PaymentStatusView (apps/billing/views.py) — the public, unauthenticated
endpoint backing the payments/complete redirect landing page on the
marketing website (kingdomimpactventures.org). Added to close a real
production bug: a Flutterwave sandbox payment succeeded, redirected the
payer to a page that didn't exist, and the account was never upgraded
because the webhook never reached Django. This endpoint self-heals that
by verifying directly with Flutterwave's own API when a payment is still
"pending" locally.

Run:
  python3 manage.py test apps.billing.test_payment_status_view --keepdb -v 2
"""
from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import AccountTier, Subscription
from apps.billing.models import WalletTransaction

User = get_user_model()


def _api_url(route_name: str) -> str:
    url = reverse(route_name)
    return url if url.endswith("/") else f"{url}/"


def _make_user(phone: str) -> User:
    return User.objects.create_user(phone=phone, country="CM", password="pass1234")


def _make_tier(name: str, price_cents: int, rank: int) -> AccountTier:
    return AccountTier.objects.create(name=name, price_cents=price_cents, rank=rank, billing_period_days=30)


class PaymentStatusViewTests(TestCase):
    def setUp(self):
        self.user = _make_user("+237699600010")
        self.tier = _make_tier("PSV Business", 10000, 2)
        self.client = APIClient()
        # Deliberately unauthenticated — this is the whole point of the
        # endpoint: a public redirect landing page has no session.

    def _status_url(self, tx_ref: str, transaction_id: str = "") -> str:
        url = _api_url("billing-payment-status")
        params = f"?tx_ref={tx_ref}"
        if transaction_id:
            params += f"&transaction_id={transaction_id}"
        return url + params

    def test_unknown_tx_ref_returns_404(self):
        response = self.client.get(self._status_url("kis_upgrade_doesnotexist"), secure=True)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.data["found"])

    def test_missing_tx_ref_returns_400(self):
        response = self.client.get(_api_url("billing-payment-status"), secure=True)
        self.assertEqual(response.status_code, 400)

    def test_returns_paid_status_when_already_reconciled(self):
        tx = WalletTransaction.objects.create(
            user=self.user, provider="flutterwave", method="card", amount_cents=10000,
            currency="USD", status="success", tx_ref="kis_upgrade_alreadypaid",
            meta={"intent": "tier_upgrade", "tier_id": str(self.tier.id), "tier_name": self.tier.name},
        )
        response = self.client.get(self._status_url(tx.tx_ref), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "paid")
        self.assertEqual(response.data["kind"], "tier_upgrade")
        self.assertEqual(response.data["tier_name"], self.tier.name)

    @patch("apps.billing.views.verify_flutterwave_transaction")
    def test_pending_without_transaction_id_never_calls_flutterwave(self, mock_verify):
        tx = WalletTransaction.objects.create(
            user=self.user, provider="flutterwave", method="card", amount_cents=10000,
            currency="USD", status="pending", tx_ref="kis_upgrade_pendingnoid",
            meta={"intent": "tier_upgrade", "tier_id": str(self.tier.id), "tier_name": self.tier.name},
        )
        response = self.client.get(self._status_url(tx.tx_ref), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "pending")
        mock_verify.assert_not_called()

    @override_settings(SECURE_SSL_REDIRECT=False)
    @patch("apps.billing.views.verify_flutterwave_transaction")
    def test_self_heals_a_missed_webhook_and_applies_the_tier_upgrade(self, mock_verify):
        # Reproduces the exact production bug: a real successful payment
        # whose webhook never arrived, leaving the WalletTransaction (and
        # the user's tier) stuck at "pending"/unupgraded.
        tx = WalletTransaction.objects.create(
            user=self.user, provider="flutterwave", method="card", amount_cents=10000,
            currency="USD", status="pending", tx_ref="kis_upgrade_missedwebhook",
            meta={"intent": "tier_upgrade", "tier_id": str(self.tier.id), "tier_name": self.tier.name},
        )
        mock_verify.return_value = {
            "id": "10431224", "tx_ref": tx.tx_ref, "status": "successful",
            "amount": 100.0, "currency": "USD",
        }

        response = self.client.get(self._status_url(tx.tx_ref, transaction_id="10431224"), secure=True)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "paid")
        mock_verify.assert_called_once_with("10431224")

        tx.refresh_from_db()
        self.assertEqual(tx.status, "success")
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, self.tier.name)
        subscription = Subscription.objects.get(user=self.user, status=Subscription.STATUS_ACTIVE)
        self.assertEqual(subscription.tier_id, self.tier.id)

    @patch("apps.billing.views.verify_flutterwave_transaction")
    def test_mismatched_tx_ref_from_verify_response_is_never_reconciled(self, mock_verify):
        # A transaction_id that verifies successfully but for a DIFFERENT
        # tx_ref than requested must never be allowed to reconcile this
        # record — otherwise a transaction_id for someone else's unrelated
        # payment could be used to fake-confirm an arbitrary tx_ref.
        tx = WalletTransaction.objects.create(
            user=self.user, provider="flutterwave", method="card", amount_cents=10000,
            currency="USD", status="pending", tx_ref="kis_upgrade_realref",
            meta={"intent": "tier_upgrade", "tier_id": str(self.tier.id), "tier_name": self.tier.name},
        )
        mock_verify.return_value = {
            "id": "99999999", "tx_ref": "kis_upgrade_someone_elses_payment", "status": "successful",
        }

        response = self.client.get(self._status_url(tx.tx_ref, transaction_id="99999999"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "pending")
        tx.refresh_from_db()
        self.assertEqual(tx.status, "pending")
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.tier, self.tier.name)

    @patch("apps.billing.views.verify_flutterwave_transaction")
    def test_verify_failure_leaves_status_pending_without_raising(self, mock_verify):
        tx = WalletTransaction.objects.create(
            user=self.user, provider="flutterwave", method="card", amount_cents=10000,
            currency="USD", status="pending", tx_ref="kis_upgrade_verifyerror",
            meta={"intent": "tier_upgrade", "tier_id": str(self.tier.id)},
        )
        mock_verify.side_effect = ValueError("Flutterwave is unreachable")

        response = self.client.get(self._status_url(tx.tx_ref, transaction_id="whatever"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "pending")
