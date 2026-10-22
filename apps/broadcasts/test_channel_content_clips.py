"""
ChannelContentClipView (create+list) had zero test coverage despite being
fully implemented and consumed by mobile (ClipsListScreen.tsx) - an
earlier audit pass under-counted this feature as "only delete confirmed"
without grepping thoroughly enough; create/list/detail/share/public views
all already existed. Low priority (weight 1 in the parity scorecard -
YouTube itself restricts clipping to gaming creators with 1k+ followers),
but worth a minimal regression guard given it was genuinely unverified.

Run:
  python3 manage.py test apps.broadcasts.test_channel_content_clips --keepdb -v 2
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from .models import (
    BroadcastChannel,
    ChannelContent,
    ChannelContentClip,
    ChannelContentType,
    ContentModerationStatus,
)

User = get_user_model()


class ChannelContentClipTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(phone="+2349180000001", password="pw123456", country="NG")
        self.other = User.objects.create_user(phone="+2349180000002", password="pw123456", country="NG")
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.owner.id, owner_user=self.owner,
            handle="clip-channel", display_name="Clip Channel", is_public=True,
        )
        self.content = ChannelContent.objects.create(
            channel=self.channel, content_type=ChannelContentType.VIDEO, title="Clippable",
            created_by=self.owner,
            visibility=ChannelContent.Visibility.PUBLIC,
            status=ChannelContent.Status.PUBLISHED,
            moderation_status=ContentModerationStatus.PASSED,
            moderation_expires_at=timezone.now() + timedelta(days=90),
        )
        self.client = APIClient()

    def test_creates_a_clip_within_the_length_limit(self):
        self.client.force_authenticate(self.other)

        res = self.client.post(
            f"/api/v1/broadcasts/channel-contents/{self.content.id}/clip/",
            {"start_seconds": 10, "end_seconds": 40, "title": "Best part"},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        clip = ChannelContentClip.objects.get(id=res.data["id"])
        self.assertEqual(clip.created_by_id, self.other.id)
        self.assertEqual(clip.start_seconds, 10)
        self.assertEqual(clip.end_seconds, 40)
        self.assertEqual(clip.status, "pending")

    def test_rejects_end_before_or_equal_to_start(self):
        self.client.force_authenticate(self.other)

        res = self.client.post(
            f"/api/v1/broadcasts/channel-contents/{self.content.id}/clip/",
            {"start_seconds": 30, "end_seconds": 30},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_a_clip_longer_than_five_minutes(self):
        self.client.force_authenticate(self.other)

        res = self.client.post(
            f"/api/v1/broadcasts/channel-contents/{self.content.id}/clip/",
            {"start_seconds": 0, "end_seconds": 301},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_list_only_shows_the_requesting_users_own_clips(self):
        ChannelContentClip.objects.create(
            content=self.content, created_by=self.other, title="Mine",
            start_seconds=0, end_seconds=20, status="ready",
        )
        ChannelContentClip.objects.create(
            content=self.content, created_by=self.owner, title="Not mine",
            start_seconds=0, end_seconds=20, status="ready",
        )
        self.client.force_authenticate(self.other)

        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.content.id}/clip/")

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        titles = {row["title"] for row in res.data["results"]}
        self.assertEqual(titles, {"Mine"})

    def test_requires_authentication(self):
        res = self.client.post(
            f"/api/v1/broadcasts/channel-contents/{self.content.id}/clip/",
            {"start_seconds": 0, "end_seconds": 10},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
