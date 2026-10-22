"""
ChannelLiveStreamTipView/TipListView had zero dedicated test coverage
despite a real Flutterwave payment-intent integration behind them
(create_direct_payment_intent -> _mark_target_paid, the same shared
pipeline apps/billing/test_marketplace_payments.py already covers for
ChannelContentTip/VOD tips - that file's own docstring even names
ChannelLiveStreamTipView as covered, but no test there ever exercised it).

Covers: pending-not-instant-complete, payout-eligibility gating, minimum
amount validation, _mark_target_paid completing a tip, the Super Chat
auto-pin rule (>=1000 cents pins for 2 minutes, below that doesn't), and
TipListView only ever surfacing COMPLETED tips (a PENDING tip must never
be visible to other viewers as if it had already landed).

Run:
  python3 manage.py test apps.broadcasts.test_channel_live_stream_tips --keepdb -v 2
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.billing.models import DirectPaymentIntent
from apps.broadcasts.models import (
    BroadcastChannel,
    BroadcastChannelPayoutAccountStatus,
    BroadcastChannelRole,
    ChannelLiveStream,
    ChannelLiveStreamTip,
)

User = get_user_model()


class LiveStreamTipTestBase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(phone="+2349130000001", password="pw123456", country="NG")
        self.tipper = User.objects.create_user(phone="+2349130000002", password="pw123456", country="NG")
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER,
            owner_id=self.owner.id,
            owner_user=self.owner,
            handle="tip-channel",
            display_name="Tip Channel",
            is_public=True,
            payout_account_status=BroadcastChannelPayoutAccountStatus.ACTIVE,
            flutterwave_subaccount_id="RS_TEST_LIVE_TIP",
        )
        BroadcastChannelRole.objects.create(channel=self.channel, user=self.owner, role=BroadcastChannelRole.Role.OWNER)
        self.live = ChannelLiveStream.objects.create(channel=self.channel, title="Live tipping test")
        self.client = APIClient()


class LiveStreamTipCreationTests(LiveStreamTipTestBase):
    def test_tip_is_pending_and_creates_payment_intent_not_instant_complete(self):
        self.client.force_authenticate(self.tipper)
        with patch("apps.billing.direct_payments._ensure_provider_payment_link", side_effect=lambda intent, actor=None: intent):
            res = self.client.post(
                f"/api/v1/broadcasts/live-streams/{self.live.id}/tip/",
                {"amount_cents": 500, "message": "Great stream!"},
                format="json",
            )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        tip = ChannelLiveStreamTip.objects.get(live_stream=self.live, user=self.tipper)
        self.assertEqual(tip.status, ChannelLiveStreamTip.Status.PENDING)
        self.assertTrue(res.data["payment_required"])
        self.assertTrue(
            DirectPaymentIntent.objects.filter(
                target_type=DirectPaymentIntent.TARGET_CHANNEL_TIP, target_id=tip.id,
            ).exists()
        )

    def test_rejects_below_minimum_amount(self):
        self.client.force_authenticate(self.tipper)
        res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live.id}/tip/",
            {"amount_cents": 50},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_non_integer_amount(self):
        self.client.force_authenticate(self.tipper)
        res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live.id}/tip/",
            {"amount_cents": "not-a-number"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_requires_authentication(self):
        res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live.id}/tip/",
            {"amount_cents": 500},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_blocked_when_channel_has_no_payout_setup(self):
        self.channel.payout_account_status = BroadcastChannelPayoutAccountStatus.NOT_CONNECTED
        self.channel.flutterwave_subaccount_id = ""
        self.channel.save(update_fields=["payout_account_status", "flutterwave_subaccount_id"])

        self.client.force_authenticate(self.tipper)
        res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live.id}/tip/",
            {"amount_cents": 500},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_402_PAYMENT_REQUIRED)


class LiveStreamTipPaymentConfirmationTests(LiveStreamTipTestBase):
    def _paid_intent_for(self, tip, amount_cents):
        return DirectPaymentIntent.objects.create(
            user=self.tipper, provider="flutterwave",
            target_type=DirectPaymentIntent.TARGET_CHANNEL_TIP, target_id=tip.id,
            amount_cents=amount_cents, currency="USD",
            status=DirectPaymentIntent.STATUS_PAID,
            tx_ref=f"kis_direct_live_tip_test_{tip.id}",
        )

    def test_mark_target_paid_completes_the_tip(self):
        from apps.billing.direct_payments import _mark_target_paid

        tip = ChannelLiveStreamTip.objects.create(
            live_stream=self.live, user=self.tipper, amount_cents=500,
            status=ChannelLiveStreamTip.Status.PENDING,
        )
        intent = self._paid_intent_for(tip, 500)

        _mark_target_paid(intent, {})

        tip.refresh_from_db()
        self.assertEqual(tip.status, ChannelLiveStreamTip.Status.COMPLETED)
        self.assertEqual(tip.payment_reference, intent.tx_ref)

    def test_super_chat_tip_at_or_above_threshold_gets_pinned(self):
        from apps.billing.direct_payments import _mark_target_paid

        tip = ChannelLiveStreamTip.objects.create(
            live_stream=self.live, user=self.tipper, amount_cents=1000,
            status=ChannelLiveStreamTip.Status.PENDING,
        )
        intent = self._paid_intent_for(tip, 1000)

        _mark_target_paid(intent, {})

        tip.refresh_from_db()
        self.assertTrue(tip.is_pinned)
        self.assertIsNotNone(tip.pinned_until)
        self.assertGreater(tip.pinned_until, timezone.now())

    def test_tip_below_super_chat_threshold_is_not_pinned(self):
        from apps.billing.direct_payments import _mark_target_paid

        tip = ChannelLiveStreamTip.objects.create(
            live_stream=self.live, user=self.tipper, amount_cents=999,
            status=ChannelLiveStreamTip.Status.PENDING,
        )
        intent = self._paid_intent_for(tip, 999)

        _mark_target_paid(intent, {})

        tip.refresh_from_db()
        self.assertFalse(tip.is_pinned)
        self.assertIsNone(tip.pinned_until)


class LiveStreamTipListVisibilityTests(LiveStreamTipTestBase):
    def test_only_completed_tips_are_listed(self):
        ChannelLiveStreamTip.objects.create(
            live_stream=self.live, user=self.tipper, amount_cents=500,
            status=ChannelLiveStreamTip.Status.COMPLETED,
        )
        ChannelLiveStreamTip.objects.create(
            live_stream=self.live, user=self.tipper, amount_cents=700,
            status=ChannelLiveStreamTip.Status.PENDING,
        )
        ChannelLiveStreamTip.objects.create(
            live_stream=self.live, user=self.tipper, amount_cents=300,
            status=ChannelLiveStreamTip.Status.FAILED,
        )

        self.client.force_authenticate(self.tipper)
        res = self.client.get(f"/api/v1/broadcasts/live-streams/{self.live.id}/tips/")

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        amounts = {row["amount_cents"] for row in res.data}
        self.assertEqual(amounts, {500})
