"""
Comms migration (Sep 2026): the gift-membership endpoint
(ChannelMembershipGiftView) used to email recipient_email directly on
every gift creation. It now delivers via deliver_gift_membership_notice
instead — an in-app/push GIFT_RECEIVED notification when recipient_email
resolves to an existing verified KIS member, or a share link (from the
gift's own unguessable redeem_token) handed to the gifter to pass along
manually when the recipient is genuinely external. Email stays available
as the underlying send_gift_membership_email function (see
SendGiftMembershipEmailTemplateTests below) but is no longer called
automatically from this flow.

Run:
  python3 manage.py test apps.broadcasts.test_gift_membership_email --keepdb -v 2
"""
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import Device, User
from apps.accounts.views import issue_tokens_for_user
from apps.broadcasts.models import BroadcastChannel, ChannelMembershipGift, ChannelMembershipTier
from apps.notifications.models import Notification

DEVICE_ID = "gift-membership-email-test-device"


def _make_user(phone: str, display_name: str = "", email_verified: bool = True) -> User:
    user = User.objects.create_user(phone=phone, password="TestPass12!", country="CM")
    user.email = f"{phone.lstrip('+')}@example.com"
    user.email_verified = email_verified
    user.status = "active"
    user.is_active = True
    if display_name:
        user.display_name = display_name
    user.save(update_fields=["email", "email_verified", "status", "is_active", "display_name"])
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
        # Free tier deliberately - the notification/link leg only fires
        # immediately for free-tier gifts. Paid-tier gifts defer delivery
        # until payment is confirmed (see test_gift_membership_payment.py).
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

    def test_external_recipient_gets_a_share_link_not_an_email(self):
        with patch("apps.notifications.email_service.send_gift_membership_email") as mock_send:
            res = self._gift(recipient_email="giftee@example.com", message="Happy birthday!")

        self.assertEqual(res.status_code, 201, res.data)
        mock_send.assert_not_called()
        gift = ChannelMembershipGift.objects.get(recipient_email="giftee@example.com")
        self.assertIn(gift.redeem_token, res.data["share_link"])
        self.assertIsNone(gift.recipient_id)

        # Gifter is notified in-app with the same link, since KIS never
        # emails it directly.
        notif = Notification.objects.filter(
            user_id=self.gifter.id, type="GIFT_READY_TO_SHARE", dedup_key=f"gift_share_link:{gift.id}",
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn(gift.redeem_token, notif.body)

    def test_existing_verified_member_gets_in_app_notification_not_a_link(self):
        member = _make_user("+237699600003", display_name="Existing Member")
        with patch("apps.notifications.email_service.send_gift_membership_email") as mock_send:
            res = self._gift(recipient_email=member.email)

        self.assertEqual(res.status_code, 201, res.data)
        mock_send.assert_not_called()
        self.assertIsNone(res.data.get("share_link"))
        gift = ChannelMembershipGift.objects.get(recipient_email=member.email)
        self.assertEqual(gift.recipient_id, member.id)

        notif = Notification.objects.filter(
            user_id=member.id, type="GIFT_RECEIVED", dedup_key=f"gift_received:{gift.id}",
        ).first()
        self.assertIsNotNone(notif)

    def test_unverified_email_match_is_not_resolved_to_that_account(self):
        # An unverified email must never let a gift silently attach to
        # someone else's account (mirrors ParentRecoveryInitView's rule).
        unverified = _make_user("+237699600004", email_verified=False)
        res = self._gift(recipient_email=unverified.email)

        self.assertEqual(res.status_code, 201, res.data)
        gift = ChannelMembershipGift.objects.get(recipient_email=unverified.email)
        self.assertIsNone(gift.recipient_id)
        self.assertIn(gift.redeem_token, res.data["share_link"])

    def test_no_recipient_email_never_attempts_to_send(self):
        with patch("apps.notifications.email_service.send_gift_membership_email") as mock_send:
            res = self._gift(recipient_email="")

        self.assertEqual(res.status_code, 201, res.data)
        mock_send.assert_not_called()

    def test_gift_creation_still_succeeds_even_when_notification_delivery_is_broken(self):
        # The gift record itself must never be blocked by a notification
        # failure - deliver_gift_membership_notice swallows exceptions
        # raised by create_notification internally.
        with patch(
            "apps.notifications.services.create_notification",
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
