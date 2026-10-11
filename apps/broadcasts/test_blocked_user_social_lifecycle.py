"""
P1.2 social-lifecycle gap: apps.moderation.UserBlock existed and was
already enforced in the legacy BroadcastFeedView, but was never checked
anywhere in the real ChannelContent platform - a user a creator blocked
could still comment on, react to, and subscribe to that creator's channel,
and that creator's content kept surfacing in the blocked user's cross-
channel recommendations feed. _channel_blocks_user() closes this,
symmetrically (either direction blocks the interaction), scoped to
owner_type=USER channels only (an org-owned channel has no single
accountable individual to block against).

Run:
  python3 manage.py test apps.broadcasts.test_blocked_user_social_lifecycle --keepdb -v 2
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.moderation.models import UserBlock

from .models import (
    BroadcastChannel,
    BroadcastChannelSubscription,
    ChannelContent,
    ChannelContentType,
    ContentModerationStatus,
)

User = get_user_model()


class BlockedUserSocialLifecycleTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(phone="+2349150000001", password="pw123456", country="NG")
        self.blocked_viewer = User.objects.create_user(phone="+2349150000002", password="pw123456", country="NG")
        self.regular_viewer = User.objects.create_user(phone="+2349150000003", password="pw123456", country="NG")
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.owner.id, owner_user=self.owner,
            handle="blocking-channel", display_name="Blocking Channel", is_public=True,
        )
        self.content = ChannelContent.objects.create(
            channel=self.channel, content_type=ChannelContentType.VIDEO, title="A video",
            status=ChannelContent.Status.PUBLISHED, visibility=ChannelContent.Visibility.PUBLIC,
            published_at=timezone.now(), created_by=self.owner,
            moderation_status=ContentModerationStatus.PASSED,
            moderation_passed_at=timezone.now(),
            moderation_expires_at=timezone.now() + datetime.timedelta(days=90),
        )
        UserBlock.objects.create(blocker=self.owner, blocked=self.blocked_viewer)
        self.client = APIClient()

    def test_blocked_viewer_cannot_comment(self):
        self.client.force_authenticate(self.blocked_viewer)

        res = self.client.post(
            f"/api/v1/broadcasts/channel-contents/{self.content.id}/comments/", {"body": "hi"}, format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_regular_viewer_can_still_comment(self):
        self.client.force_authenticate(self.regular_viewer)

        res = self.client.post(
            f"/api/v1/broadcasts/channel-contents/{self.content.id}/comments/", {"body": "hi"}, format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)

    def test_blocked_viewer_cannot_react(self):
        self.client.force_authenticate(self.blocked_viewer)

        res = self.client.post(f"/api/v1/broadcasts/channel-contents/{self.content.id}/react/", {"reaction": "like"}, format="json")

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_blocked_viewer_cannot_subscribe(self):
        self.client.force_authenticate(self.blocked_viewer)

        res = self.client.post(f"/api/v1/broadcasts/channels/{self.channel.id}/subscribe/")

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(BroadcastChannelSubscription.objects.filter(channel=self.channel, user=self.blocked_viewer).exists())

    def test_block_is_symmetric_viewer_blocking_owner_also_counts(self):
        """The viewer who blocked the owner (rather than the other way
        around) should be equally prevented - this is about not forcing
        either party into contact, not just punishing whoever blocked
        first."""
        UserBlock.objects.create(blocker=self.regular_viewer, blocked=self.owner)
        self.client.force_authenticate(self.regular_viewer)

        res = self.client.post(
            f"/api/v1/broadcasts/channel-contents/{self.content.id}/comments/", {"body": "hi"}, format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_blocked_relationship_excludes_content_from_recommendations(self):
        BroadcastChannelSubscription.objects.create(channel=self.channel, user=self.regular_viewer)
        self.client.force_authenticate(self.blocked_viewer)

        res = self.client.get("/api/v1/broadcasts/recommendations/")

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        ids = {row["id"] for row in res.data["results"]}
        self.assertNotIn(str(self.content.id), ids)

    def test_unrelated_viewer_recommendations_unaffected(self):
        self.client.force_authenticate(self.regular_viewer)

        res = self.client.get("/api/v1/broadcasts/recommendations/")

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        ids = {row["id"] for row in res.data["results"]}
        self.assertIn(str(self.content.id), ids)

    def test_org_owned_channel_block_check_is_a_no_op(self):
        """owner_type != USER has no owner_user_id to block against -
        _channel_blocks_user must return False, not raise, for e.g. a
        shop-owned channel."""
        org_channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.SHOP, owner_id=self.owner.id, owner_user=None,
            handle="org-channel", display_name="Org Channel", is_public=True,
        )
        org_content = ChannelContent.objects.create(
            channel=org_channel, content_type=ChannelContentType.VIDEO, title="Org video",
            status=ChannelContent.Status.PUBLISHED, visibility=ChannelContent.Visibility.PUBLIC,
            published_at=timezone.now(), created_by=self.owner,
            moderation_status=ContentModerationStatus.PASSED,
            moderation_passed_at=timezone.now(),
            moderation_expires_at=timezone.now() + datetime.timedelta(days=90),
        )
        self.client.force_authenticate(self.blocked_viewer)

        res = self.client.post(
            f"/api/v1/broadcasts/channel-contents/{org_content.id}/comments/", {"body": "hi"}, format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
