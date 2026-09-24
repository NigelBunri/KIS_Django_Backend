"""
Phase 6: confirms failed membership/receipt emails from the Stripe webhook
handler (payment_intent.succeeded, channel_membership target) are now
logged + audited instead of vanishing via bare `except: pass`
(apps/billing/views.py StripeWebhookView).

Stripe signature verification is mocked at its call site
(apps.billing.stripe_payments.verify_webhook) rather than exercised for
real — this test targets the email-failure-visibility fix, not Stripe's
signing scheme.

Email-system audit, Priority 2: this file used to document a pre-existing,
separate bug this hardening surfaced (previously invisible behind a bare
`except: pass`): the membership email branch built
`channel_name=membership.tier.channel.name`, but BroadcastChannel has no
`name` field/property — only `display_name` — so the membership-email
attempt in this webhook unconditionally raised AttributeError, regardless
of send_membership_email's own mocked return value. That's fixed now
(channel.display_name), here and in the Flutterwave webhook branch and the
free-tier join path with the identical copy-paste mistake — the test below
now covers the real success path instead of documenting the bug.

Comms migration (Sep 2026): the generic payment-receipt leg of this webhook
no longer emails a receipt automatically — replaced by an immediate
in-app/push PAYMENT_SUCCESS notification (see test_payment_email_hardening.py
for the Flutterwave equivalent). The membership-confirmation email tests
below (send_membership_email) are a separate, still-intact email use case
and are untouched by that migration.

Run:
  python3 manage.py test apps.billing.test_stripe_email_hardening --keepdb -v 2
"""
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import AuditLog, User
from apps.broadcasts.models import BroadcastChannel, ChannelMembership, ChannelMembershipTier
from apps.notifications.models import Notification


def _make_user(phone: str) -> User:
    user = User.objects.create_user(phone=phone, password="TestPass12!", country="CM")
    user.email = f"{phone.lstrip('+')}@example.com"
    user.save(update_fields=["email"])
    return user


@override_settings(SECURE_SSL_REDIRECT=False)
class StripeMembershipAndReceiptEmailFailureVisibilityTests(TestCase):
    def setUp(self):
        self.user = _make_user("+237699400001")
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.user.id, owner_user=self.user,
            handle="stripe-email-test-channel", display_name="Stripe Email Test Channel",
        )
        self.tier = ChannelMembershipTier.objects.create(
            channel=self.channel, title="Supporter", price_cents=500, currency="USD",
        )
        self.membership = ChannelMembership.objects.create(
            user=self.user, tier=self.tier, status="pending_payment",
        )
        self.client = APIClient()

    def _post_stripe_webhook(self):
        fake_event = {
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_test_123",
                    "amount": 500,
                    "currency": "usd",
                    "metadata": {
                        "target_type": "channel_membership",
                        "target_id": str(self.membership.id),
                        "user_id": str(self.user.id),
                    },
                }
            },
        }
        with patch("apps.billing.stripe_payments.verify_webhook", return_value=fake_event):
            return self.client.post(
                "/api/v1/billing/stripe/webhook/", {}, format="json",
                HTTP_STRIPE_SIGNATURE="test-sig", secure=True,
            )

    def test_receipt_email_is_no_longer_sent_automatically(self):
        with patch("apps.notifications.email_service.send_payment_receipt_email") as mock_receipt:
            res = self._post_stripe_webhook()

        self.assertEqual(res.status_code, 200)
        mock_receipt.assert_not_called()
        self.assertFalse(
            AuditLog.objects.filter(actor_id=self.user.id, action="email.payment_receipt.failed").exists()
        )
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, ChannelMembership.Status.ACTIVE)

    def test_payment_success_notification_is_created_instead(self):
        res = self._post_stripe_webhook()

        self.assertEqual(res.status_code, 200)
        notif = Notification.objects.filter(
            user_id=self.user.id, type="PAYMENT_SUCCESS", dedup_key="payment_success:pi_test_123",
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn("5.00", notif.body)

    def test_membership_confirmation_notification_uses_the_real_channel_name(self):
        # Regression coverage for the channel.name -> channel.display_name
        # fix, carried over from when this was an email assertion: the
        # confirmation is now an in-app MEMBERSHIP_CONFIRMED notification
        # (comms architecture migration, Sep 2026) rather than an email.
        res = self._post_stripe_webhook()

        self.assertEqual(res.status_code, 200)
        notif = Notification.objects.filter(
            user_id=self.user.id, type="MEMBERSHIP_CONFIRMED",
            context_data__membership_id=str(self.membership.id),
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn("Stripe Email Test Channel", notif.body)
        self.assertFalse(
            AuditLog.objects.filter(actor_id=self.user.id, action="email.membership.failed").exists()
        )

    def test_notification_failure_never_blocks_activation(self):
        with patch(
            "apps.notifications.services.create_notification",
            side_effect=RuntimeError("boom"),
        ):
            res = self._post_stripe_webhook()

        self.assertEqual(res.status_code, 200)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, ChannelMembership.Status.ACTIVE)
