"""
Email-system audit, Priority 2 (row 10, "Never fires"): the gift-membership
endpoint (ChannelMembershipGiftView) creates the ChannelMembershipGift
record but previously never emailed recipient_email at all — the only way
to "learn the gift exists" was the gifter telling them out of band.

Run:
  python3 manage.py test apps.broadcasts.test_gift_membership_email --keepdb -v 2
"""
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import AuditLog, Device, User
from apps.accounts.views import issue_tokens_for_user
from apps.broadcasts.models import BroadcastChannel, ChannelMembershipGift, ChannelMembershipTier

DEVICE_ID = "gift-membership-email-test-device"


def _make_user(phone: str, display_name: str = "") -> User:
    user = User.objects.create_user(phone=phone, password="TestPass12!", country="CM")
    user.email = f"{phone.lstrip('+')}@example.com"
    user.status = "active"
    user.is_active = True
    if display_name:
        user.display_name = display_name
    user.save(update_fields=["email", "status", "is_active", "display_name"])
    Device.objects.create(user=user, device_id=DEVICE_ID, platform="android", is_parent=True, token_version=1)
    return user


@override_settings(SECURE_SSL_REDIRECT=False)
class GiftMembershipEmailTests(TestCase):
    def setUp(self):
        self.gifter = _make_user("+237699600001", display_name="Aisha K.")
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.gifter.id, owner_user=self.gifter,
            handle="gift-email-test-channel", display_name="Gift Email Test Channel",
        )
        # Free tier deliberately - this file tests the email leg, which
        # only fires immediately for free-tier gifts. Paid-tier gifts
        # defer the recipient email until payment is confirmed (see
        # test_gift_membership_payment.py), so a price_cents>0 fixture
        # here would make every "email sent on creation" assertion below
        # false for the very case they're meant to test.
        self.tier = ChannelMembershipTier.objects.create(
            channel=self.channel, title="Supporter", price_cents=0, currency="USD", is_active=True,
        )
        tokens = issue_tokens_for_user(self.gifter, device_id=DEVICE_ID)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}", HTTP_X_DEVICE_ID=DEVICE_ID)

    def _gift(self, recipient_email="recipient@example.com", message=""):
        return self.client.post(
            "/api/v1/broadcasts/memberships/gift/",
            {"tier_id": str(self.tier.id), "recipient_email": recipient_email, "message": message},
            format="json",
        )

    @patch("apps.notifications.email_service.send_gift_membership_email", return_value=True)
    def test_recipient_email_is_now_sent_on_gift_creation(self, mock_send):
        res = self._gift(recipient_email="giftee@example.com", message="Happy birthday!")

        self.assertEqual(res.status_code, 201, res.data)
        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        self.assertEqual(kwargs["to_email"], "giftee@example.com")
        self.assertEqual(kwargs["gifter_name"], "Aisha K.")
        self.assertEqual(kwargs["tier_title"], "Supporter")
        self.assertEqual(kwargs["channel_name"], "Gift Email Test Channel")
        self.assertEqual(kwargs["message"], "Happy birthday!")
        gift = ChannelMembershipGift.objects.get(recipient_email="giftee@example.com")
        self.assertEqual(kwargs["redeem_code"], gift.redeem_token)

    @patch("apps.notifications.email_service.send_gift_membership_email", return_value=True)
    def test_gifter_falls_back_to_username_when_no_display_name(self, mock_send):
        plain_gifter = _make_user("+237699600002")
        plain_gifter.username = "plaingifter"
        plain_gifter.save(update_fields=["username"])
        tokens = issue_tokens_for_user(plain_gifter, device_id=DEVICE_ID + "-2")
        Device.objects.filter(user=plain_gifter).update(device_id=DEVICE_ID + "-2")
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}", HTTP_X_DEVICE_ID=DEVICE_ID + "-2")

        res = client.post(
            "/api/v1/broadcasts/memberships/gift/",
            {"tier_id": str(self.tier.id), "recipient_email": "giftee2@example.com"},
            format="json",
        )

        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(mock_send.call_args.kwargs["gifter_name"], "plaingifter")

    @patch("apps.notifications.email_service.send_gift_membership_email")
    def test_no_recipient_email_never_attempts_to_send(self, mock_send):
        res = self._gift(recipient_email="")

        self.assertEqual(res.status_code, 201, res.data)
        mock_send.assert_not_called()

    @patch("apps.notifications.email_service.send_gift_membership_email", return_value=False)
    def test_send_failure_is_logged_and_audited_without_failing_the_request(self, _mock_send):
        res = self._gift()

        self.assertEqual(res.status_code, 201, res.data)
        gift = ChannelMembershipGift.objects.get(recipient_email="recipient@example.com")
        entry = AuditLog.objects.filter(actor_id=self.gifter.id, action="email.gift_membership.failed").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.meta.get("gift_id"), str(gift.id))

    @patch("apps.notifications.email_service.send_gift_membership_email", side_effect=RuntimeError("provider down"))
    def test_send_exception_is_caught_logged_and_audited(self, _mock_send):
        res = self._gift()

        self.assertEqual(res.status_code, 201, res.data)
        entry = AuditLog.objects.filter(actor_id=self.gifter.id, action="email.gift_membership.failed").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.meta.get("error"), "RuntimeError")

    def test_gift_creation_still_succeeds_even_when_email_module_is_broken(self):
        # The gift record itself must never be blocked by an email failure.
        with patch(
            "apps.notifications.email_service.send_gift_membership_email",
            side_effect=RuntimeError("boom"),
        ):
            res = self._gift()
        self.assertEqual(res.status_code, 201, res.data)
        self.assertTrue(ChannelMembershipGift.objects.filter(recipient_email="recipient@example.com").exists())


class SendGiftMembershipEmailTemplateTests(TestCase):
    def test_renders_with_and_without_a_personal_message(self):
        from django.core import mail
        from apps.notifications.email_service import send_gift_membership_email

        sent = send_gift_membership_email(
            to_email="giftee@example.com", gifter_name="Aisha K.", tier_title="Supporter",
            channel_name="Test Channel", redeem_code="abc123", expires_at="October 01, 2026",
            message="Enjoy!",
        )
        self.assertTrue(sent)
        self.assertEqual(len(mail.outbox), 1)
        html_body = mail.outbox[0].alternatives[0][0]
        self.assertIn("abc123", html_body)
        self.assertIn("Enjoy!", html_body)
        self.assertIn("Aisha K.", mail.outbox[0].subject)

        mail.outbox.clear()
        sent_no_message = send_gift_membership_email(
            to_email="giftee@example.com", gifter_name="Aisha K.", tier_title="Supporter",
            channel_name="Test Channel", redeem_code="abc123", expires_at="October 01, 2026",
        )
        self.assertTrue(sent_no_message)
        self.assertEqual(len(mail.outbox), 1)

    def test_gifter_name_and_message_are_html_escaped(self):
        from django.core import mail
        from apps.notifications.email_service import send_gift_membership_email

        send_gift_membership_email(
            to_email="giftee@example.com",
            gifter_name="<script>alert(1)</script>",
            tier_title="Supporter",
            channel_name="Test Channel",
            redeem_code="abc123",
            expires_at="October 01, 2026",
            message="<img src=x onerror=alert(2)>",
        )
        html_body = mail.outbox[0].alternatives[0][0]
        self.assertNotIn("<script>", html_body)
        self.assertNotIn("<img src=x", html_body)
