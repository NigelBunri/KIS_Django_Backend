from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.chat.models import BaseConversationRole, Conversation, ConversationMember, ConversationType
from apps.groups.models import Group, GroupMembership
from apps.partners.models import Partner, PartnerMembership, PartnerMembershipStatus, PartnerRole, PartnerRoleAssignment


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


class PartnerGroupCreationPermissionTests(APITestCase):
    """A regular partner member must not be able to create a group
    attributed to that partner — GroupViewSet.perform_create previously had
    no partner_user_can_manage/permission check at all, letting any
    authenticated user create (and own, as conversation OWNER) a group
    under any partner's name."""

    def setUp(self):
        self.owner = User.objects.create_user(
            phone="+237670009902", password="TestPass12!", country="CM",
        )
        self.member = User.objects.create_user(
            phone="+237670009903", password="TestPass12!", country="CM",
        )
        conversation = Conversation.objects.create(
            type=ConversationType.POST, title="Partner HQ", created_by=self.owner,
        )
        ConversationMember.objects.create(
            conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER,
        )
        self.partner = Partner.objects.create(
            owner=self.owner, name="Partner Co", slug="partner-co", main_conversation=conversation,
        )
        PartnerMembership.objects.create(
            partner=self.partner, user=self.member, status=PartnerMembershipStatus.MEMBER, role="member",
        )

    def test_plain_member_cannot_create_partner_group(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            "/api/v1/chat-groups/",
            {"name": "Partner Group", "slug": "partner-group", "partner": str(self.partner.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Group.objects.filter(partner=self.partner, slug="partner-group").exists())

    def test_owner_can_create_partner_group(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            "/api/v1/chat-groups/",
            {"name": "Partner Group", "slug": "partner-group", "partner": str(self.partner.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_member_with_groups_manage_permission_can_create_partner_group(self):
        role = PartnerRole.objects.create(
            partner=self.partner, name="Group Manager", permissions=["partner.groups.manage"],
        )
        PartnerRoleAssignment.objects.create(partner=self.partner, user=self.member, role=role)

        self.client.force_authenticate(self.member)
        response = self.client.post(
            "/api/v1/chat-groups/",
            {"name": "Partner Group", "slug": "partner-group", "partner": str(self.partner.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
