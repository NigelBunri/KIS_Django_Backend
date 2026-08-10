"""
Phase 6: confirms a failed membership-confirmation email on the free-tier
join path is now logged + audited instead of vanishing via bare
`except: pass` (apps/broadcasts/views.py ChannelMembershipView.post).

Also documents the same pre-existing `channel.name` bug found via
apps/billing/test_stripe_email_hardening.py — BroadcastChannel has no
`name` field/property, only `display_name`, so this send currently always
raises AttributeError rather than reaching send_membership_email's mocked
return value. Out of scope to fix here; see that file's docstring and the
Phase 6 report for the full explanation.

Run:
  python3 manage.py test apps.broadcasts.test_membership_email_hardening --keepdb -v 2
"""
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

    def test_free_tier_join_succeeds_even_though_the_confirmation_email_currently_fails(self):
        res = self._join()

        self.assertEqual(res.status_code, 201)
        self.assertTrue(res.data["joined"])

    def test_membership_email_failure_is_logged_and_audited(self):
        # Documents current real behavior (see module docstring): this
        # currently always raises AttributeError building channel_name.
        res = self._join()

        self.assertEqual(res.status_code, 201)
        entry = AuditLog.objects.filter(actor_id=self.member.id, action="email.membership.failed").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.meta.get("error"), "AttributeError")
        self.assertEqual(entry.meta.get("channel_id"), str(self.channel.id))
