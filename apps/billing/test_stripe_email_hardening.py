"""
Phase 6: confirms failed membership/receipt emails from the Stripe webhook
handler (payment_intent.succeeded, channel_membership target) are now
logged + audited instead of vanishing via bare `except: pass`
(apps/billing/views.py StripeWebhookView).

Stripe signature verification is mocked at its call site
(apps.billing.stripe_payments.verify_webhook) rather than exercised for
real — this test targets the email-failure-visibility fix, not Stripe's
signing scheme.

Also documents a pre-existing, separate bug this hardening surfaced (it
was previously invisible behind a bare `except: pass`): the membership
email branch builds `channel_name=membership.tier.channel.name`, but
BroadcastChannel has no `name` field/property — only `display_name`. This
means the membership-email attempt in this webhook currently raises
AttributeError unconditionally, regardless of send_membership_email's own
mocked return value. That's a real, separate bug flagged in the Phase 6
report — out of scope to fix here (this phase hardens the email SEND path,
not BroadcastChannel's attribute contract). The tests below assert the
CURRENT actual behavior (an always-caught, always-logged, always-audited
exception) rather than an unreachable "success" path for that branch.

Run:
  python3 manage.py test apps.billing.test_stripe_email_hardening --keepdb -v 2
"""
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import AuditLog, User
from apps.broadcasts.models import BroadcastChannel, ChannelMembership, ChannelMembershipTier


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

    @patch("apps.notifications.email_service.send_payment_receipt_email", return_value=False)
    def test_failed_receipt_email_is_logged_and_audited(self, _mock_receipt):
        res = self._post_stripe_webhook()

        self.assertEqual(res.status_code, 200)
        self.assertTrue(
            AuditLog.objects.filter(actor_id=self.user.id, action="email.payment_receipt.failed").exists()
        )
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, ChannelMembership.Status.ACTIVE)

    @patch("apps.notifications.email_service.send_payment_receipt_email", return_value=True)
    def test_successful_receipt_email_does_not_create_a_failure_audit_entry(self, _mock_receipt):
        res = self._post_stripe_webhook()

        self.assertEqual(res.status_code, 200)
        self.assertFalse(
            AuditLog.objects.filter(actor_id=self.user.id, action="email.payment_receipt.failed").exists()
        )

    def test_membership_email_currently_always_fails_due_to_the_pre_existing_channel_name_bug(self):
        # Documents current real behavior — see module docstring. If/when
        # BroadcastChannel.name (or the view's reference to it) is fixed,
        # this test should be replaced with a real success-path test.
        res = self._post_stripe_webhook()

        self.assertEqual(res.status_code, 200)
        entry = AuditLog.objects.filter(actor_id=self.user.id, action="email.membership.failed").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.meta.get("error"), "AttributeError")
