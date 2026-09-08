from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.channels.models import Channel, Subchannel
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

    def test_plain_member_cannot_create_partner_channel(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            "/api/v1/partner-channels/channels/",
            {
                "partner": str(self.partner.id),
                "name": "General",
                "slug": "general",
                "channel_type": Channel.ChannelType.TEXT,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Channel.objects.filter(partner=self.partner, slug="general").exists())

    def test_member_with_channels_manage_permission_can_create_partner_channel(self):
        role = self._create_role("Channel Manager", ["partner.channels.manage"])
        from apps.partners.models import PartnerRoleAssignment

        PartnerRoleAssignment.objects.create(partner=self.partner, user=self.member, role=role)

        self.client.force_authenticate(self.member)
        response = self.client.post(
            "/api/v1/partner-channels/channels/",
            {
                "partner": str(self.partner.id),
                "name": "General",
                "slug": "general",
                "channel_type": Channel.ChannelType.TEXT,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

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


class VoiceChannelTierGateApiTests(TestCase):
    """Voice channels are a Partner Pro-exclusive differentiator (see
    apps/accounts/tier_presets.py's "voice_channels" feature flag) — the
    org's own PartnerSubscription is what's checked, not the requesting
    staff member's personal tier, matching the existing job_posting gate."""

    def setUp(self):
        from apps.accounts.models import AccountTier, Subscription
        from apps.accounts.tiers import ensure_default_account_tiers
        from apps.partners.models import PartnerSubscription

        self.client = APIClient()
        ensure_default_account_tiers()
        self.owner = User.objects.create_user(phone="+237671009001", country="CM", password="pass1234")
        conversation = Conversation.objects.create(
            type=ConversationType.POST, title="Voice Partner", description="", created_by=self.owner,
        )
        ConversationMember.objects.create(conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER)
        self.partner = Partner.objects.create(owner=self.owner, name="Voice Partner", slug="voice-partner", main_conversation=conversation)
        self.partner_pro_tier = AccountTier.objects.filter(name__iexact="Partner Pro").first()
        self.partner_tier = AccountTier.objects.filter(name__iexact="Partner").first()
        self.subscription = PartnerSubscription.objects.create(partner=self.partner, tier=self.partner_tier, status="active")
        # The channel-count cap in ChannelViewSet.perform_create checks the
        # REQUESTING USER's own personal tier (a separate, pre-existing gate
        # from the org-level voice_channels feature this test targets) — give
        # the owner a personal plan with enough headroom so that unrelated
        # check doesn't shadow the one under test.
        Subscription.objects.create(user=self.owner, tier=self.partner_pro_tier, status="active")

    def _create_body(self):
        return {
            "partner": str(self.partner.id),
            "name": "Lounge",
            "slug": "lounge",
            "channel_type": Channel.ChannelType.VOICE,
        }

    def test_partner_tier_cannot_create_voice_channel(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post("/api/v1/partner-channels/channels/", self._create_body(), format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_partner_pro_tier_can_create_voice_channel(self):
        self.subscription.tier = self.partner_pro_tier
        self.subscription.save(update_fields=["tier"])
        self.client.force_authenticate(self.owner)
        response = self.client.post("/api/v1/partner-channels/channels/", self._create_body(), format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["channel_type"], Channel.ChannelType.VOICE)

    def test_partner_tier_can_still_create_text_channel(self):
        self.client.force_authenticate(self.owner)
        body = self._create_body()
        body["channel_type"] = Channel.ChannelType.TEXT
        response = self.client.post("/api/v1/partner-channels/channels/", body, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)


class _PersonalChannelTestBase(TestCase):
    """Fixtures for a personal (non-partner) Channel — the case
    ChannelServerOrganizationApiTests above doesn't cover, since every
    channel there belongs to self.partner. Subchannel authorization and
    private-channel access are both personal-channel-shaped bugs, so
    tested against a personal channel specifically, not a partner one
    (which already had its own, separate, correctly-working permission
    system before this pass — see apps/partners/services.py)."""

    def setUp(self):
        self.client = APIClient()
        self.owner = self._create_user("owner", "+237672000001")
        self.stranger = self._create_user("stranger", "+237672000002")
        self.member = self._create_user("member", "+237672000003")

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

    def _create_personal_channel(self, *, owner: User, name: str, slug: str, channel_type: str = Channel.ChannelType.ANNOUNCEMENT) -> Channel:
        conversation = Conversation.objects.create(
            type=ConversationType.CHANNEL, title=name, created_by=owner,
        )
        ConversationMember.objects.create(
            conversation=conversation, user=owner, base_role=BaseConversationRole.OWNER,
        )
        return Channel.objects.create(
            partner=None, community=None, category=None,
            owner=owner, conversation=conversation, name=name, slug=slug, channel_type=channel_type,
        )


class SubchannelAuthorizationTests(_PersonalChannelTestBase):
    """P0 fix: SubchannelViewSet previously had no ownership/permission
    check at all on create/update/destroy beyond IsAuthenticated — any
    authenticated user could create, rename, or delete a subchannel of ANY
    channel. Tests the exact matrix the task asked for: owner can manage,
    an unrelated authenticated user cannot, and a direct API request
    (not routed through any frontend) cannot bypass the restriction."""

    def setUp(self):
        super().setUp()
        self.channel = self._create_personal_channel(owner=self.owner, name="Owner Channel", slug="owner-channel")
        ConversationMember.objects.create(
            conversation=self.channel.conversation, user=self.member, base_role=BaseConversationRole.MEMBER,
        )

    def test_owner_can_create_subchannel(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            "/api/v1/subchannels/", {"channel": str(self.channel.id), "name": "General"}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_unrelated_authenticated_user_cannot_create_subchannel(self):
        """The exact gap: previously succeeded (201) for anyone."""
        self.client.force_authenticate(self.stranger)
        response = self.client.post(
            "/api/v1/subchannels/", {"channel": str(self.channel.id), "name": "General"}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_plain_member_cannot_create_subchannel(self):
        """Membership alone isn't management — only owner/admin can."""
        self.client.force_authenticate(self.member)
        response = self.client.post(
            "/api/v1/subchannels/", {"channel": str(self.channel.id), "name": "General"}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_can_update_and_delete_subchannel(self):
        self.client.force_authenticate(self.owner)
        sub = Subchannel.objects.create(channel=self.channel, name="General", created_by=self.owner)
        update = self.client.patch(f"/api/v1/subchannels/{sub.id}/", {"name": "Renamed"}, format="json")
        self.assertEqual(update.status_code, status.HTTP_200_OK, update.data)
        delete = self.client.delete(f"/api/v1/subchannels/{sub.id}/")
        self.assertEqual(delete.status_code, status.HTTP_204_NO_CONTENT)

    def test_unrelated_authenticated_user_cannot_update_subchannel(self):
        sub = Subchannel.objects.create(channel=self.channel, name="General", created_by=self.owner)
        self.client.force_authenticate(self.stranger)
        response = self.client.patch(f"/api/v1/subchannels/{sub.id}/", {"name": "Hijacked"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        sub.refresh_from_db()
        self.assertEqual(sub.name, "General")

    def test_unrelated_authenticated_user_cannot_delete_subchannel(self):
        """The exact gap, direct-API-request form: DELETE by id, no
        ownership check previously existed at all."""
        sub = Subchannel.objects.create(channel=self.channel, name="General", created_by=self.owner)
        self.client.force_authenticate(self.stranger)
        response = self.client.delete(f"/api/v1/subchannels/{sub.id}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Subchannel.objects.filter(id=sub.id).exists())

    def test_plain_member_cannot_delete_subchannel(self):
        sub = Subchannel.objects.create(channel=self.channel, name="General", created_by=self.owner)
        self.client.force_authenticate(self.member)
        response = self.client.delete(f"/api/v1/subchannels/{sub.id}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Subchannel.objects.filter(id=sub.id).exists())


class PersonalPrivateChannelAccessTests(_PersonalChannelTestBase):
    """P0 fix: channel_type=PRIVATE was a label with zero backend
    enforcement for personal (non-partner) channels — discoverable,
    directly retrievable, and self-subscribable by anyone. Tests the
    access model this pass actually implements: hidden from discovery/
    search/direct-retrieve/self-subscribe for non-members, with the
    owner/admin able to grant access via the new members-add action."""

    def setUp(self):
        super().setUp()
        self.private_channel = self._create_personal_channel(
            owner=self.owner, name="Private Club", slug="private-club", channel_type=Channel.ChannelType.PRIVATE,
        )
        self.public_channel = self._create_personal_channel(
            owner=self.owner, name="Public Square", slug="public-square", channel_type=Channel.ChannelType.ANNOUNCEMENT,
        )

    def test_private_channel_excluded_from_stranger_discovery_list(self):
        self.client.force_authenticate(self.stranger)
        response = self.client.get("/api/v1/partner-channels/channels/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {row["id"] for row in response.data["results"]}
        self.assertNotIn(str(self.private_channel.id), ids)
        self.assertIn(str(self.public_channel.id), ids)

    def test_private_channel_excluded_from_stranger_search(self):
        self.client.force_authenticate(self.stranger)
        response = self.client.get("/api/v1/partner-channels/channels/?q=Private")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {row["id"] for row in response.data["results"]}
        self.assertNotIn(str(self.private_channel.id), ids)

    def test_private_channel_visible_to_owner_in_discovery(self):
        self.client.force_authenticate(self.owner)
        response = self.client.get("/api/v1/partner-channels/channels/")
        ids = {row["id"] for row in response.data["results"]}
        self.assertIn(str(self.private_channel.id), ids)

    def test_private_channel_direct_retrieve_404_for_stranger(self):
        """Not 403 — a private channel a non-member has no business
        knowing exists shouldn't confirm its existence via a
        distinguishable error code."""
        self.client.force_authenticate(self.stranger)
        response = self.client.get(f"/api/v1/partner-channels/channels/{self.private_channel.id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_private_channel_direct_retrieve_ok_for_owner(self):
        self.client.force_authenticate(self.owner)
        response = self.client.get(f"/api/v1/partner-channels/channels/{self.private_channel.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_private_channel_self_subscribe_rejected_for_stranger(self):
        """The exact gap: previously succeeded (201, granted membership)
        for literally anyone, regardless of channel_type."""
        self.client.force_authenticate(self.stranger)
        response = self.client.post(f"/api/v1/partner-channels/channels/{self.private_channel.id}/subscribe/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(
            ConversationMember.objects.filter(
                conversation=self.private_channel.conversation, user=self.stranger, left_at__isnull=True,
            ).exists()
        )

    def test_public_channel_self_subscribe_still_works_for_stranger(self):
        """Confirms the fix is scoped to PRIVATE only — a normal personal
        channel's existing open-subscribe behavior is unchanged."""
        self.client.force_authenticate(self.stranger)
        response = self.client.post(f"/api/v1/partner-channels/channels/{self.public_channel.id}/subscribe/")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_owner_can_invite_stranger_via_add_member(self):
        """The real 'invite-only' mechanism: owner explicitly grants
        access. After being added, the invited user can see and retrieve
        the channel like any other member."""
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            f"/api/v1/partner-channels/channels/{self.private_channel.id}/members/",
            {"user_id": str(self.stranger.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        self.client.force_authenticate(self.stranger)
        retrieve = self.client.get(f"/api/v1/partner-channels/channels/{self.private_channel.id}/")
        self.assertEqual(retrieve.status_code, status.HTTP_200_OK)
        discovery = self.client.get("/api/v1/partner-channels/channels/")
        ids = {row["id"] for row in discovery.data["results"]}
        self.assertIn(str(self.private_channel.id), ids)

    def test_stranger_cannot_add_themselves_as_member(self):
        """Only channel-manage permission can add members — a non-owner
        can't grant themselves (or anyone else) access this way either.

        Uses the PUBLIC channel specifically: on the private one, get_object()
        itself already 404s a non-member before this action's own manage-
        permission check is ever reached (see
        test_private_channel_direct_retrieve_404_for_stranger) — this test
        isolates the manage-permission gate itself, which is what actually
        stops a stranger who *can* see a channel from adding members to it."""
        self.client.force_authenticate(self.stranger)
        response = self.client.post(
            f"/api/v1/partner-channels/channels/{self.public_channel.id}/members/",
            {"user_id": str(self.stranger.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_stranger_add_member_on_private_channel_is_hidden_as_404(self):
        """Companion to the above: for a channel the stranger can't even
        see, the response is 404 (existence hidden), not 403 — consistent
        with retrieve/subscribe's own behavior for the same case."""
        self.client.force_authenticate(self.stranger)
        response = self.client.post(
            f"/api/v1/partner-channels/channels/{self.private_channel.id}/members/",
            {"user_id": str(self.stranger.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_member_list_not_visible_to_non_member_even_for_public_channel(self):
        """Privacy-by-default on the roster itself, not just channel
        content — get_object() alone doesn't gate this for a public
        channel, so the members() action's own explicit check is the real
        enforcement point here."""
        self.client.force_authenticate(self.stranger)
        response = self.client.get(f"/api/v1/partner-channels/channels/{self.public_channel.id}/members/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class ChannelDiscoveryOrderingTests(_PersonalChannelTestBase):
    """P0 fix: order_by('?') re-randomizes on every query, which breaks
    pagination (duplicate/skipped rows across pages since page 2's query
    re-randomizes independently of page 1's). Tests the property that
    actually matters for pagination correctness: repeated, unmodified
    queries return channels in the same order."""

    def setUp(self):
        super().setUp()
        for i in range(5):
            self._create_personal_channel(owner=self.owner, name=f"Channel {i}", slug=f"channel-{i}")

    def test_repeated_discovery_queries_return_stable_order(self):
        self.client.force_authenticate(self.owner)
        first = self.client.get("/api/v1/partner-channels/channels/")
        second = self.client.get("/api/v1/partner-channels/channels/")
        first_ids = [row["id"] for row in first.data["results"]]
        second_ids = [row["id"] for row in second.data["results"]]
        self.assertEqual(first_ids, second_ids)
        self.assertGreaterEqual(len(first_ids), 5)
