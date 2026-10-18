from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.chat.models import BaseConversationRole, Conversation, ConversationMember, ConversationType
from apps.communities.models import Community, CommunityBan, CommunityMembership, CommunityRole
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


class ConversationAddMemberCommunitySyncTests(APITestCase):
    """
    The generic "add from contacts/chats" action correctly kept
    GroupMembership in sync (see ConversationAddMemberGroupSyncTests
    above) but never touched the group's owning Community at all -
    unlike GroupViewSet.add_members, which already does. A member added
    this way showed up fine in the group's own member list but was
    permanently invisible in the community's (CommunityViewSet.members
    reads CommunityMembership exclusively), and a community-banned user
    could be silently re-added via this path with no ban check at all.
    """

    def setUp(self):
        self.owner = User.objects.create_user(
            phone="+237670009003", password="TestPass12!", country="CM",
        )
        self.newcomer = User.objects.create_user(
            phone="+237670009004", password="TestPass12!", country="CM",
        )
        self.banned_user = User.objects.create_user(
            phone="+237670009005", password="TestPass12!", country="CM",
        )
        self.main_conversation = Conversation.objects.create(
            type=ConversationType.GROUP, title="Community Main", created_by=self.owner,
        )
        self.posts_conversation = Conversation.objects.create(
            type=ConversationType.POST, title="Community Posts", created_by=self.owner,
        )
        self.community = Community.objects.create(
            owner=self.owner, name="Add Member Sync Community", slug="add-member-sync-community",
            main_conversation=self.main_conversation, posts_conversation=self.posts_conversation,
        )
        CommunityMembership.objects.create(community=self.community, user=self.owner, role=CommunityRole.OWNER)
        CommunityBan.objects.create(community=self.community, user=self.banned_user, reason="test ban")

        self.group = create_group_with_conversation(
            owner=self.owner, name="Community Sub Group", slug="community-sub-group", community=self.community,
        )
        self.client.force_authenticate(self.owner)

    def test_add_member_via_conversation_endpoint_syncs_community_membership(self):
        response = self.client.post(
            f"/api/v1/chats/conversations/{self.group.conversation_id}/members/",
            {"user_id": str(self.newcomer.id), "base_role": "member"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)

        community_membership = CommunityMembership.objects.filter(
            community=self.community, user=self.newcomer,
        ).first()
        self.assertIsNotNone(community_membership)
        self.assertIsNone(community_membership.left_at)
        self.assertTrue(
            ConversationMember.objects.filter(
                conversation=self.main_conversation, user=self.newcomer, left_at__isnull=True,
            ).exists()
        )
        self.assertTrue(
            ConversationMember.objects.filter(
                conversation=self.posts_conversation, user=self.newcomer, left_at__isnull=True,
            ).exists()
        )

    def test_cannot_add_a_community_banned_user_via_conversation_endpoint(self):
        response = self.client.post(
            f"/api/v1/chats/conversations/{self.group.conversation_id}/members/",
            {"user_id": str(self.banned_user.id), "base_role": "member"},
            format="json",
        )
        self.assertEqual(response.status_code, 403, response.data)
        self.assertFalse(
            CommunityMembership.objects.filter(community=self.community, user=self.banned_user).exists()
        )
        self.assertFalse(
            GroupMembership.objects.filter(
                group=self.group, user=self.banned_user, left_at__isnull=True,
            ).exists(),
            "A community ban must block access to this sub-group too, not just leave the user "
            "nominally un-synced at the community level.",
        )
