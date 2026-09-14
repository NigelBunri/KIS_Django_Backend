"""
Phase 6: confirms a failed payment-receipt email from the Flutterwave
webhook handler is now logged + audited instead of vanishing via a bare
`except: pass` (apps/billing/views.py FlutterwaveWebhookView).

Email-system audit, Priority 2, adds coverage for two real bugs fixed in
the same handler:
- The receipt email sent transaction_obj.amount_cents raw as "amount" —
  a $5.00 (500 cents) charge read as "500" in the email, and larger
  amounts were off by two orders of magnitude, e.g. a $5,000 charge read
  as "500000". Now formatted as cents/100 with 2 decimals, matching the
  Stripe branch's (correct) equivalent.
- The channel_membership activation branch never sent a confirmation
  email at all (audit finding, row 09: "Inconsistent" vs. Stripe's and
  the free-tier join path's — which also turned out to be silently
  broken by a separate channel.name/display_name bug, fixed alongside
  this one; see test_stripe_email_hardening.py's docstring).

Run:
  python3 manage.py test apps.billing.test_payment_email_hardening --keepdb -v 2
"""
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import AuditLog, AccountTier, User
from apps.billing.models import WalletTransaction
from apps.broadcasts.models import BroadcastChannel, ChannelMembership, ChannelMembershipTier


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

    @patch("apps.notifications.email_service.send_payment_receipt_email", return_value=True)
    def test_receipt_amount_is_dollars_not_raw_cents(self, mock_send):
        # self.tx.amount_cents == 1500 (set in setUp) — a $15.00 charge.
        # Previously sent as the literal string "1500".
        res = self._post_webhook()

        self.assertEqual(res.status_code, 200)
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.kwargs.get("amount"), "15.00")

    @patch("apps.notifications.email_service.send_payment_receipt_email", return_value=True)
    def test_receipt_amount_handles_a_large_charge_correctly(self, mock_send):
        # The bug this specifically guards against: a $5,000 charge used to
        # read as "500000" (raw cents) rather than "5000.00".
        big_tx = WalletTransaction.objects.create(
            user=self.user, provider="flutterwave", method="card",
            amount_cents=500000, currency="USD", status="pending",
            tx_ref="kis_receipt_email_ref_large",
            meta={"intent": "deposit"},
        )
        res = self.client.post(
            "/api/v1/wallet/webhook/flutterwave/",
            {"data": {"tx_ref": big_tx.tx_ref, "status": "successful", "id": "flw-evt-receipt-2", "currency": "USD"}},
            format="json",
            HTTP_VERIF_HASH="test-webhook-secret",
            secure=True,
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(mock_send.call_args.kwargs.get("amount"), "5000.00")


@override_settings(SECURE_SSL_REDIRECT=False, FLW_WEBHOOK_SECRET="test-webhook-secret")
class FlutterwaveMembershipConfirmationEmailTests(TestCase):
    """Row 09 of the audit ("Inconsistent"): the channel_membership
    activation branch of the Flutterwave webhook never sent a confirmation
    email at all, unlike the Stripe and free-tier join paths. Fixed
    alongside the channel.name -> channel.display_name bug that was
    silently breaking those other two paths' emails too (see
    test_stripe_email_hardening.py)."""

    def setUp(self):
        self.user = _make_user("+237699300002")
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.user.id, owner_user=self.user,
            handle="flw-membership-email-test-channel", display_name="FLW Membership Email Test Channel",
        )
        self.tier = ChannelMembershipTier.objects.create(
            channel=self.channel, title="Supporter", price_cents=500, currency="USD",
        )
        self.membership = ChannelMembership.objects.create(
            user=self.user, tier=self.tier, status="pending_payment",
        )
        self.tx = WalletTransaction.objects.create(
            user=self.user, provider="flutterwave", method="card",
            amount_cents=500, currency="USD", status="pending",
            tx_ref="kis_membership_email_ref",
            meta={"intent": "deposit", "target_type": "channel_membership", "target_id": str(self.membership.id), "user_id": str(self.user.id)},
        )
        self.client = APIClient()

    def _post_webhook(self):
        return self.client.post(
            "/api/v1/wallet/webhook/flutterwave/",
            {"data": {"tx_ref": self.tx.tx_ref, "status": "successful", "id": "flw-evt-membership-1", "currency": "USD"}},
            format="json",
            HTTP_VERIF_HASH="test-webhook-secret",
            secure=True,
        )

    @patch("apps.notifications.email_service.send_membership_email", return_value=True)
    def test_membership_confirmation_email_is_now_sent_on_flutterwave_activation(self, mock_send):
        res = self._post_webhook()

        self.assertEqual(res.status_code, 200)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, ChannelMembership.Status.ACTIVE)
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.kwargs.get("to_email"), self.user.email)
        self.assertEqual(mock_send.call_args.kwargs.get("tier_title"), "Supporter")
        self.assertEqual(mock_send.call_args.kwargs.get("channel_name"), "FLW Membership Email Test Channel")

    @patch("apps.notifications.email_service.send_membership_email", return_value=False)
    def test_membership_email_failure_is_logged_and_audited_without_failing_activation(self, _mock_send):
        res = self._post_webhook()

        self.assertEqual(res.status_code, 200)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, ChannelMembership.Status.ACTIVE)
        entry = AuditLog.objects.filter(actor_id=self.user.id, action="email.membership.failed").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.meta.get("membership_id"), str(self.membership.id))
