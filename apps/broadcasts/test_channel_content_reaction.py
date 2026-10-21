"""
Like/dislike on channel content had no way for the mobile Like/Dislike
buttons to know the viewer's existing reaction - ChannelContentDetailSerializer
exposed no such field, so the UI could only ever blindly POST another
reaction on every tap (no toggle-off, no "already liked" highlight) even
though the backend has always supported both a real toggle (DELETE removes
a reaction) and type-switching (POST upserts via
ChannelContentReaction.unique_together = [("content", "user")]). Added
ChannelContentDetailSerializer.my_reaction to close the gap this test
covers; the toggle/switch semantics on the view itself were already
correct and are locked down here too since they had zero prior coverage.

Run:
  python3 manage.py test apps.broadcasts.test_channel_content_reaction --keepdb -v 2
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from .models import (
    BroadcastChannel,
    BroadcastChannelRole,
    ChannelContent,
    ChannelContentReaction,
    ChannelContentType,
    ContentModerationStatus,
)

User = get_user_model()


class ChannelContentReactionTestBase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(phone="+2349210000001", password="pw123456", country="NG")
        self.viewer = User.objects.create_user(phone="+2349210000002", password="pw123456", country="NG")
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.owner.id, owner_user=self.owner,
            handle="reaction-channel", display_name="Reaction Channel", is_public=True,
        )
        BroadcastChannelRole.objects.create(channel=self.channel, user=self.owner, role=BroadcastChannelRole.Role.OWNER)
        self.content = ChannelContent.objects.create(
            channel=self.channel, content_type=ChannelContentType.VIDEO, title="Reaction video",
            status=ChannelContent.Status.PUBLISHED, visibility=ChannelContent.Visibility.PUBLIC,
            published_at=timezone.now(), created_by=self.owner,
            moderation_status=ContentModerationStatus.PASSED,
            moderation_passed_at=timezone.now(),
            moderation_expires_at=timezone.now() + datetime.timedelta(days=90),
        )
        self.client = APIClient()
        self.client.force_authenticate(self.viewer)


class ReactionToggleTests(ChannelContentReactionTestBase):
    def test_liking_twice_does_not_create_a_duplicate_row(self):
        url = f"/api/v1/broadcasts/channel-contents/{self.content.id}/react/"
        self.client.post(url, {"reaction": "like"}, format="json")
        self.client.post(url, {"reaction": "like"}, format="json")

        self.assertEqual(ChannelContentReaction.objects.filter(content=self.content, user=self.viewer).count(), 1)

    def test_disliking_after_liking_switches_the_same_row(self):
        url = f"/api/v1/broadcasts/channel-contents/{self.content.id}/react/"
        self.client.post(url, {"reaction": "like"}, format="json")
        res = self.client.post(url, {"reaction": "dislike"}, format="json")

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        reaction = ChannelContentReaction.objects.get(content=self.content, user=self.viewer)
        self.assertEqual(reaction.reaction, "dislike")
        self.assertEqual(ChannelContentReaction.objects.filter(content=self.content, user=self.viewer).count(), 1)

    def test_delete_removes_the_reaction(self):
        url = f"/api/v1/broadcasts/channel-contents/{self.content.id}/react/"
        self.client.post(url, {"reaction": "like"}, format="json")

        res = self.client.delete(url)

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertFalse(ChannelContentReaction.objects.filter(content=self.content, user=self.viewer).exists())


class MyReactionFieldTests(ChannelContentReactionTestBase):
    def test_detail_reflects_no_reaction_by_default(self):
        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.content.id}/")
        self.assertIsNone(res.data["my_reaction"])

    def test_detail_reflects_existing_like(self):
        ChannelContentReaction.objects.create(content=self.content, user=self.viewer, reaction="like")

        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.content.id}/")

        self.assertEqual(res.data["my_reaction"], "like")

    def test_anonymous_viewer_gets_no_reaction(self):
        self.client.force_authenticate(None)

        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.content.id}/")

        self.assertIsNone(res.data["my_reaction"])
