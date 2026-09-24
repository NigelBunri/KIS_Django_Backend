"""
Comms architecture migration (Sep 2026): free-tier membership confirmation
moved from email to an in-app+push MEMBERSHIP_CONFIRMED notification - the
member is standing in the app joining right now, so email added nothing a
notification doesn't already cover. These tests now confirm (1) no email is
ever sent for a free-tier join, and (2) the in-app notification is created
correctly instead.

Previously this file covered a since-fixed bug (channel.name ->
channel.display_name AttributeError) in the email path that no longer
exists - superseded by the migration below.

Run:
  python3 manage.py test apps.broadcasts.test_membership_email_hardening --keepdb -v 2
"""
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.accounts.views import issue_tokens_for_user
from apps.broadcasts.models import BroadcastChannel, ChannelMembershipTier
from apps.notifications.models import Notification
from rest_framework.test import APIClient

DEVICE_ID = "membership-email-test-device"


def _make_user(phone: str) -> User:
    from apps.accounts.models import Device

    user = User.objects.create_user(phone=phone, password="TestPass12!", country="CM")
    user.email = f"{phone.lstrip('+')}@example.com"
    user.status = "active"
    user.is_active = True
    user.save(update_fields=["email", "status", "is_active"])
    Device.objects.create(
        user=user, device_id=DEVICE_ID, platform="android",
        is_parent=True, token_version=1,
    )
    return user


@override_settings(SECURE_SSL_REDIRECT=False)
class FreeTierJoinMembershipNotificationTests(TestCase):
    def setUp(self):
        self.owner = _make_user("+237699500001")
        self.member = _make_user("+237699500002")
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.owner.id, owner_user=self.owner,
            handle="free-tier-email-test-channel", display_name="Free Tier Email Test Channel",
        )
        self.tier = ChannelMembershipTier.objects.create(
            channel=self.channel, title="Free Supporter", price_cents=0, currency="USD",
        )
        tokens = issue_tokens_for_user(self.member, device_id=DEVICE_ID)
        self.client = APIClient()
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {tokens['access']}", HTTP_X_DEVICE_ID=DEVICE_ID,
        )

    def _join(self):
        return self.client.post(
            f"/api/v1/broadcasts/channels/{self.channel.id}/membership/",
            {"tier_id": str(self.tier.id)}, format="json",
        )

    def test_free_tier_join_succeeds(self):
        res = self._join()

        self.assertEqual(res.status_code, 201)
        self.assertTrue(res.data["joined"])

    @patch("apps.notifications.email_service.send_membership_email")
    def test_free_tier_join_never_sends_email(self, mock_send):
        res = self._join()

        self.assertEqual(res.status_code, 201)
        mock_send.assert_not_called()

    def test_free_tier_join_creates_membership_confirmed_notification(self):
        res = self._join()

        self.assertEqual(res.status_code, 201)
        notif = Notification.objects.filter(
            user_id=self.member.id, type="MEMBERSHIP_CONFIRMED",
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn("Free Supporter", notif.title)
        self.assertIn("Free Tier Email Test Channel", notif.body)
        deliveries = set(notif.deliveries.values_list("channel", flat=True))
        self.assertIn("IN_APP", deliveries)
        self.assertIn("PUSH", deliveries)

    @patch("apps.notifications.services.create_notification", side_effect=RuntimeError("boom"))
    def test_notification_failure_does_not_break_the_join(self, _mock_create):
        # Mirrors the old email path's non-blocking guarantee: a failure in
        # the confirmation channel must never fail the join itself.
        res = self._join()

        self.assertEqual(res.status_code, 201)
        self.assertTrue(res.data["joined"])
