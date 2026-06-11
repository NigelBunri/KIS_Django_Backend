from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.chat.models import Conversation, ConversationMember, ConversationType
from apps.groups.models import Group, GroupMembership


User = get_user_model()


class ChatGroupCreationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone="+237670009901",
            password="TestPass12!",
            country="CM",
        )
        self.client.force_authenticate(self.user)

    def test_chat_group_endpoint_creates_backing_conversation_and_owner(self):
        response = self.client.post(
            "/api/v1/chat-groups/",
            {"name": "Backend-backed group", "slug": "backend-backed-group"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        conversation_id = response.data.get("conversation_id")
        self.assertTrue(conversation_id)

        group = Group.objects.get(pk=response.data["id"])
        self.assertEqual(str(group.conversation_id), str(conversation_id))
        self.assertEqual(group.conversation.type, ConversationType.GROUP)
        self.assertTrue(
            Conversation.objects.filter(pk=conversation_id).exists()
        )
        self.assertTrue(
            ConversationMember.objects.filter(
                conversation_id=conversation_id,
                user=self.user,
                left_at__isnull=True,
            ).exists()
        )
        self.assertTrue(
            GroupMembership.objects.filter(
                group=group,
                user=self.user,
                left_at__isnull=True,
            ).exists()
        )
