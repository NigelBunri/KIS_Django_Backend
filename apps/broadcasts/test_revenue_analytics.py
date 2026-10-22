"""
P1.3 monetization gap: ChannelRevenueAnalyticsView had zero test coverage
and, it turns out, two real contract bugs against its only actual caller
(RevenueAnalyticsPanel.tsx):

  1. The frontend sends `?period=7d|30d|90d`; the backend only ever read
     `?days=N`. Every period-selector tap in the UI silently did nothing.
  2. The backend returned nested objects (`super_thanks.total_cents`, ...);
     the frontend's RevenueData type reads flat fields
     (`super_thanks_amount`, ...). Every revenue figure except
     `ad_impressions` always rendered as a confident $0 regardless of
     actual revenue - indistinguishable from "you made no money."

Also covers the companion ad-revenue gap: ad impressions used to be
counted but never priced into `total_estimated_cents` at all.

Run:
  python3 manage.py test apps.broadcasts.test_revenue_analytics --keepdb -v 2
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from .models import (
    BroadcastChannel,
    BroadcastChannelRole,
    ChannelAdCampaign,
    ChannelAdImpression,
    ChannelAdSlot,
    ChannelContent,
    ChannelContentTip,
    ChannelLiveStream,
    ChannelLiveStreamTip,
    ChannelMembership,
    ChannelMembershipTier,
)

User = get_user_model()


class ChannelRevenueAnalyticsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(phone="+2349170000001", password="pw123456", country="NG")
        self.tipper = User.objects.create_user(phone="+2349170000002", password="pw123456", country="NG")
        self.member = User.objects.create_user(phone="+2349170000003", password="pw123456", country="NG")
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.owner.id, owner_user=self.owner,
            handle="revenue-channel", display_name="Revenue Channel", is_public=True,
        )
        BroadcastChannelRole.objects.create(channel=self.channel, user=self.owner, role=BroadcastChannelRole.Role.OWNER)
        self.content = ChannelContent.objects.create(
            channel=self.channel, content_type="video", title="Monetized", created_by=self.owner,
        )
        ChannelContentTip.objects.create(
            content=self.content, user=self.tipper, amount_cents=500,
            status=ChannelContentTip.Status.COMPLETED,
        )
        tier = ChannelMembershipTier.objects.create(channel=self.channel, title="Gold", price_cents=1000)
        ChannelMembership.objects.create(user=self.member, tier=tier, status=ChannelMembership.Status.ACTIVE)
        campaign = ChannelAdCampaign.objects.create(
            advertiser_channel=self.channel, title="Promo", budget_cents=10000,
            status=ChannelAdCampaign.Status.ACTIVE,
        )
        slot = ChannelAdSlot.objects.create(content=self.content, campaign=campaign)
        ChannelAdImpression.objects.create(slot=slot, user=self.tipper)
        ChannelAdImpression.objects.create(slot=slot, user=self.member)
        self.client = APIClient()

    def test_flat_response_fields_match_the_frontend_contract(self):
        self.client.force_authenticate(self.owner)

        res = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/revenue/")

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertEqual(res.data["super_thanks_amount"], 500)
        self.assertEqual(res.data["super_thanks_count"], 1)
        self.assertEqual(res.data["memberships_active"], 1)
        # Dollars, not cents - the one field the frontend uses un-divided.
        self.assertEqual(res.data["memberships_est_monthly"], 10.0)
        self.assertEqual(res.data["ad_impressions"], 2)

    def test_ad_revenue_is_counted_in_the_total(self):
        self.client.force_authenticate(self.owner)

        res = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/revenue/")

        # 500 (tip) + 1000 (membership) + 2*10 (ad impressions) = 1520
        self.assertEqual(res.data["total_estimated_cents"], 1520)

    def test_period_query_param_actually_changes_the_window(self):
        old_tip = ChannelContentTip.objects.create(
            content=self.content, user=self.tipper, amount_cents=999,
            status=ChannelContentTip.Status.COMPLETED,
        )
        old_tip.created_at = timezone.now() - timezone.timedelta(days=60)
        old_tip.save(update_fields=["created_at"])
        self.client.force_authenticate(self.owner)

        res_90d = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/revenue/?period=90d")
        res_7d = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/revenue/?period=7d")

        self.assertEqual(res_90d.data["super_thanks_amount"], 500 + 999)
        self.assertEqual(res_7d.data["super_thanks_amount"], 500)

    def test_non_manager_cannot_view_revenue(self):
        self.client.force_authenticate(self.tipper)

        res = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/revenue/")

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)


class ChannelRevenueAnalyticsLiveTipsTests(TestCase):
    """F4: super_chat_amount/count (live-stream tips) were already
    aggregated separately from super_thanks_amount/count (VOD content
    tips) in the view itself, but no test ever exercised that - this
    file's existing coverage only ever created ChannelContentTip rows, so
    a regression in the live-tip branch (e.g. the wrong FK, the wrong
    status filter) could ship unnoticed. These prove both sources are
    kept distinct AND correctly summed into total_estimated_cents
    together, plus the zero-revenue honesty case (0, not missing/null)."""

    def setUp(self):
        self.owner = User.objects.create_user(phone="+2349170000010", password="pw123456", country="NG")
        self.tipper = User.objects.create_user(phone="+2349170000011", password="pw123456", country="NG")
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.owner.id, owner_user=self.owner,
            handle="live-revenue-channel", display_name="Live Revenue Channel", is_public=True,
        )
        BroadcastChannelRole.objects.create(channel=self.channel, user=self.owner, role=BroadcastChannelRole.Role.OWNER)
        self.live = ChannelLiveStream.objects.create(channel=self.channel, title="Revenue stream")
        self.client = APIClient()

    def test_super_chat_is_counted_separately_from_super_thanks_and_both_sum_into_total(self):
        content = ChannelContent.objects.create(
            channel=self.channel, content_type="video", title="VOD", created_by=self.owner,
        )
        ChannelContentTip.objects.create(
            content=content, user=self.tipper, amount_cents=500,
            status=ChannelContentTip.Status.COMPLETED,
        )
        ChannelLiveStreamTip.objects.create(
            live_stream=self.live, user=self.tipper, amount_cents=1200,
            status=ChannelLiveStreamTip.Status.COMPLETED,
        )
        # A pending live tip must not count as revenue yet.
        ChannelLiveStreamTip.objects.create(
            live_stream=self.live, user=self.tipper, amount_cents=9999,
            status=ChannelLiveStreamTip.Status.PENDING,
        )
        self.client.force_authenticate(self.owner)

        res = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/revenue/")

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertEqual(res.data["super_thanks_amount"], 500)
        self.assertEqual(res.data["super_chat_amount"], 1200)
        self.assertEqual(res.data["super_chat_count"], 1)
        self.assertEqual(res.data["total_estimated_cents"], 500 + 1200)

    def test_live_tip_outside_the_period_window_is_excluded(self):
        old_tip = ChannelLiveStreamTip.objects.create(
            live_stream=self.live, user=self.tipper, amount_cents=777,
            status=ChannelLiveStreamTip.Status.COMPLETED,
        )
        old_tip.created_at = timezone.now() - timezone.timedelta(days=60)
        old_tip.save(update_fields=["created_at"])
        self.client.force_authenticate(self.owner)

        res_7d = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/revenue/?period=7d")
        res_90d = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/revenue/?period=90d")

        self.assertEqual(res_7d.data["super_chat_amount"], 0)
        self.assertEqual(res_90d.data["super_chat_amount"], 777)

    def test_zero_revenue_channel_reports_real_zeros_not_missing_fields(self):
        """Honesty bar mirrors test_impressions_analytics_honesty.py's
        standard elsewhere in this app: a channel that has genuinely never
        earned anything must see explicit 0s it can trust, not an absent
        key or a null that could be confused with "not yet computed"."""
        self.client.force_authenticate(self.owner)

        res = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/revenue/")

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        for field in (
            "super_thanks_amount", "super_thanks_count", "super_chat_amount", "super_chat_count",
            "memberships_active", "memberships_est_monthly", "ad_impressions", "ad_revenue_cents",
            "total_estimated_cents",
        ):
            self.assertIn(field, res.data)
            self.assertEqual(res.data[field], 0)
