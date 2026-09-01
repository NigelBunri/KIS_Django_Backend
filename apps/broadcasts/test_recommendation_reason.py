"""
ChannelContentRecommendationsView's "why am I seeing this?" explanation -
this is the actual engagement-optimizing ranking algorithm in the app
(subscription bonus + watch-percentage + reaction/comment engagement +
recency), unlike the plain reverse-chronological main feed
(BroadcastFeedView) or the already-explained social_recommendations.py.
Previously the computed score existed only to sort candidates, then was
discarded before the response was built - a user had no way to know a
ranking algorithm was involved at all, let alone why one item outranked
another.

Run:
  python3 manage.py test apps.broadcasts.test_recommendation_reason --keepdb -v 2
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    BroadcastChannel,
    BroadcastChannelSubscription,
    ChannelContent,
    ChannelContentComment,
    ChannelContentReaction,
    ChannelContentType,
)

User = get_user_model()

URL = "/api/v1/broadcasts/recommendations/"


class RecommendationReasonTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.viewer = User.objects.create_user(phone="+2349100000001", password="pw123456", country="NG")
        self.owner = User.objects.create_user(phone="+2349100000002", password="pw123456", country="NG")
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER,
            owner_id=self.owner.id,
            owner_user=self.owner,
            handle="rec-channel",
            display_name="Rec Channel",
            is_public=True,
        )
        self.client.force_authenticate(self.viewer)

    def _make_content(self, title, **overrides):
        defaults = dict(
            channel=self.channel,
            content_type=ChannelContentType.VIDEO,
            title=title,
            status=ChannelContent.Status.PUBLISHED,
            visibility=ChannelContent.Visibility.PUBLIC,
            published_at=timezone.now(),
            created_by=self.owner,
        )
        defaults.update(overrides)
        return ChannelContent.objects.create(**defaults)

    def test_subscribed_channel_content_is_explained_as_such(self):
        BroadcastChannelSubscription.objects.create(channel=self.channel, user=self.viewer)
        self._make_content("Subscribed content")

        res = self.client.get(URL)

        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data["results"]), 1)
        self.assertEqual(res.data["results"][0]["recommendation_reason"], "From a channel you're subscribed to")

    def test_recently_published_unsubscribed_content_is_explained_as_recent(self):
        self._make_content("Fresh content")

        res = self.client.get(URL)

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["results"][0]["recommendation_reason"], "Recently published")

    def test_highly_engaged_content_is_explained_by_engagement(self):
        # 60 days old (recency_score = max(0, 3 - 60/30) = 1) with 10
        # reactions (engagement_score = log2(11) ~= 3.46) so engagement
        # clearly dominates recency, not just edges it out.
        content = self._make_content("Buzzing content", published_at=timezone.now() - datetime.timedelta(days=60))
        for i in range(10):
            reactor = User.objects.create_user(phone=f"+23491000001{i:02d}", password="pw123456", country="NG")
            ChannelContentReaction.objects.create(content=content, user=reactor, reaction="like")

        res = self.client.get(URL)

        self.assertEqual(res.status_code, 200)
        self.assertIn("Popular right now", res.data["results"][0]["recommendation_reason"])
        self.assertIn("10 reactions", res.data["results"][0]["recommendation_reason"])

    def test_every_result_has_a_reason(self):
        self._make_content("A")
        self._make_content("B")
        BroadcastChannelSubscription.objects.create(channel=self.channel, user=self.viewer)

        res = self.client.get(URL)

        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data["results"]), 2)
        for row in res.data["results"]:
            self.assertTrue(row["recommendation_reason"])

    def test_reason_reflects_the_same_ordering_used_for_ranking(self):
        # The subscribed item must sort ahead of the unsubscribed one (per
        # the scoring function's sub_bonus) AND be labeled accordingly -
        # confirms the reason is computed from the same score components
        # actually driving the sort, not a separate/inconsistent pass.
        BroadcastChannelSubscription.objects.create(channel=self.channel, user=self.viewer)
        self._make_content("Subscribed", published_at=timezone.now() - datetime.timedelta(days=5))
        other_owner = User.objects.create_user(phone="+2349100000003", password="pw123456", country="NG")
        other_channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER,
            owner_id=other_owner.id,
            owner_user=other_owner,
            handle="other-channel",
            display_name="Other Channel",
            is_public=True,
        )
        ChannelContent.objects.create(
            channel=other_channel,
            content_type=ChannelContentType.VIDEO,
            title="Not subscribed",
            status=ChannelContent.Status.PUBLISHED,
            visibility=ChannelContent.Visibility.PUBLIC,
            published_at=timezone.now(),
            created_by=other_owner,
        )

        res = self.client.get(URL)

        self.assertEqual(res.status_code, 200)
        titles_in_order = [row["title"] for row in res.data["results"]]
        self.assertEqual(titles_in_order[0], "Subscribed")
        self.assertEqual(res.data["results"][0]["recommendation_reason"], "From a channel you're subscribed to")
