"""
P1.5 analytics parity pass: ChannelAnalyticsDailyRollup.impressions has no
writer anywhere in this codebase - a real impressions metric needs a
client-reported "this card was actually shown" event (the same
architecture ChannelAdImpression already uses for ads), which is a
genuinely separate feature, OUT OF CURRENT PRODUCT SCOPE for this pass.
ChannelAnalyticsImpressionsCTRView used to return a bare `ctr_percent: 0.0`
whenever total_impressions was 0 - indistinguishable from "a real
measured 0% click-through rate" and actively misleading to a creator.
Fixed to return `ctr_percent: null` + `has_impression_data: false` so a
client can honestly render "no data yet" instead of a confident lie.

Run:
  python3 manage.py test apps.broadcasts.test_impressions_analytics_honesty --keepdb -v 2
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from .models import (
    BroadcastChannel,
    BroadcastChannelRole,
    ChannelAnalyticsDailyRollup,
    ChannelContent,
)

User = get_user_model()


class ImpressionsAnalyticsHonestyTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(phone="+2349210000001", password="pw123456", country="NG")
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.owner.id, owner_user=self.owner,
            handle="impressions-channel", display_name="Impressions Channel", is_public=True,
        )
        BroadcastChannelRole.objects.create(channel=self.channel, user=self.owner, role=BroadcastChannelRole.Role.OWNER)
        self.content = ChannelContent.objects.create(
            channel=self.channel, content_type="video", title="A video", created_by=self.owner,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def test_ctr_is_null_not_zero_when_no_impression_data_exists(self):
        res = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/analytics/impressions/")

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertIsNone(res.data["ctr_percent"])
        self.assertFalse(res.data["has_impression_data"])

    def test_per_content_ctr_is_null_when_that_rows_impressions_are_zero(self):
        ChannelAnalyticsDailyRollup.objects.create(
            channel=self.channel, content=self.content, date=timezone.now().date(),
            views=5, impressions=0,
        )

        res = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/analytics/impressions/")

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        row = res.data["by_content"][0]
        self.assertIsNone(row["ctr_percent"])

    def test_ctr_is_a_real_number_once_impressions_exist(self):
        ChannelAnalyticsDailyRollup.objects.create(
            channel=self.channel, content=self.content, date=timezone.now().date(),
            views=5, impressions=20,
        )

        res = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/analytics/impressions/")

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertTrue(res.data["has_impression_data"])
        self.assertEqual(res.data["ctr_percent"], 25.0)
        self.assertEqual(res.data["by_content"][0]["ctr_percent"], 25.0)
