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
