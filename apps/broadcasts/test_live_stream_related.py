"""
ChannelLiveStreamRelatedView - the related-content feed shown below the
player on LiveWatchPage.tsx. Ranks primarily by topic relevance (tag +
title/description keyword overlap), with same-channel/category/freshness/
engagement/currently-live as tie-breakers, and a per-channel diversity cap.

Run:
  python3 manage.py test apps.broadcasts.test_live_stream_related --keepdb -v 2
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    BroadcastChannel,
    ChannelContent,
    ChannelContentType,
    ChannelLiveStream,
    ContentModerationStatus,
)

User = get_user_model()


class LiveStreamRelatedTestBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(phone="+2349200000001", password="pw123456", country="NG")
        self.other_owner = User.objects.create_user(phone="+2349200000002", password="pw123456", country="NG")
        self.viewer = User.objects.create_user(phone="+2349200000003", password="pw123456", country="NG")
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER,
            owner_id=self.owner.id,
            owner_user=self.owner,
            handle="related-main",
            display_name="Main Channel",
            category="worship",
            is_public=True,
        )

    def _make_content(self, channel, title, **overrides):
        defaults = dict(
            channel=channel,
            content_type=ChannelContentType.VIDEO,
            title=title,
            description="",
            tags=[],
            status=ChannelContent.Status.PUBLISHED,
            visibility=ChannelContent.Visibility.PUBLIC,
            published_at=timezone.now(),
            moderation_status=ContentModerationStatus.PASSED,
            moderation_passed_at=timezone.now(),
            moderation_expires_at=timezone.now() + datetime.timedelta(days=90),
        )
        defaults.update(overrides)
        return ChannelContent.objects.create(**defaults)

    def _make_live_stream(self, channel, title, content=None, status=ChannelLiveStream.Status.ENDED, **overrides):
        defaults = dict(channel=channel, title=title, status=status, content=content)
        defaults.update(overrides)
        return ChannelLiveStream.objects.create(**defaults)

    def url(self, stream_id):
        return f"/api/v1/broadcasts/live-streams/{stream_id}/related/"


class TopicRankingTests(LiveStreamRelatedTestBase):
    def test_tag_overlap_outranks_unrelated_popular_content(self):
        source_content = self._make_content(self.channel, "Sunday Worship Night", tags=["worship", "prayer", "revival"])
        source_stream = self._make_live_stream(self.channel, "Sunday Worship Night", content=source_content)

        related = self._make_content(
            self.other_owner_channel(), "Midweek Worship & Prayer", tags=["worship", "prayer"],
        )
        self._make_live_stream(related.channel, "Midweek Worship & Prayer", content=related)

        unrelated_popular = self._make_content(
            self.other_owner_channel(handle="unrelated", category="comedy"), "Market Day Comedy Skit", tags=["comedy"],
            stats={"views": 100000, "reactions": 5000, "comments": 2000},
        )
        self._make_live_stream(unrelated_popular.channel, "Market Day Comedy Skit", content=unrelated_popular)

        res = self.client.get(self.url(source_stream.id))

        self.assertEqual(res.status_code, 200)
        titles = [row["title"] for row in res.data["results"]]
        self.assertIn("Midweek Worship & Prayer", titles)
        self.assertLess(titles.index("Midweek Worship & Prayer"), titles.index("Market Day Comedy Skit"))

    def other_owner_channel(self, handle="related-other", category="worship"):
        if not hasattr(self, "_other_channels"):
            self._other_channels = {}
        if handle not in self._other_channels:
            self._other_channels[handle] = BroadcastChannel.objects.create(
                owner_type=BroadcastChannel.OwnerType.USER,
                owner_id=self.other_owner.id,
                owner_user=self.other_owner,
                handle=handle,
                display_name=handle,
                category=category,
                is_public=True,
            )
        return self._other_channels[handle]

    def test_keyword_fallback_when_no_tags_present(self):
        source_content = self._make_content(self.channel, "Healing and Deliverance Service", tags=[])
        source_stream = self._make_live_stream(self.channel, "Healing and Deliverance Service", content=source_content)

        related_channel = self.other_owner_channel()
        related = self._make_content(related_channel, "Deliverance and Healing Crusade", tags=[])
        self._make_live_stream(related_channel, "Deliverance and Healing Crusade", content=related)

        unrelated = self._make_content(related_channel, "Cooking Tips For Beginners", tags=[])
        self._make_live_stream(related_channel, "Cooking Tips For Beginners", content=unrelated)

        res = self.client.get(self.url(source_stream.id))

        titles = [row["title"] for row in res.data["results"]]
        self.assertIn("Deliverance and Healing Crusade", titles)
        self.assertLess(titles.index("Deliverance and Healing Crusade"), titles.index("Cooking Tips For Beginners"))


class ExclusionTests(LiveStreamRelatedTestBase):
    def test_excludes_the_current_stream_itself(self):
        content = self._make_content(self.channel, "Current Stream")
        stream = self._make_live_stream(self.channel, "Current Stream", content=content)

        res = self.client.get(self.url(stream.id))

        ids = [row["id"] for row in res.data["results"]]
        self.assertNotIn(str(content.id), ids)

    def test_excludes_archived_replay_of_the_same_stream(self):
        content = self._make_content(self.channel, "Current Stream")
        stream = self._make_live_stream(self.channel, "Current Stream", content=content)
        replay = self._make_content(
            self.channel, "Current Stream (Replay)",
            metadata={"archived_from_stream": str(stream.id)},
        )

        res = self.client.get(self.url(stream.id))

        ids = [row["id"] for row in res.data["results"]]
        self.assertNotIn(str(replay.id), ids)

    def test_excludes_deleted_content(self):
        content = self._make_content(self.channel, "Current Stream")
        stream = self._make_live_stream(self.channel, "Current Stream", content=content)
        deleted = self._make_content(self.channel, "Deleted One", is_deleted=True)

        res = self.client.get(self.url(stream.id))

        ids = [row["id"] for row in res.data["results"]]
        self.assertNotIn(str(deleted.id), ids)

    def test_excludes_private_visibility_content(self):
        content = self._make_content(self.channel, "Current Stream")
        stream = self._make_live_stream(self.channel, "Current Stream", content=content)
        private = self._make_content(self.channel, "Private One", visibility=ChannelContent.Visibility.PRIVATE)

        res = self.client.get(self.url(stream.id))

        ids = [row["id"] for row in res.data["results"]]
        self.assertNotIn(str(private.id), ids)

    def test_excludes_content_from_private_channel(self):
        content = self._make_content(self.channel, "Current Stream")
        stream = self._make_live_stream(self.channel, "Current Stream", content=content)
        private_channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER,
            owner_id=self.other_owner.id,
            owner_user=self.other_owner,
            handle="private-channel",
            display_name="Private Channel",
            is_public=False,
        )
        hidden = self._make_content(private_channel, "Hidden One")

        res = self.client.get(self.url(stream.id))

        ids = [row["id"] for row in res.data["results"]]
        self.assertNotIn(str(hidden.id), ids)

    def test_excludes_unmoderated_content(self):
        content = self._make_content(self.channel, "Current Stream")
        stream = self._make_live_stream(self.channel, "Current Stream", content=content)
        unmoderated = self._make_content(
            self.channel, "Pending Review One",
            moderation_status=ContentModerationStatus.PENDING_REVIEW,
            moderation_passed_at=None,
            moderation_expires_at=None,
        )

        res = self.client.get(self.url(stream.id))

        ids = [row["id"] for row in res.data["results"]]
        self.assertNotIn(str(unmoderated.id), ids)


class AccessAndErrorTests(LiveStreamRelatedTestBase):
    def test_anonymous_access_allowed_for_public_stream(self):
        content = self._make_content(self.channel, "Public Stream")
        stream = self._make_live_stream(self.channel, "Public Stream", content=content)

        res = self.client.get(self.url(stream.id))

        self.assertEqual(res.status_code, 200)

    def test_authenticated_access_allowed(self):
        content = self._make_content(self.channel, "Public Stream")
        stream = self._make_live_stream(self.channel, "Public Stream", content=content)
        self.client.force_authenticate(self.viewer)

        res = self.client.get(self.url(stream.id))

        self.assertEqual(res.status_code, 200)

    def test_private_channel_stream_404s_for_non_manager(self):
        private_channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER,
            owner_id=self.other_owner.id,
            owner_user=self.other_owner,
            handle="private-stream-channel",
            display_name="Private Stream Channel",
            is_public=False,
        )
        content = self._make_content(private_channel, "Private Stream")
        stream = self._make_live_stream(private_channel, "Private Stream", content=content)
        self.client.force_authenticate(self.viewer)

        res = self.client.get(self.url(stream.id))

        self.assertEqual(res.status_code, 404)

    def test_invalid_stream_id_404s(self):
        res = self.client.get("/api/v1/broadcasts/live-streams/00000000-0000-0000-0000-000000000000/related/")

        self.assertEqual(res.status_code, 404)

    def test_empty_result_set_when_nothing_eligible(self):
        content = self._make_content(self.channel, "Lonely Stream")
        stream = self._make_live_stream(self.channel, "Lonely Stream", content=content)

        res = self.client.get(self.url(stream.id))

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["results"], [])
        self.assertFalse(res.data["has_more"])


class PaginationAndDiversityTests(LiveStreamRelatedTestBase):
    def test_pagination_is_stable_and_non_overlapping(self):
        content = self._make_content(self.channel, "Anchor Stream", tags=["music"])
        stream = self._make_live_stream(self.channel, "Anchor Stream", content=content)
        # Spread across distinct channels (not just one) so the per-channel
        # diversity cap doesn't interfere with what this test is actually
        # checking - stable, non-overlapping pagination.
        for i in range(10):
            other_channel = BroadcastChannel.objects.create(
                owner_type=BroadcastChannel.OwnerType.USER,
                owner_id=self.other_owner.id,
                owner_user=self.other_owner,
                handle=f"pagination-channel-{i}",
                display_name=f"Pagination Channel {i}",
                is_public=True,
            )
            self._make_content(other_channel, f"Music Video {i}", tags=["music"])

        first_page = self.client.get(self.url(stream.id), {"limit": 4})
        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(len(first_page.data["results"]), 4)
        cursor = first_page.data["cursor"]
        self.assertIsNotNone(cursor)

        second_page = self.client.get(self.url(stream.id), {"limit": 4, "cursor": cursor})
        self.assertEqual(second_page.status_code, 200)
        first_ids = {row["id"] for row in first_page.data["results"]}
        second_ids = {row["id"] for row in second_page.data["results"]}
        self.assertEqual(first_ids & second_ids, set())

    def test_diversity_cap_limits_single_channel_dominance(self):
        content = self._make_content(self.channel, "Anchor Stream", tags=["music"])
        stream = self._make_live_stream(self.channel, "Anchor Stream", content=content)
        dominant_channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER,
            owner_id=self.other_owner.id,
            owner_user=self.other_owner,
            handle="dominant-channel",
            display_name="Dominant Channel",
            is_public=True,
        )
        for i in range(8):
            self._make_content(dominant_channel, f"Dominant Music Video {i}", tags=["music"])
        # Enough other channels/content that the diversity cap (3 per
        # channel) can fill a 5-item page without needing to relax it -
        # otherwise a thin catalog would make this test ambiguous about
        # whether the cap is actually being enforced.
        for i in range(3):
            minor_channel = BroadcastChannel.objects.create(
                owner_type=BroadcastChannel.OwnerType.USER,
                owner_id=self.viewer.id,
                owner_user=self.viewer,
                handle=f"minor-channel-{i}",
                display_name=f"Minor Channel {i}",
                is_public=True,
            )
            self._make_content(minor_channel, f"Minor Music Video {i}", tags=["music"])

        res = self.client.get(self.url(stream.id), {"limit": 5})

        dominant_count = sum(
            1 for row in res.data["results"] if row["channel"]["handle"] == "dominant-channel"
        )
        self.assertLessEqual(dominant_count, 3)


class LiveBoostTests(LiveStreamRelatedTestBase):
    def test_currently_live_candidate_gets_live_now_section(self):
        content = self._make_content(self.channel, "Anchor Stream", tags=["music"])
        stream = self._make_live_stream(self.channel, "Anchor Stream", content=content)
        other_channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER,
            owner_id=self.other_owner.id,
            owner_user=self.other_owner,
            handle="live-now-channel",
            display_name="Live Now Channel",
            is_public=True,
        )
        live_content = self._make_content(other_channel, "Currently Live Music", tags=["music"])
        self._make_live_stream(
            other_channel, "Currently Live Music", content=live_content,
            status=ChannelLiveStream.Status.LIVE,
        )

        res = self.client.get(self.url(stream.id))

        row = next(r for r in res.data["results"] if r["title"] == "Currently Live Music")
        self.assertEqual(row["section"], "live_now")
