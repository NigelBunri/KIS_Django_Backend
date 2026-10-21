"""
Paid ChannelMembership rows never expired: MembershipScreen.tsx has always
labeled a paid tier "{currency} {price}/mo", but the Flutterwave/Stripe
webhook activation paths (apps.billing.views) never set expires_at at all,
while the structurally identical gift-membership redemption correctly
grants a 30-day expires_at. Fixed by setting expires_at on both webhook
activation paths and adding a Celery Beat sweep
(sweep_expired_channel_memberships_task) that flips ACTIVE rows past their
expires_at to EXPIRED, mirroring apps.billing.services.sweep_expired_
subscriptions for AccountTier Subscriptions.

Run:
  python3 manage.py test apps.broadcasts.test_channel_membership_expiry --keepdb -v 2
"""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import WalletTransaction

from .models import BroadcastChannel, ChannelMembership, ChannelMembershipTier
from .tasks import sweep_expired_channel_memberships_task


def _make_user(phone: str) -> User:
    return User.objects.create_user(phone=phone, password="TestPass12!", country="CM")


@override_settings(SECURE_SSL_REDIRECT=False, FLW_WEBHOOK_SECRET="test-webhook-secret")
class FlutterwaveMembershipWebhookSetsExpiryTests(TestCase):
    def setUp(self):
        self.owner = _make_user("+237699710001")
        self.member = _make_user("+237699710002")
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.owner.id, owner_user=self.owner,
            handle="flw-membership-expiry-channel", display_name="FLW Membership Expiry Channel",
        )
        self.tier = ChannelMembershipTier.objects.create(
            channel=self.channel, title="Gold", price_cents=500, currency="USD", is_active=True,
        )
        self.membership = ChannelMembership.objects.create(
            user=self.member, tier=self.tier, status="pending_payment",
        )
        self.tx = WalletTransaction.objects.create(
            user=self.member, provider="flutterwave", method="card",
            amount_cents=500, currency="USD", status="pending",
            tx_ref=f"KIS-MEM-{self.membership.id}",
            meta={"target_type": "channel_membership", "target_id": str(self.membership.id), "user_id": str(self.member.id)},
        )
        self.client = APIClient()

    def test_webhook_activation_sets_a_thirty_day_expiry(self):
        res = self.client.post(
            "/api/v1/wallet/webhook/flutterwave/",
            {"data": {"tx_ref": self.tx.tx_ref, "status": "successful", "id": "flw-evt-mem-1", "currency": "USD"}},
            format="json",
            HTTP_VERIF_HASH="test-webhook-secret",
            secure=True,
        )

        self.assertEqual(res.status_code, 200)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, ChannelMembership.Status.ACTIVE)
        self.assertIsNotNone(self.membership.expires_at)
        self.assertGreater(self.membership.expires_at, timezone.now() + timedelta(days=29))
        self.assertLess(self.membership.expires_at, timezone.now() + timedelta(days=31))


class SweepExpiredChannelMembershipsTests(TestCase):
    def setUp(self):
        self.owner = _make_user("+237699710003")
        self.member = _make_user("+237699710004")
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.owner.id, owner_user=self.owner,
            handle="sweep-expiry-channel", display_name="Sweep Expiry Channel",
        )
        self.tier = ChannelMembershipTier.objects.create(channel=self.channel, title="Gold", price_cents=500)

    def test_sweep_expires_a_lapsed_active_membership(self):
        membership = ChannelMembership.objects.create(
            user=self.member, tier=self.tier, status=ChannelMembership.Status.ACTIVE,
            expires_at=timezone.now() - timedelta(days=1),
        )

        result = sweep_expired_channel_memberships_task()

        membership.refresh_from_db()
        self.assertEqual(membership.status, ChannelMembership.Status.EXPIRED)
        self.assertEqual(result["expired"], 1)

    def test_sweep_leaves_a_not_yet_expired_membership_active(self):
        membership = ChannelMembership.objects.create(
            user=self.member, tier=self.tier, status=ChannelMembership.Status.ACTIVE,
            expires_at=timezone.now() + timedelta(days=10),
        )

        sweep_expired_channel_memberships_task()

        membership.refresh_from_db()
        self.assertEqual(membership.status, ChannelMembership.Status.ACTIVE)

    def test_sweep_ignores_free_membership_with_no_expiry(self):
        membership = ChannelMembership.objects.create(
            user=self.member, tier=self.tier, status=ChannelMembership.Status.ACTIVE, expires_at=None,
        )

        sweep_expired_channel_memberships_task()

        membership.refresh_from_db()
        self.assertEqual(membership.status, ChannelMembership.Status.ACTIVE)
