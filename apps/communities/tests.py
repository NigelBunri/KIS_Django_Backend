from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chat.models import Conversation, ConversationMember, ConversationType
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

    def test_direct_comments_and_reactions_return_updated_feed_state(self):
        post = CommunityPost.objects.create(
            community=self.community,
            author=self.owner,
            text_plain="Community interaction",
            text_preview="Community interaction",
        )
        self.client.force_authenticate(self.member)

        comment = self.client.post(
            f"/api/v1/posts/{post.id}/comment/",
            {"text": "Useful post"},
            format="json",
        )
        self.assertEqual(comment.status_code, status.HTTP_201_CREATED, comment.data)
        self.assertEqual(comment.data["text"], "Useful post")

        comments = self.client.get(f"/api/v1/posts/{post.id}/comments/")
        self.assertEqual(comments.status_code, status.HTTP_200_OK, comments.data)
        self.assertEqual(len(comments.data), 1)

        added = self.client.post(
            f"/api/v1/posts/{post.id}/react/",
            {"emoji": "👍", "action": "add"},
            format="json",
        )
        self.assertEqual(added.status_code, status.HTTP_200_OK, added.data)
        self.assertTrue(added.data["has_reacted"])
        self.assertEqual(added.data["reactions_count"], 1)

        removed = self.client.post(
            f"/api/v1/posts/{post.id}/react/",
            {"emoji": "👍", "action": "remove"},
            format="json",
        )
        self.assertEqual(removed.status_code, status.HTTP_200_OK, removed.data)
        self.assertFalse(removed.data["has_reacted"])
        self.assertEqual(removed.data["reactions_count"], 0)


class ChatCommunityCreationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(
            phone="+2348110000099",
            country="NG",
            password="pass1234",
            username="chat-community-owner",
            email="chat-community-owner@example.com",
        )
        self.client.force_authenticate(self.owner)

    def test_chat_community_endpoint_creates_conversations_and_owner_memberships(self):
        response = self.client.post(
            "/api/v1/chat-communities/",
            {
                "name": "Chat Community",
                "slug": "chat-community",
                "create_main_conversation": True,
                "create_posts_conversation": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        main_id = response.data.get("main_conversation_id")
        posts_id = response.data.get("posts_conversation_id")
        self.assertTrue(main_id)
        self.assertTrue(posts_id)

        community = Community.objects.get(pk=response.data["id"])
        self.assertEqual(str(community.main_conversation_id), str(main_id))
        self.assertEqual(str(community.posts_conversation_id), str(posts_id))
        self.assertEqual(community.main_conversation.type, ConversationType.GROUP)
        self.assertEqual(community.posts_conversation.type, ConversationType.POST)
        self.assertEqual(
            ConversationMember.objects.filter(
                conversation_id__in=[main_id, posts_id],
                user=self.owner,
                left_at__isnull=True,
            ).count(),
            2,
        )
        self.assertTrue(
            CommunityMembership.objects.filter(
                community=community,
                user=self.owner,
                role=CommunityRole.OWNER,
                left_at__isnull=True,
            ).exists()
        )

        listed = self.client.get("/api/v1/chat-communities/")
        self.assertEqual(listed.status_code, status.HTTP_200_OK, listed.data)
        item = listed.data["results"][0] if isinstance(listed.data, dict) else listed.data[0]
        self.assertTrue(item["is_owner"])
        self.assertTrue(item["is_member"])
        self.assertEqual(item["current_user_role"], CommunityRole.OWNER)
