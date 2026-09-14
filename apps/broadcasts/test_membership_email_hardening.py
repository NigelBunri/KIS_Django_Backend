"""
Phase 6: confirms a failed membership-confirmation email on the free-tier
join path is now logged + audited instead of vanishing via bare
`except: pass` (apps/broadcasts/views.py ChannelMembershipView.post).

Email-system audit, Priority 2: this file used to document a real,
separate bug (channel_name=channel.name — BroadcastChannel has no `name`
field/property, only `display_name` — so the send always raised
AttributeError before ever reaching send_membership_email). That's fixed
now (channel.display_name), here and in the two billing.py webhook
branches with the identical copy-paste mistake — this file's tests below
now cover the real success path instead of documenting the bug.

Run:
  python3 manage.py test apps.broadcasts.test_membership_email_hardening --keepdb -v 2
"""
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.accounts.models import AuditLog, User
from apps.accounts.views import issue_tokens_for_user
from apps.broadcasts.models import BroadcastChannel, ChannelMembershipTier
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
class FreeTierJoinMembershipEmailFailureVisibilityTests(TestCase):
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

    @patch("apps.notifications.email_service.send_membership_email", return_value=True)
    def test_membership_email_now_sends_successfully_with_the_real_channel_name(self, mock_send):
        # Regression test for the channel.name -> channel.display_name fix:
        # previously this always raised AttributeError before ever calling
        # send_membership_email at all.
        res = self._join()

        self.assertEqual(res.status_code, 201)
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.kwargs.get("channel_name"), "Free Tier Email Test Channel")
        self.assertFalse(
            AuditLog.objects.filter(actor_id=self.member.id, action="email.membership.failed").exists()
        )

    @patch("apps.notifications.email_service.send_membership_email", return_value=False)
    def test_membership_email_failure_is_still_logged_and_audited(self, _mock_send):
        res = self._join()

        self.assertEqual(res.status_code, 201)
        entry = AuditLog.objects.filter(actor_id=self.member.id, action="email.membership.failed").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.meta.get("channel_id"), str(self.channel.id))
