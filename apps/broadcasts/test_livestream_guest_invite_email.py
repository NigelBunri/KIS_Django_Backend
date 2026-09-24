"""
Comms migration (Sep 2026): ChannelLiveStreamGuestsView.post used to email
the invitee directly. It now delivers via deliver_livestream_guest_invite_notice
instead — an in-app/push notification when the email resolves to an existing
verified KIS member, or a share link (from the guest's own unguessable
invite_token) handed to the inviter to pass along manually when the invitee
is genuinely external. Email stays available as the underlying
send_livestream_guest_invite_email function (see
SendLivestreamGuestInviteEmailTemplateTests below) but is no longer called
automatically from this flow.

Run:
  python3 manage.py test apps.broadcasts.test_livestream_guest_invite_email --keepdb -v 2
"""
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import Device, User
from apps.accounts.views import issue_tokens_for_user
from apps.broadcasts.models import BroadcastChannel, ChannelLiveStream, ChannelLiveStreamGuest
from apps.notifications.models import Notification

DEVICE_ID = "livestream-guest-invite-email-test-device"


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
class LivestreamGuestInviteEmailTests(TestCase):
    def setUp(self):
        self.owner = _make_user("+237699800001", display_name="Chidi O.")
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.owner.id, owner_user=self.owner,
            handle="livestream-guest-email-test-channel", display_name="Livestream Guest Email Test Channel",
        )
        self.live_stream = ChannelLiveStream.objects.create(channel=self.channel, title="Sunday Service")
        tokens = issue_tokens_for_user(self.owner, device_id=DEVICE_ID)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}", HTTP_X_DEVICE_ID=DEVICE_ID)

    def _invite(self, email="guest@example.com", role="guest"):
        return self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live_stream.id}/guests/",
            {"email": email, "role": role},
            format="json",
        )

    def test_external_invitee_gets_a_share_link_not_an_email(self):
        with patch("apps.notifications.email_service.send_livestream_guest_invite_email") as mock_send:
            res = self._invite(email="guest@example.com", role="cohost")

        self.assertEqual(res.status_code, 201, res.data)
        mock_send.assert_not_called()
        guest = ChannelLiveStreamGuest.objects.get(email="guest@example.com")
        self.assertIsNone(guest.user_id)
        self.assertIn(guest.invite_token, res.data["share_link"])

        notif = Notification.objects.filter(
            user_id=self.owner.id, type="GUEST_INVITATION_READY_TO_SHARE",
            dedup_key=f"guest_invite_share_link:{guest.id}",
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn(guest.invite_token, notif.body)

    def test_existing_verified_member_gets_in_app_notification_not_a_link(self):
        invited_user = _make_user("+237699800002", display_name="Existing Member")
        with patch("apps.notifications.email_service.send_livestream_guest_invite_email") as mock_send:
            res = self._invite(email=invited_user.email, role="guest")

        self.assertEqual(res.status_code, 201, res.data)
        mock_send.assert_not_called()
        self.assertIsNone(res.data.get("share_link"))
        guest = ChannelLiveStreamGuest.objects.get(email=invited_user.email)
        self.assertEqual(guest.user_id, invited_user.id)

        notif = Notification.objects.filter(
            user_id=invited_user.id, type="GUEST_INVITATION", dedup_key=f"guest_invite:{guest.id}",
        ).first()
        self.assertIsNotNone(notif)

    def test_unverified_email_match_is_not_resolved_to_that_account(self):
        unverified = _make_user("+237699800003", email_verified=False)
        res = self._invite(email=unverified.email, role="guest")

        self.assertEqual(res.status_code, 201, res.data)
        guest = ChannelLiveStreamGuest.objects.get(email=unverified.email)
        self.assertIsNone(guest.user_id)
        self.assertIn(guest.invite_token, res.data["share_link"])

    def test_direct_user_id_invite_never_attempts_to_email(self):
        invited_user = _make_user("+237699800004")
        with patch("apps.notifications.email_service.send_livestream_guest_invite_email") as mock_send:
            res = self.client.post(
                f"/api/v1/broadcasts/live-streams/{self.live_stream.id}/guests/",
                {"user_id": str(invited_user.id), "role": "guest"},
                format="json",
            )

        self.assertEqual(res.status_code, 201, res.data)
        mock_send.assert_not_called()

    def test_guest_creation_still_succeeds_even_when_notification_delivery_is_broken(self):
        with patch(
            "apps.notifications.services.create_notification",
            side_effect=RuntimeError("boom"),
        ):
            res = self._invite()

        self.assertEqual(res.status_code, 201, res.data)
        self.assertTrue(ChannelLiveStreamGuest.objects.filter(email="guest@example.com").exists())


class SendLivestreamGuestInviteEmailTemplateTests(TestCase):
    def test_renders_with_and_without_a_scheduled_time(self):
        from django.core import mail
        from apps.notifications.email_service import send_livestream_guest_invite_email

        sent = send_livestream_guest_invite_email(
            to_email="guest@example.com", inviter_name="Chidi O.", channel_name="Test Channel",
            stream_title="Sunday Service", role="cohost", invite_url="https://api.kis.app/broadcasts/live/join/tok123/",
            scheduled_start_at="October 01, 2026 at 09:00 UTC",
        )
        self.assertTrue(sent)
        html_body = mail.outbox[0].alternatives[0][0]
        self.assertIn("tok123", html_body)
        self.assertIn("October 01, 2026", html_body)

        mail.outbox.clear()
        send_livestream_guest_invite_email(
            to_email="guest@example.com", inviter_name="Chidi O.", channel_name="Test Channel",
            stream_title="Sunday Service", role="guest", invite_url="https://api.kis.app/broadcasts/live/join/tok456/",
        )
        self.assertEqual(len(mail.outbox), 1)

    def test_inviter_name_and_titles_are_html_escaped(self):
        from django.core import mail
        from apps.notifications.email_service import send_livestream_guest_invite_email

        send_livestream_guest_invite_email(
            to_email="guest@example.com",
            inviter_name="<script>alert(1)</script>",
            channel_name="Test Channel",
            stream_title="<img src=x onerror=alert(2)>",
            role="guest",
            invite_url="https://api.kis.app/broadcasts/live/join/tok789/",
        )
        html_body = mail.outbox[0].alternatives[0][0]
        self.assertNotIn("<script>", html_body)
        self.assertNotIn("<img src=x", html_body)
