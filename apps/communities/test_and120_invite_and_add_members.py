"""AND-120: "Can't generate an invite link nor add members." The invite-link
half was a client-side visibility bug (CommunityInfoPage's `me` lookup only
matched object-shaped member.user, so isAdmin was always false and the
invite-link/add-members controls never rendered for real owners/admins -
see src/Module/Community/CommunityInfoPage.tsx). The backend actions these
controls call (CommunityViewSet.invite_link / .add_members below) already
existed and worked; this covers them directly through the same
chat-communities URL prefix the client actually calls, confirming both
endpoints really do work end-to-end for an owner/admin.
"""
from rest_framework import status
from rest_framework.test import APIClient
from django.test import TestCase

from apps.accounts.models import User
from apps.communities.models import (
    Community,
    CommunityJoinPolicy,
    CommunityMembership,
    CommunityMembershipStatus,
    CommunityRole,
)


def _make_user(phone, username):
    return User.objects.create_user(
        phone=phone,
        country="NG",
        password="pass1234",
        username=username,
        email=f"{username}@example.com",
    )


class CommunityInviteLinkAndAddMembersTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = _make_user("+2348130000101", "and120-owner")
        self.member = _make_user("+2348130000102", "and120-member")
        self.outsider = _make_user("+2348130000103", "and120-outsider")
        self.community = Community.objects.create(
            owner=self.owner,
            name="AND-120 Community",
            slug="and-120-community",
            join_policy=CommunityJoinPolicy.OPEN,
        )
        CommunityMembership.objects.create(community=self.community, user=self.owner, role=CommunityRole.OWNER)
        CommunityMembership.objects.create(community=self.community, user=self.member, role=CommunityRole.MEMBER)

    def test_owner_can_generate_invite_link(self):
        self.client.force_authenticate(self.owner)
        res = self.client.post(
            f"/api/v1/chat-communities/{self.community.id}/invite-link/", {}, format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertTrue(res.data.get("invite_link"))
        self.community.refresh_from_db()
        self.assertTrue(self.community.invite_token)
        self.assertIn(self.community.invite_token, res.data["invite_link"])

    def test_plain_member_cannot_generate_invite_link(self):
        self.client.force_authenticate(self.member)
        res = self.client.post(
            f"/api/v1/chat-communities/{self.community.id}/invite-link/", {}, format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN, res.data)

    def test_owner_can_add_members_by_user_id(self):
        self.client.force_authenticate(self.owner)
        res = self.client.post(
            f"/api/v1/chat-communities/{self.community.id}/add-members/",
            {"userIds": [str(self.outsider.id)]},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertEqual(res.data["added"], [str(self.outsider.id)])
        membership = CommunityMembership.objects.get(community=self.community, user=self.outsider)
        self.assertEqual(membership.status, CommunityMembershipStatus.ACTIVE)

    def test_plain_member_cannot_add_members(self):
        self.client.force_authenticate(self.member)
        res = self.client.post(
            f"/api/v1/chat-communities/{self.community.id}/add-members/",
            {"userIds": [str(self.outsider.id)]},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN, res.data)
        self.assertFalse(
            CommunityMembership.objects.filter(community=self.community, user=self.outsider).exists()
        )
