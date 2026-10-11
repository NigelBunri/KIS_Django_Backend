"""
P1.4 search/discovery pass: BroadcastSearchView, BroadcastSearchSuggestView,
BroadcastTrendingView, and ChannelCategoryBrowseView all filtered on the
creator-controlled status=PUBLISHED/visibility=PUBLIC fields only - none
of them were ever wired into the ChannelContent moderation gate added in
an earlier closure pass, so a never-reviewed or explicitly BLOCKED item
was fully searchable/trending/browsable by the entire public. Fixed by
routing all four through filter_channel_content_broadcast_eligible, the
same gate already used by the content list/detail/recommendations views.

Run:
  python3 manage.py test apps.broadcasts.test_search_discovery_moderation_gate --keepdb -v 2
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from .models import BroadcastChannel, ChannelContent, ChannelContentType, ContentModerationStatus

User = get_user_model()


class SearchDiscoveryModerationGateTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(phone="+2349200000001", password="pw123456", country="NG")
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.owner.id, owner_user=self.owner,
            handle="search-gate-channel", display_name="Search Gate Channel", is_public=True,
            category="worship",
        )
        self.approved = ChannelContent.objects.create(
            channel=self.channel, content_type=ChannelContentType.VIDEO, title="Covenant teaching",
            tags=["covenant"], status=ChannelContent.Status.PUBLISHED, visibility=ChannelContent.Visibility.PUBLIC,
            published_at=timezone.now(), created_by=self.owner,
            moderation_status=ContentModerationStatus.PASSED,
            moderation_passed_at=timezone.now(),
            moderation_expires_at=timezone.now() + datetime.timedelta(days=90),
            stats={"views": 1000, "reactions": 50, "comments": 10, "shares": 5},
        )
        self.unapproved = ChannelContent.objects.create(
            channel=self.channel, content_type=ChannelContentType.VIDEO, title="Covenant unreviewed",
            tags=["covenant"], status=ChannelContent.Status.PUBLISHED, visibility=ChannelContent.Visibility.PUBLIC,
            published_at=timezone.now(), created_by=self.owner,
            # moderation_status defaults to PENDING_REVIEW - never approved.
            stats={"views": 999999, "reactions": 99999, "comments": 99999, "shares": 99999},
        )
        self.client = APIClient()

    def test_search_excludes_unapproved_content(self):
        res = self.client.get("/api/v1/broadcasts/search/?q=covenant")

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        ids = {row["id"] for row in res.data["results"]}
        self.assertIn(str(self.approved.id), ids)
        self.assertNotIn(str(self.unapproved.id), ids)

    def test_suggest_excludes_unapproved_content(self):
        res = self.client.get("/api/v1/broadcasts/search/suggest/?q=Covenant")

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        titles = {row["title"] for row in res.data["contents"]}
        self.assertIn("Covenant teaching", titles)
        self.assertNotIn("Covenant unreviewed", titles)

    def test_trending_excludes_unapproved_content_despite_dominant_stats(self):
        """The unapproved item has wildly higher engagement stats - if it
        shows up anyway, the gate isn't actually filtering before ranking."""
        res = self.client.get("/api/v1/broadcasts/trending/")

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        ids = {row["id"] for row in res.data["results"]}
        self.assertIn(str(self.approved.id), ids)
        self.assertNotIn(str(self.unapproved.id), ids)

    def test_category_browse_excludes_unapproved_content(self):
        res = self.client.get("/api/v1/broadcasts/categories/worship/browse/")

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        ids = {row["id"] for row in res.data["results"]}
        self.assertIn(str(self.approved.id), ids)
        self.assertNotIn(str(self.unapproved.id), ids)
