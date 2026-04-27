from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chat.models import Conversation, ConversationType
from apps.communities.models import (
    Community,
    CommunityMembership,
    CommunityPost,
    CommunityRole,
)
from apps.communities.serializers import CommunityPostSerializer


class CommunityPostDiscussionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(
            phone="+2348110000001",
            country="NG",
            password="pass1234",
            username="community-owner",
            email="community-owner@example.com",
        )
        self.member = User.objects.create_user(
            phone="+2348110000002",
            country="NG",
            password="pass1234",
            username="community-member",
            email="community-member@example.com",
        )
        self.community = Community.objects.create(
            owner=self.owner,
            name="Community One",
            slug="community-one",
        )
        CommunityMembership.objects.create(
            community=self.community,
            user=self.owner,
            role=CommunityRole.OWNER,
        )
        CommunityMembership.objects.create(
            community=self.community,
            user=self.member,
            role=CommunityRole.MEMBER,
        )

    def test_comment_room_is_reused_and_membership_is_created(self):
        post = CommunityPost.objects.create(
            community=self.community,
            author=self.owner,
            text_plain="Discuss this",
            text_preview="Discuss this",
        )

        self.client.force_authenticate(self.member)
        first = self.client.post(f"/api/v1/communities/posts/{post.id}/comment-room/", {}, format="json")
        self.assertEqual(first.status_code, status.HTTP_200_OK, first.data)
        conversation_id = first.data.get("conversation_id")
        self.assertTrue(conversation_id)

        second = self.client.post(f"/api/v1/communities/posts/{post.id}/comment-room/", {}, format="json")
        self.assertEqual(second.status_code, status.HTTP_200_OK, second.data)
        self.assertEqual(second.data.get("conversation_id"), conversation_id)

        post.refresh_from_db()
        self.assertEqual(str(post.comment_conversation_id), str(conversation_id))
        self.assertTrue(
            post.comment_conversation.memberships.filter(
                user=self.member,
                left_at__isnull=True,
            ).exists()
        )

    def test_serializer_prefers_comment_conversation_sequence_for_count(self):
        discussion = Conversation.objects.create(
            type=ConversationType.POST,
            title="Community comments",
            description="Canonical discussion",
            created_by=self.owner,
            last_message_seq=9,
        )
        post = CommunityPost.objects.create(
            community=self.community,
            author=self.owner,
            text_plain="Count source",
            text_preview="Count source",
            comment_conversation=discussion,
        )

        payload = CommunityPostSerializer(post).data

        self.assertEqual(payload["comments_count"], 9)
