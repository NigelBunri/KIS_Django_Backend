"""
Direct coverage for the actual domain fix in this change - the broader
apps.groups/apps.communities/apps.partners test suites (286 tests, all
still passing) never asserted on invite_link's actual URL content before
this fix, so a wrong-but-consistent domain would have passed silently.
These tests fail loudly if the SITE_URL bug (or an equivalent regression)
ever comes back.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.chat.models import BaseConversationRole, Conversation, ConversationMember, ConversationType
from apps.communities.models import Community, CommunityMembership, CommunityMembershipStatus, CommunityRole
from apps.groups.models import Group, GroupMembership, GroupRole
from apps.partners.models import Partner, PartnerInvite
from apps.partners.serializers import PartnerInviteSerializer

User = get_user_model()


def _make_user(phone, username):
    return User.objects.create_user(phone=phone, country="NG", password="pass1234", username=username)


@override_settings(KIS_WEBSITE_PUBLIC_BASE_URL="https://kingdomimpactventures.org", SITE_URL="https://api.kingdomimpactventures.org")
class GroupInviteLinkDomainTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = _make_user("+15550001101", "invitelink_group_owner")
        self.conversation = Conversation.objects.create(type=ConversationType.GROUP, title="x", created_by=self.owner)
        self.group = Group.objects.create(name="Test Group", owner=self.owner, conversation=self.conversation)
        GroupMembership.objects.create(group=self.group, user=self.owner, role=GroupRole.OWNER)
        self.client.force_authenticate(self.owner)

    def test_invite_link_uses_website_domain_not_api_domain(self):
        resp = self.client.get(f"/api/v1/chat-groups/{self.group.id}/invite-link/")
        self.assertEqual(resp.status_code, 200)
        link = resp.data["invite_link"]
        self.assertTrue(
            link.startswith("https://kingdomimpactventures.org/join/group/"),
            f"expected the website domain, got: {link}",
        )
        self.assertNotIn("api.kingdomimpactventures.org", link)


@override_settings(KIS_WEBSITE_PUBLIC_BASE_URL="https://kingdomimpactventures.org", SITE_URL="https://api.kingdomimpactventures.org")
class CommunityInviteLinkDomainTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = _make_user("+15550001102", "invitelink_community_owner")
        self.community = Community.objects.create(
            owner=self.owner, name="Test Community", is_active=True, allow_join_link=True,
        )
        CommunityMembership.objects.create(
            community=self.community, user=self.owner,
            role=CommunityRole.OWNER, status=CommunityMembershipStatus.ACTIVE,
        )
        self.client.force_authenticate(self.owner)

    def test_invite_link_uses_website_domain_not_api_domain(self):
        resp = self.client.get(f"/api/v1/communities/{self.community.id}/invite-link/")
        self.assertEqual(resp.status_code, 200)
        link = resp.data["invite_link"]
        self.assertTrue(
            link.startswith("https://kingdomimpactventures.org/join/community/"),
            f"expected the website domain, got: {link}",
        )
        self.assertNotIn("api.kingdomimpactventures.org", link)


@override_settings(KIS_WEBSITE_PUBLIC_BASE_URL="https://kingdomimpactventures.org")
class PartnerInviteLinkDomainTests(TestCase):
    """Partner invites previously had no shareable URL at all - this
    proves the newly-added invite_link field is real, not just present."""

    def setUp(self):
        self.owner = _make_user("+15550001103", "invitelink_partner_owner")
        self.partner = Partner.objects.create(name="Test Org", owner=self.owner, slug="invitelink-test-org")

    def test_serializer_computes_a_real_shareable_link(self):
        invite = PartnerInvite.objects.create(partner=self.partner)
        data = PartnerInviteSerializer(invite).data
        self.assertEqual(data["invite_link"], f"https://kingdomimpactventures.org/join/partner/{invite.code}")

    @override_settings(KIS_WEBSITE_PUBLIC_BASE_URL="")
    def test_returns_none_rather_than_a_broken_link_when_unconfigured(self):
        invite = PartnerInvite.objects.create(partner=self.partner)
        data = PartnerInviteSerializer(invite).data
        self.assertIsNone(data["invite_link"])
