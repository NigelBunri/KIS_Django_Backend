"""
P1.3 monetization gap: ChannelAdImpressionView charged every impression
against a campaign's spent_cents with no budget cap check at all, and
ChannelAdSlotView served every slot regardless of its campaign's status -
an advertiser's budget_cents was decorative, and a PAUSED/ENDED/DRAFT
campaign's ad kept being served and billed forever. Fixed via
_ad_slot_is_servable(), checked both when listing slots for a non-manager
(the real ad-serving path) and again at impression-record time (closing
the gap between "was servable when the player loaded" and "is still
servable now").

Run:
  python3 manage.py test apps.broadcasts.test_ad_budget_enforcement --keepdb -v 2
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from .models import (
    BroadcastChannel,
    BroadcastChannelRole,
    ChannelAdCampaign,
    ChannelAdSlot,
    ChannelContent,
)

User = get_user_model()


class AdBudgetEnforcementTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(phone="+2349160000001", password="pw123456", country="NG")
        self.viewer = User.objects.create_user(phone="+2349160000002", password="pw123456", country="NG")
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.owner.id, owner_user=self.owner,
            handle="ad-channel", display_name="Ad Channel", is_public=True,
        )
        BroadcastChannelRole.objects.create(channel=self.channel, user=self.owner, role=BroadcastChannelRole.Role.OWNER)
        self.content = ChannelContent.objects.create(
            channel=self.channel, content_type="video", title="Monetized video", created_by=self.owner,
        )
        self.campaign = ChannelAdCampaign.objects.create(
            advertiser_channel=self.channel, title="Launch promo",
            budget_cents=25, status=ChannelAdCampaign.Status.ACTIVE,
        )
        self.slot = ChannelAdSlot.objects.create(content=self.content, campaign=self.campaign)
        self.client = APIClient()

    def _record_impression(self):
        self.client.force_authenticate(self.viewer)
        return self.client.post(f"/api/v1/broadcasts/ad-slots/{self.slot.id}/impression/", {}, format="json")

    def test_impressions_accumulate_spend_up_to_budget(self):
        res1 = self._record_impression()
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED, res1.data)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.spent_cents, 10)
        self.assertEqual(self.campaign.status, ChannelAdCampaign.Status.ACTIVE)

    def test_campaign_auto_ends_once_budget_is_reached(self):
        self._record_impression()
        self._record_impression()  # 20 cents, still under 25

        res3 = self._record_impression()  # 30 cents would exceed 25 -> this one is rejected

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.spent_cents, 20)
        # The impression that would have pushed spend past budget never
        # happened at all - the check runs before charging, not after.
        self.assertEqual(res3.status_code, status.HTTP_400_BAD_REQUEST)

    def test_exhausted_campaign_stops_serving_the_slot(self):
        self.campaign.spent_cents = 25
        self.campaign.status = ChannelAdCampaign.Status.ENDED
        self.campaign.save(update_fields=["spent_cents", "status"])

        res = self._record_impression()

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_paused_campaign_slot_is_excluded_from_viewer_slot_list(self):
        self.campaign.status = ChannelAdCampaign.Status.PAUSED
        self.campaign.save(update_fields=["status"])
        self.client.force_authenticate(self.viewer)

        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.content.id}/ad-slots/")

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data, [])

    def test_manager_still_sees_paused_slot_for_management(self):
        self.campaign.status = ChannelAdCampaign.Status.PAUSED
        self.campaign.save(update_fields=["status"])
        self.client.force_authenticate(self.owner)

        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.content.id}/ad-slots/")

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)

    def test_house_slot_with_no_campaign_always_serves(self):
        # self.campaign (attached to self.slot) is still fresh/active/
        # unexhausted here too, so both it and the house slot should serve.
        house_slot = ChannelAdSlot.objects.create(content=self.content, campaign=None)
        self.client.force_authenticate(self.viewer)

        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.content.id}/ad-slots/")

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        ids = {row["id"] for row in res.data}
        self.assertIn(str(house_slot.id), ids)
        self.assertIn(str(self.slot.id), ids)

    def test_zero_budget_is_treated_as_unbounded_not_zero(self):
        self.campaign.budget_cents = 0
        self.campaign.save(update_fields=["budget_cents"])

        for _ in range(5):
            res = self._record_impression()
            self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, ChannelAdCampaign.Status.ACTIVE)
