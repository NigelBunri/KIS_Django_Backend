"""
Phase 6: confirms a failed payment-receipt email from the Flutterwave
webhook handler is now logged + audited instead of vanishing via a bare
`except: pass` (apps/billing/views.py FlutterwaveWebhookView).

Run:
  python3 manage.py test apps.billing.test_payment_email_hardening --keepdb -v 2
"""
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import AuditLog, AccountTier, User
from apps.billing.models import WalletTransaction


def _make_user(phone: str) -> User:
    user = User.objects.create_user(phone=phone, password="TestPass12!", country="CM")
    user.email = f"{phone.lstrip('+')}@example.com"
    user.save(update_fields=["email"])
    return user


@override_settings(SECURE_SSL_REDIRECT=False, FLW_WEBHOOK_SECRET="test-webhook-secret")
class PaymentReceiptEmailFailureVisibilityTests(TestCase):
    def setUp(self):
        self.user = _make_user("+237699300001")
        self.tx = WalletTransaction.objects.create(
            user=self.user, provider="flutterwave", method="card",
            amount_cents=1500, currency="USD", status="pending",
            tx_ref="kis_receipt_email_ref",
            meta={"intent": "deposit"},
        )
        self.client = APIClient()

    def _post_webhook(self):
        return self.client.post(
            "/api/v1/wallet/webhook/flutterwave/",
            {"data": {"tx_ref": self.tx.tx_ref, "status": "successful", "id": "flw-evt-receipt-1", "currency": "USD"}},
            format="json",
            HTTP_VERIF_HASH="test-webhook-secret",
            secure=True,
        )

    @patch("apps.notifications.email_service.send_payment_receipt_email", return_value=False)
    def test_failed_receipt_email_is_logged_and_audited_without_failing_the_webhook(self, _mock_send):
        res = self._post_webhook()

        self.assertEqual(res.status_code, 200)
        self.assertTrue(
            AuditLog.objects.filter(actor_id=self.user.id, action="email.payment_receipt.failed").exists()
        )
        self.tx.refresh_from_db()
        self.assertEqual(self.tx.status, "success")

    @patch("apps.notifications.email_service.send_payment_receipt_email", return_value=True)
    def test_successful_receipt_email_does_not_create_a_failure_audit_entry(self, _mock_send):
        res = self._post_webhook()

        self.assertEqual(res.status_code, 200)
        self.assertFalse(
            AuditLog.objects.filter(actor_id=self.user.id, action="email.payment_receipt.failed").exists()
        )
