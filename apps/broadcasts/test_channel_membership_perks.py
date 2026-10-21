"""
Member-only-content gating and the paid-member badge in live chat/comments
had zero enforcement anywhere despite the full join/payment/gift flow being
real - a paying member and a free viewer were indistinguishable everywhere.
Closed by: adding ChannelContent.Visibility.MEMBERS_ONLY and gating it in
_user_can_view_content (the same central check every content-interaction
endpoint already calls) plus the content list endpoint, and adding
is_member to the live chat history/send responses and the comment
serializer.

Run:
  python3 manage.py test apps.broadcasts.test_channel_membership_perks --keepdb -v 2
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
    ChannelContentComment,
    ChannelContentType,
    ChannelLiveStream,
    ChannelMembership,
    ChannelMembershipTier,
    ContentModerationStatus,
)

User = get_user_model()


class MembersOnlyContentTestBase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(phone="+2349200000001", password="pw123456", country="NG")
        self.member = User.objects.create_user(phone="+2349200000002", password="pw123456", country="NG")
        self.outsider = User.objects.create_user(phone="+2349200000003", password="pw123456", country="NG")
        self.channel = BroadcastChannel.objects.create(
            owner_type=BroadcastChannel.OwnerType.USER, owner_id=self.owner.id, owner_user=self.owner,
            handle="perks-channel", display_name="Perks Channel", is_public=True,
        )
        BroadcastChannelRole.objects.create(channel=self.channel, user=self.owner, role=BroadcastChannelRole.Role.OWNER)
        self.tier = ChannelMembershipTier.objects.create(channel=self.channel, title="Gold", price_cents=500)
        ChannelMembership.objects.create(user=self.member, tier=self.tier, status=ChannelMembership.Status.ACTIVE)

        self.members_only_content = ChannelContent.objects.create(
            channel=self.channel, content_type=ChannelContentType.VIDEO, title="Members-only video",
            status=ChannelContent.Status.PUBLISHED, visibility=ChannelContent.Visibility.MEMBERS_ONLY,
            published_at=timezone.now(), created_by=self.owner,
            moderation_status=ContentModerationStatus.PASSED,
            moderation_passed_at=timezone.now(),
            moderation_expires_at=timezone.now() + datetime.timedelta(days=90),
        )
        self.client = APIClient()


class MembersOnlyContentDetailTests(MembersOnlyContentTestBase):
    def test_active_member_can_view(self):
        self.client.force_authenticate(self.member)
        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.members_only_content.id}/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_non_member_cannot_view(self):
        self.client.force_authenticate(self.outsider)
        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.members_only_content.id}/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_anonymous_cannot_view(self):
        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.members_only_content.id}/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_channel_owner_can_always_view_their_own(self):
        self.client.force_authenticate(self.owner)
        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.members_only_content.id}/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_cancelled_membership_loses_access(self):
        ChannelMembership.objects.filter(user=self.member, tier=self.tier).update(
            status=ChannelMembership.Status.CANCELLED,
        )
        self.client.force_authenticate(self.member)
        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.members_only_content.id}/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)


class MembersOnlyContentListTests(MembersOnlyContentTestBase):
    def test_member_sees_members_only_content_in_channel_list(self):
        self.client.force_authenticate(self.member)
        res = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/contents/")
        ids = {row["id"] for row in res.data.get("results", [])}
        self.assertIn(str(self.members_only_content.id), ids)

    def test_non_member_does_not_see_members_only_content_in_channel_list(self):
        self.client.force_authenticate(self.outsider)
        res = self.client.get(f"/api/v1/broadcasts/channels/{self.channel.id}/contents/")
        ids = {row["id"] for row in res.data.get("results", [])}
        self.assertNotIn(str(self.members_only_content.id), ids)


class CommentMemberBadgeTests(MembersOnlyContentTestBase):
    def setUp(self):
        super().setUp()
        self.public_content = ChannelContent.objects.create(
            channel=self.channel, content_type=ChannelContentType.VIDEO, title="Public video",
            status=ChannelContent.Status.PUBLISHED, visibility=ChannelContent.Visibility.PUBLIC,
            published_at=timezone.now(), created_by=self.owner,
            moderation_status=ContentModerationStatus.PASSED,
            moderation_passed_at=timezone.now(),
            moderation_expires_at=timezone.now() + datetime.timedelta(days=90),
        )

    def test_member_comment_is_flagged_is_member(self):
        ChannelContentComment.objects.create(content=self.public_content, user=self.member, body="Hi from a member")
        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.public_content.id}/comments/")
        row = next(r for r in res.data["results"] if str(r["user"]) == str(self.member.id))
        self.assertTrue(row["is_member"])

    def test_non_member_comment_is_not_flagged(self):
        ChannelContentComment.objects.create(content=self.public_content, user=self.outsider, body="Hi from an outsider")
        res = self.client.get(f"/api/v1/broadcasts/channel-contents/{self.public_content.id}/comments/")
        row = next(r for r in res.data["results"] if str(r["user"]) == str(self.outsider.id))
        self.assertFalse(row["is_member"])


class LiveChatMemberBadgeTests(MembersOnlyContentTestBase):
    def setUp(self):
        super().setUp()
        self.live = ChannelLiveStream.objects.create(
            channel=self.channel, title="Members test stream", status=ChannelLiveStream.Status.LIVE,
        )

    def test_send_response_flags_member(self):
        self.client.force_authenticate(self.member)
        res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live.id}/chat/", {"message": "hi"}, format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertTrue(res.data["is_member"])

    def test_send_response_does_not_flag_non_member(self):
        self.client.force_authenticate(self.outsider)
        res = self.client.post(
            f"/api/v1/broadcasts/live-streams/{self.live.id}/chat/", {"message": "hi"}, format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertFalse(res.data["is_member"])

    def test_history_flags_member_messages(self):
        self.client.force_authenticate(self.member)
        self.client.post(f"/api/v1/broadcasts/live-streams/{self.live.id}/chat/", {"message": "hi"}, format="json")

        history = self.client.get(f"/api/v1/broadcasts/live-streams/{self.live.id}/chat/")
        self.assertEqual(history.data["results"][0]["is_member"], True)
