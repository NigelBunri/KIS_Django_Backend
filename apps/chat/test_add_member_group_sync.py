from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.chat.models import BaseConversationRole, ConversationMember
from apps.groups.models import Group, GroupMembership
from apps.groups.services import create_group_with_conversation


User = get_user_model()


class ConversationAddMemberGroupSyncTests(APITestCase):
    """
    ConversationViewSet.add_member/remove_member previously only touched
    ConversationMember. For a conversation that backs a Group, the group's
    own member list is read from the separate GroupMembership table (see
    GroupViewSet.members) - so a member added via the chat/conversation
    "Add member" UI could already send and receive messages, but never
    showed up in the group's member list, even after a refresh.
    """

    def setUp(self):
        self.owner = User.objects.create_user(
            phone="+237670009001",
            password="TestPass12!",
            country="CM",
        )
        self.newcomer = User.objects.create_user(
            phone="+237670009002",
            password="TestPass12!",
            country="CM",
        )
        self.group = create_group_with_conversation(
            owner=self.owner,
            name="Sync test group",
            slug="sync-test-group",
        )
        self.client.force_authenticate(self.owner)

    def test_add_member_via_conversation_endpoint_creates_group_membership(self):
        response = self.client.post(
            f"/api/v1/chats/conversations/{self.group.conversation_id}/members/",
            {"user_id": str(self.newcomer.id), "base_role": "member"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            ConversationMember.objects.filter(
                conversation=self.group.conversation,
                user=self.newcomer,
                left_at__isnull=True,
            ).exists()
        )
        self.assertTrue(
            GroupMembership.objects.filter(
                group=self.group,
                user=self.newcomer,
                left_at__isnull=True,
            ).exists(),
            "Adding a member via the conversation endpoint must also create "
            "a GroupMembership row, or the group's own member list never "
            "reflects the add.",
        )

    def test_group_members_endpoint_reflects_member_added_via_conversation_endpoint(self):
        self.client.post(
            f"/api/v1/chats/conversations/{self.group.conversation_id}/members/",
            {"user_id": str(self.newcomer.id), "base_role": "member"},
            format="json",
        )

        response = self.client.get(f"/api/v1/chat-groups/{self.group.id}/members/")

        self.assertEqual(response.status_code, 200)
        member_user_ids = {str(row["user"]["id"]) for row in response.data}
        self.assertIn(str(self.newcomer.id), member_user_ids)

    def test_remove_member_via_conversation_endpoint_also_deactivates_group_membership(self):
        self.client.post(
            f"/api/v1/chats/conversations/{self.group.conversation_id}/members/",
            {"user_id": str(self.newcomer.id), "base_role": "member"},
            format="json",
        )

        response = self.client.post(
            f"/api/v1/chats/conversations/{self.group.conversation_id}/members/remove/",
            {"user_id": str(self.newcomer.id)},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            GroupMembership.objects.filter(
                group=self.group,
                user=self.newcomer,
                left_at__isnull=True,
            ).exists(),
            "Removing a member via the conversation endpoint must also "
            "deactivate their GroupMembership, or they'd still show up in "
            "the group's own member list despite being removed from the "
            "conversation.",
        )

    def test_rejoin_after_remove_restores_group_membership(self):
        self.client.post(
            f"/api/v1/chats/conversations/{self.group.conversation_id}/members/",
            {"user_id": str(self.newcomer.id), "base_role": "member"},
            format="json",
        )
        self.client.post(
            f"/api/v1/chats/conversations/{self.group.conversation_id}/members/remove/",
            {"user_id": str(self.newcomer.id)},
            format="json",
        )

        response = self.client.post(
            f"/api/v1/chats/conversations/{self.group.conversation_id}/members/",
            {"user_id": str(self.newcomer.id), "base_role": "member"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        membership = GroupMembership.objects.get(group=self.group, user=self.newcomer)
        self.assertIsNone(membership.left_at)
        self.assertFalse(membership.is_banned)
