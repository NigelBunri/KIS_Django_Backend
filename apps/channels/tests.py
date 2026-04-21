from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.channels.models import Channel
from apps.chat.models import BaseConversationRole, Conversation, ConversationMember, ConversationType
from apps.partners.models import (
    Partner,
    PartnerMembership,
    PartnerMembershipStatus,
    PartnerServerCategory,
    PartnerRole,
    PartnerChannelPermissionOverwrite,
)


class ChannelServerOrganizationApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = self._create_user("channel-owner", "+237671000001")
        self.member = self._create_user("channel-member", "+237671000002")
        self.partner = self._create_partner(self.owner, "Partner Server", "partner-server")
        PartnerMembership.objects.create(
            partner=self.partner,
            user=self.member,
            status=PartnerMembershipStatus.MEMBER,
            role="member",
        )
        ConversationMember.objects.create(
            conversation=self.partner.main_conversation,
            user=self.member,
            base_role=BaseConversationRole.MEMBER,
        )

    def _create_user(self, username: str, phone: str) -> User:
        suffix = phone[-4:]
        return User.objects.create_user(
            phone=phone,
            country="CM",
            password="pass1234",
            email=f"{username}-{suffix}@example.com",
            username=f"{username}-{suffix}",
            display_name=username.title(),
            phone_country_code="+237",
            phone_number=phone[-9:],
        )

    def _create_partner(self, owner: User, name: str, slug: str) -> Partner:
        conversation = Conversation.objects.create(
            type=ConversationType.POST,
            title=name,
            description=f"Post space for {name}",
            created_by=owner,
        )
        ConversationMember.objects.create(
            conversation=conversation,
            user=owner,
            base_role=BaseConversationRole.OWNER,
        )
        return Partner.objects.create(
            owner=owner,
            name=name,
            slug=slug,
            main_conversation=conversation,
        )

    def _create_channel(
        self,
        *,
        owner: User | None = None,
        name: str,
        slug: str,
        category: PartnerServerCategory | None = None,
        order: int = 0,
        channel_type: str = Channel.ChannelType.TEXT,
    ) -> Channel:
        conversation = Conversation.objects.create(
            type=ConversationType.CHANNEL,
            title=name,
            created_by=owner or self.owner,
        )
        ConversationMember.objects.create(
            conversation=conversation,
            user=owner or self.owner,
            base_role=BaseConversationRole.OWNER,
        )
        return Channel.objects.create(
            partner=self.partner,
            category=category,
            owner=owner or self.owner,
            conversation=conversation,
            name=name,
            slug=slug,
            channel_type=channel_type,
            order=order,
        )

    def _create_role(self, name: str, permissions: list[str] | None = None) -> PartnerRole:
        return PartnerRole.objects.create(
            partner=self.partner,
            name=name,
            permissions=permissions or [],
        )

    def test_owner_can_create_and_list_server_categories(self):
        self.client.force_authenticate(self.owner)

        create_response = self.client.post(
            f"/api/v1/partners/{self.partner.id}/server-categories/",
            {"name": "Staff", "slug": "staff", "order": 10, "is_private": True},
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)

        list_response = self.client.get(f"/api/v1/partners/{self.partner.id}/server-categories/")
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(list_response.data["categories"]), 1)
        self.assertEqual(list_response.data["categories"][0]["slug"], "staff")

    def test_channel_create_rejects_category_from_another_partner(self):
        other_partner = self._create_partner(self.owner, "Other Partner", "other-partner")
        foreign_category = PartnerServerCategory.objects.create(
            partner=other_partner,
            name="Foreign",
            slug="foreign",
            order=1,
        )

        self.client.force_authenticate(self.owner)
        response = self.client.post(
            "/api/v1/partner-channels/channels/",
            {
                "partner": str(self.partner.id),
                "name": "General",
                "slug": "general",
                "channel_type": Channel.ChannelType.TEXT,
                "category": foreign_category.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("category", response.data)

    def test_partner_filtered_channel_list_is_ordered_by_category_and_channel_order(self):
        staff = PartnerServerCategory.objects.create(
            partner=self.partner,
            name="Staff",
            slug="staff",
            order=1,
        )
        public = PartnerServerCategory.objects.create(
            partner=self.partner,
            name="Public",
            slug="public",
            order=2,
        )

        self._create_channel(name="ops", slug="ops", category=staff, order=2)
        self._create_channel(name="announcements", slug="announcements", category=public, order=1)
        self._create_channel(name="general", slug="general", category=public, order=3)
        self._create_channel(name="backroom", slug="backroom", category=staff, order=1)

        self.client.force_authenticate(self.member)
        response = self.client.get(f"/api/v1/partner-channels/channels/?partner={self.partner.id}")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"] if isinstance(response.data, dict) else response.data
        self.assertEqual(
            [item["slug"] for item in results],
            ["backroom", "ops", "announcements", "general"],
        )

    def test_private_category_channel_hidden_without_matching_overwrite(self):
        member_role = self._create_role("Member")
        staff = PartnerServerCategory.objects.create(
            partner=self.partner,
            name="Staff",
            slug="staff",
            order=1,
            is_private=True,
        )
        public = PartnerServerCategory.objects.create(
            partner=self.partner,
            name="Public",
            slug="public",
            order=2,
        )
        self._create_channel(name="staff-room", slug="staff-room", category=staff, order=1)
        public_channel = self._create_channel(name="general", slug="general", category=public, order=1)

        PartnerChannelPermissionOverwrite.objects.create(
            partner=self.partner,
            channel=public_channel,
            subject_type=PartnerChannelPermissionOverwrite.SubjectType.ROLE,
            role=member_role,
            allow_permissions=[PartnerChannelPermissionOverwrite.PermissionCode.VIEW_CHANNEL],
            deny_permissions=[],
        )

        self.client.force_authenticate(self.member)
        response = self.client.get(f"/api/v1/partner-channels/channels/?partner={self.partner.id}")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"] if isinstance(response.data, dict) else response.data
        self.assertEqual([item["slug"] for item in results], ["general"])

    def test_role_overwrite_allows_manager_into_private_channel(self):
        manager = self._create_user("channel-manager", "+237671000003")
        manager_role = self._create_role("Manager")
        staff = PartnerServerCategory.objects.create(
            partner=self.partner,
            name="Staff",
            slug="staff",
            order=1,
            is_private=True,
        )
        staff_channel = self._create_channel(name="staff-room", slug="staff-room", category=staff, order=1)
        PartnerMembership.objects.create(
            partner=self.partner,
            user=manager,
            status=PartnerMembershipStatus.MEMBER,
            role="manager",
        )
        ConversationMember.objects.create(
            conversation=self.partner.main_conversation,
            user=manager,
            base_role=BaseConversationRole.MEMBER,
        )
        PartnerChannelPermissionOverwrite.objects.create(
            partner=self.partner,
            channel=staff_channel,
            subject_type=PartnerChannelPermissionOverwrite.SubjectType.ROLE,
            role=manager_role,
            allow_permissions=[
                PartnerChannelPermissionOverwrite.PermissionCode.VIEW_CHANNEL,
                PartnerChannelPermissionOverwrite.PermissionCode.SEND_MESSAGES,
            ],
            deny_permissions=[],
        )

        self.client.force_authenticate(manager)
        response = self.client.get(f"/api/v1/partner-channels/channels/?partner={self.partner.id}")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"] if isinstance(response.data, dict) else response.data
        self.assertEqual([item["slug"] for item in results], ["staff-room"])
        self.assertTrue(results[0]["can_post"])

    def test_member_specific_allow_overrides_role_deny_and_subscribes_readonly_when_send_not_allowed(self):
        member_role = self._create_role("Member")
        staff = PartnerServerCategory.objects.create(
            partner=self.partner,
            name="Staff",
            slug="staff",
            order=1,
            is_private=True,
        )
        channel = self._create_channel(name="records", slug="records", category=staff, order=1)
        PartnerChannelPermissionOverwrite.objects.create(
            partner=self.partner,
            channel=channel,
            subject_type=PartnerChannelPermissionOverwrite.SubjectType.ROLE,
            role=member_role,
            allow_permissions=[],
            deny_permissions=[
                PartnerChannelPermissionOverwrite.PermissionCode.VIEW_CHANNEL,
                PartnerChannelPermissionOverwrite.PermissionCode.SEND_MESSAGES,
            ],
        )
        PartnerChannelPermissionOverwrite.objects.create(
            partner=self.partner,
            channel=channel,
            subject_type=PartnerChannelPermissionOverwrite.SubjectType.MEMBER,
            user=self.member,
            allow_permissions=[PartnerChannelPermissionOverwrite.PermissionCode.VIEW_CHANNEL],
            deny_permissions=[],
        )

        self.client.force_authenticate(self.member)
        list_response = self.client.get(f"/api/v1/partner-channels/channels/?partner={self.partner.id}")
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        results = list_response.data["results"] if isinstance(list_response.data, dict) else list_response.data
        self.assertEqual([item["slug"] for item in results], ["records"])
        self.assertFalse(results[0]["can_post"])

        subscribe_response = self.client.post(f"/api/v1/partner-channels/channels/{channel.id}/subscribe/")
        self.assertEqual(subscribe_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(subscribe_response.data["role"], BaseConversationRole.READONLY)
