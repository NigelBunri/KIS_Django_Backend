"""
`_viewer_channel_role` was called in 14 places across views.py but was
never defined anywhere in the module - every one of these endpoints
raised a bare NameError (500) on every single call, with zero test
coverage catching it. Found during the P1.3 monetization/ads pass while
testing ChannelRevenueAnalyticsView. Fixed by routing all 14 call sites
through the real, already-correct `_user_channel_role(user, channel)`
helper (note the previous calls also had the argument order backwards:
`_viewer_channel_role(channel, user)`).

This locks in the fix for every affected endpoint that wasn't already
covered by a more specific test file (ad campaigns/slots/impressions are
covered in test_ad_budget_enforcement.py; revenue analytics in
test_revenue_analytics.py).

Also covers a second bug found in the same pass: ChannelContentCopyrightClaimsView.post
had no permission check at all - any authenticated user could file a
claim "as" any channel_id in the URL, impersonating a claimant they don't
manage.

Run:
  python3 manage.py test apps.broadcasts.test_viewer_channel_role_fix --keepdb -v 2
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from .models import (
    BroadcastChannel,
    BroadcastChannelRole,
    ChannelContent,
    ChannelContentCopyrightClaim,
    ChannelContentProduct,
    ChannelLiveStream,
)

User = get_user_model()


class ViewerChannelRoleFixTestBase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(phone="+2349180000001", password="pw123456", country="NG")
        self.outsider = User.objects.create_user(phone="+2349180000002", password="pw123456", country="NG")
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.owner.id, owner_user=self.owner,
            handle="role-fix-channel", display_name="Role Fix Channel", is_public=True,
        )
        BroadcastChannelRole.objects.create(channel=self.channel, user=self.owner, role=BroadcastChannelRole.Role.OWNER)
        self.content = ChannelContent.objects.create(
            channel=self.channel, content_type="video", title="A video", created_by=self.owner,
        )
        self.client = APIClient()


class ProductTagsTests(ViewerChannelRoleFixTestBase):
    def test_owner_can_create_product_tag(self):
        self.client.force_authenticate(self.owner)

        res = self.client.post(
            f"/api/v1/broadcasts/channel-contents/{self.content.id}/products/",
            {"product_id": "sku-1", "product_title": "Mug", "timestamp_seconds": 5},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)

    def test_outsider_cannot_create_product_tag(self):
        self.client.force_authenticate(self.outsider)

        res = self.client.post(
            f"/api/v1/broadcasts/channel-contents/{self.content.id}/products/",
            {"product_id": "sku-1"}, format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_can_delete_product_tag(self):
        tag = ChannelContentProduct.objects.create(content=self.content, product_id="sku-1")
        self.client.force_authenticate(self.owner)

        res = self.client.delete(f"/api/v1/broadcasts/channel-contents/{self.content.id}/products/{tag.id}/")

        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)

    # AND-169: ChannelContentProduct.product_id has no blank=True/default,
    # so the serializer made it required=True - but ProductTagStudio.tsx
    # (the only client that calls this endpoint) tags an arbitrary
    # external product by title/URL and never collects or sends a
    # product_id at all. The test above always passed one explicitly,
    # which is exactly why this never had coverage catching the real
    # client's payload shape failing validation 100% of the time.
    def test_owner_can_create_product_tag_without_a_product_id(self):
        self.client.force_authenticate(self.owner)

        res = self.client.post(
            f"/api/v1/broadcasts/channel-contents/{self.content.id}/products/",
            {
                "product_title": "Mug",
                "product_url": "https://example.com/mug",
                "timestamp_seconds": 5,
            },
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertTrue(res.data.get("product_id"))


class AutoChaptersTests(ViewerChannelRoleFixTestBase):
    def test_owner_can_trigger_auto_chapters(self):
        self.client.force_authenticate(self.owner)

        res = self.client.post(f"/api/v1/broadcasts/channel-contents/{self.content.id}/auto-chapters/")

        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)

    def test_outsider_cannot_trigger_auto_chapters(self):
        self.client.force_authenticate(self.outsider)

        res = self.client.post(f"/api/v1/broadcasts/channel-contents/{self.content.id}/auto-chapters/")

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)


class SimulcastTargetsTests(ViewerChannelRoleFixTestBase):
    def setUp(self):
        super().setUp()
        self.live_stream = ChannelLiveStream.objects.create(channel=self.channel, title="Live")

    def test_owner_can_list_and_create_targets(self):
        self.client.force_authenticate(self.owner)

        list_res = self.client.get(f"/api/v1/broadcasts/live-streams/{self.live_stream.id}/simulcast/")
        create_res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live_stream.id}/simulcast/",
            {"platform": "youtube", "label": "YT", "rtmp_url": "rtmp://a.example.com/live", "stream_key": "k"},
            format="json",
        )

        self.assertEqual(list_res.status_code, status.HTTP_200_OK)
        self.assertEqual(create_res.status_code, status.HTTP_201_CREATED, create_res.data)

    def test_outsider_cannot_list_or_create_targets(self):
        self.client.force_authenticate(self.outsider)

        list_res = self.client.get(f"/api/v1/broadcasts/live-streams/{self.live_stream.id}/simulcast/")
        create_res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live_stream.id}/simulcast/",
            {"platform": "youtube", "label": "YT", "rtmp_url": "rtmp://a.example.com/live"}, format="json",
        )

        self.assertEqual(list_res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(create_res.status_code, status.HTTP_403_FORBIDDEN)


class CopyrightClaimsTests(ViewerChannelRoleFixTestBase):
    def test_owner_can_view_claims_against_their_channel(self):
        self.client.force_authenticate(self.owner)

        res = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/copyright-claims/")

        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_outsider_cannot_view_claims(self):
        self.client.force_authenticate(self.outsider)

        res = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/copyright-claims/")

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_only_a_manager_of_the_claimant_channel_can_file_a_claim(self):
        self.client.force_authenticate(self.outsider)

        res = self.client.post(
            f"/api/v1/broadcasts/channels/{self.channel.id}/copyright-claims/",
            {"content_id": str(self.content.id), "claimant_name": "Fake Claimant"}, format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(ChannelContentCopyrightClaim.objects.filter(content=self.content).exists())

    def test_owner_can_file_a_claim_on_behalf_of_their_own_channel(self):
        self.client.force_authenticate(self.owner)

        res = self.client.post(
            f"/api/v1/broadcasts/channels/{self.channel.id}/copyright-claims/",
            {"content_id": str(self.content.id), "claimant_name": "Legit Claimant"}, format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)

    def test_owner_can_dispute_a_claim_against_their_content(self):
        claim = ChannelContentCopyrightClaim.objects.create(content=self.content, claimant_name="Someone")
        self.client.force_authenticate(self.owner)

        res = self.client.post(f"/api/v1/broadcasts/copyright-claims/{claim.id}/dispute/", {"reason": "Fair use"}, format="json")

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)

    def test_outsider_cannot_dispute_a_claim(self):
        claim = ChannelContentCopyrightClaim.objects.create(content=self.content, claimant_name="Someone")
        self.client.force_authenticate(self.outsider)

        res = self.client.post(f"/api/v1/broadcasts/copyright-claims/{claim.id}/dispute/", {}, format="json")

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)


class AudienceDemographicsTests(ViewerChannelRoleFixTestBase):
    def test_owner_can_view_demographics(self):
        self.client.force_authenticate(self.owner)

        res = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/demographics/")

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)

    def test_outsider_cannot_view_demographics(self):
        self.client.force_authenticate(self.outsider)

        res = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/demographics/")

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_response_field_names_match_the_frontend_contract(self):
        """AudienceDemographicsPanel.tsx reads age_groups/top_countries -
        this endpoint used to send age_buckets/countries instead, so the
        panel always rendered empty regardless of real data."""
        from .models import ChannelContentDemographicSnapshot

        ChannelContentDemographicSnapshot.objects.create(
            channel=self.channel, content=self.content,
            snapshot_date=timezone.now().date(),
            age_bucket=ChannelContentDemographicSnapshot.AgeBucket.AGE_25_34,
            country_code="NG", view_count=10,
        )
        self.client.force_authenticate(self.owner)

        res = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/demographics/")

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertIn("age_groups", res.data)
        self.assertIn("top_countries", res.data)
        self.assertEqual(res.data["age_groups"][0]["view_count"], 10)
        self.assertEqual(res.data["top_countries"][0]["country_code"], "NG")
