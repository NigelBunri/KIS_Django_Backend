"""
Comms migration (Sep 2026): the Flutterwave webhook's payment-receipt leg
no longer emails a receipt automatically — the permanent WalletTransaction
row (queryable via GET wallet/transactions/, with an on-demand POST
wallet/transactions/{id}/email-receipt/ action) is the primary receipt,
backed by an immediate in-app/push PAYMENT_SUCCESS notification. This
file's PaymentReceiptEmailFailureVisibilityTests class covers that
notification (including the dollars-not-raw-cents amount formatting bug
guard it inherited from the email version it replaced).

FlutterwaveMembershipConfirmationEmailTests below is untouched by that
migration - it covers a different email (channel_membership tier
activation, send_membership_email), not the generic payment receipt, and
stays in scope as a legitimate email use case.

Run:
  python3 manage.py test apps.billing.test_payment_email_hardening --keepdb -v 2
"""
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import AuditLog, AccountTier, User
from apps.billing.models import WalletTransaction
from apps.broadcasts.models import BroadcastChannel, ChannelMembership, ChannelMembershipTier
from apps.notifications.models import Notification


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

    def test_receipt_email_is_no_longer_sent_automatically(self):
        with patch("apps.notifications.email_service.send_payment_receipt_email") as mock_send:
            res = self._post_webhook()

        self.assertEqual(res.status_code, 200)
        mock_send.assert_not_called()
        self.assertFalse(
            AuditLog.objects.filter(actor_id=self.user.id, action="email.payment_receipt.failed").exists()
        )
        self.tx.refresh_from_db()
        self.assertEqual(self.tx.status, "success")

    def test_payment_success_notification_amount_is_dollars_not_raw_cents(self):
        # self.tx.amount_cents == 1500 (set in setUp) — a $15.00 charge.
        # The email version this replaced used to send the literal string
        # "1500"; the in-app notification inherits the same guard.
        res = self._post_webhook()

        self.assertEqual(res.status_code, 200)
        notif = Notification.objects.filter(
            user_id=self.user.id, type="PAYMENT_SUCCESS", dedup_key=f"payment_success:{self.tx.tx_ref}",
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn("15.00", notif.body)

    def test_payment_success_notification_amount_handles_a_large_charge_correctly(self):
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
        notif = Notification.objects.filter(
            user_id=self.user.id, type="PAYMENT_SUCCESS", dedup_key=f"payment_success:{big_tx.tx_ref}",
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn("5000.00", notif.body)


@override_settings(SECURE_SSL_REDIRECT=False, FLW_WEBHOOK_SECRET="test-webhook-secret")
class FlutterwaveMembershipConfirmationEmailTests(TestCase):
    """Originally covered row 09 of the audit ("Inconsistent"): the
    channel_membership activation branch of the Flutterwave webhook never
    sent a confirmation email at all, unlike the Stripe and free-tier join
    paths (fixed alongside the channel.name -> channel.display_name bug
    that was silently breaking those other two paths' emails too).

    That confirmation has since moved to an in-app/push MEMBERSHIP_CONFIRMED
    notification (comms architecture migration, Sep 2026) - the payment
    itself already gets its own receipt notification elsewhere in this
    handler, so a second email for the same transaction was redundant.
    These tests were updated to match; class name kept for history."""

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

    def test_membership_confirmation_notification_is_sent_on_flutterwave_activation(self):
        res = self._post_webhook()

        self.assertEqual(res.status_code, 200)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, ChannelMembership.Status.ACTIVE)
        notif = Notification.objects.filter(
            user_id=self.user.id, type="MEMBERSHIP_CONFIRMED",
            context_data__membership_id=str(self.membership.id),
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn("Supporter", notif.title)
        self.assertIn("FLW Membership Email Test Channel", notif.body)

    def test_notification_failure_never_blocks_activation(self):
        with patch(
            "apps.notifications.services.create_notification",
            side_effect=RuntimeError("boom"),
        ):
            res = self._post_webhook()

        self.assertEqual(res.status_code, 200)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, ChannelMembership.Status.ACTIVE)
