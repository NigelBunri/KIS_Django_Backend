"""
Email-system audit, Priority 2 discovery #4 ("Never fires", structurally
identical to gift-membership): ChannelLiveStreamGuestsView.post created the
ChannelLiveStreamGuest record (with its invite_token) but never notified
the invitee at all — neither an external email invite nor an existing app
user learned they'd been invited.

Run:
  python3 manage.py test apps.broadcasts.test_livestream_guest_invite_email --keepdb -v 2
"""
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import AuditLog, Device, User
from apps.accounts.views import issue_tokens_for_user
from apps.broadcasts.models import BroadcastChannel, ChannelLiveStream, ChannelLiveStreamGuest

DEVICE_ID = "livestream-guest-invite-email-test-device"


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

    @patch("apps.notifications.email_service.send_livestream_guest_invite_email", return_value=True)
    def test_invite_email_is_now_sent_on_guest_creation(self, mock_send):
        res = self._invite(email="guest@example.com", role="cohost")

        self.assertEqual(res.status_code, 201, res.data)
        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        self.assertEqual(kwargs["to_email"], "guest@example.com")
        self.assertEqual(kwargs["inviter_name"], "Chidi O.")
        self.assertEqual(kwargs["channel_name"], "Livestream Guest Email Test Channel")
        self.assertEqual(kwargs["stream_title"], "Sunday Service")
        self.assertEqual(kwargs["role"], "cohost")
        guest = ChannelLiveStreamGuest.objects.get(email="guest@example.com")
        self.assertIn(guest.invite_token, kwargs["invite_url"])

    @patch("apps.notifications.email_service.send_livestream_guest_invite_email", return_value=True)
    def test_no_email_never_attempts_to_send(self, mock_send):
        invited_user = _make_user("+237699800002")
        res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live_stream.id}/guests/",
            {"user_id": str(invited_user.id), "role": "guest"},
            format="json",
        )

        self.assertEqual(res.status_code, 201, res.data)
        mock_send.assert_not_called()

    @patch("apps.notifications.email_service.send_livestream_guest_invite_email", return_value=False)
    def test_send_failure_is_logged_and_audited_without_failing_the_request(self, _mock_send):
        res = self._invite()

        self.assertEqual(res.status_code, 201, res.data)
        guest = ChannelLiveStreamGuest.objects.get(email="guest@example.com")
        entry = AuditLog.objects.filter(actor_id=self.owner.id, action="email.livestream_guest_invite.failed").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.meta.get("guest_id"), str(guest.id))

    @patch("apps.notifications.email_service.send_livestream_guest_invite_email", side_effect=RuntimeError("boom"))
    def test_send_exception_never_blocks_guest_creation(self, _mock_send):
        res = self._invite()

        self.assertEqual(res.status_code, 201, res.data)
        self.assertTrue(ChannelLiveStreamGuest.objects.filter(email="guest@example.com").exists())
        entry = AuditLog.objects.filter(actor_id=self.owner.id, action="email.livestream_guest_invite.failed").first()
        self.assertEqual(entry.meta.get("error"), "RuntimeError")


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
