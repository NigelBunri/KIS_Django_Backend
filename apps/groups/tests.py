from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.chat.models import BaseConversationRole, Conversation, ConversationMember, ConversationType
from apps.groups.models import Group, GroupJoinPolicy, GroupJoinRequest, GroupMembership
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


class GroupMembershipReactivationTests(APITestCase):
    """
    Email/E2EE-history audit fix: GroupMembership.joined_at and
    ConversationMember.joined_at previously only stamped once, at first-ever
    creation - every join/reactivation path (join, add-members,
    join-by-invite, approve-request) cleared left_at/is_banned on rejoin but
    never restamped joined_at, so a member who left and rejoined kept their
    ORIGINAL join date forever. This matters because the RN client derives
    the "messages sent before you joined the group" boundary purely from
    this timestamp (message decryption itself is already correctly blocked
    for pre-join messages via the E2EE per-recipient envelope scheme,
    independent of this fix - joined_at only controls what the UI boundary
    marker draws, not what's actually decryptable).
    """

    def setUp(self):
        self.owner = User.objects.create_user(
            phone="+237670009910", password="TestPass12!", country="CM",
        )
        self.admin = User.objects.create_user(
            phone="+237670009911", password="TestPass12!", country="CM",
        )
        self.member = User.objects.create_user(
            phone="+237670009912", password="TestPass12!", country="CM",
        )
        conversation = Conversation.objects.create(
            type=ConversationType.GROUP, title="Reactivation Test Group", created_by=self.owner,
        )
        self.group = Group.objects.create(
            name="Reactivation Test Group",
            slug="reactivation-test-group",
            owner=self.owner,
            conversation=conversation,
            join_policy=GroupJoinPolicy.OPEN,
            invite_token="reactivation-test-token",
        )
        GroupMembership.objects.create(group=self.group, user=self.owner, role="owner")
        ConversationMember.objects.create(
            conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER,
        )
        GroupMembership.objects.create(group=self.group, user=self.admin, role="admin")
        ConversationMember.objects.create(
            conversation=conversation, user=self.admin, base_role=BaseConversationRole.ADMIN,
        )

    def test_first_ever_join_stamps_joined_at_at_creation(self):
        # join() is a detail-route action: get_object() requires the caller
        # to already be an owner/active member of the group (see
        # GroupViewSet.get_queryset()), so a total stranger can never reach
        # it directly - joining for the first time is only reachable via
        # join-by-invite (this test) or an admin adding them via
        # add-members. Both are covered; this exercises the invite path,
        # which is also the actual real-world trigger for this feature.
        self.client.force_authenticate(self.member)
        before = timezone.now()
        response = self.client.post(
            "/api/v1/chat-groups/join-by-invite/", {"invite_token": self.group.invite_token},
        )
        after = timezone.now()

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        membership = GroupMembership.objects.get(group=self.group, user=self.member)
        self.assertTrue(before <= membership.joined_at <= after)

    def test_rejoin_via_join_by_invite_restamps_joined_at(self):
        self.client.force_authenticate(self.member)
        self.client.post("/api/v1/chat-groups/join-by-invite/", {"invite_token": self.group.invite_token})
        membership = GroupMembership.objects.get(group=self.group, user=self.member)
        original_joined_at = membership.joined_at

        membership.left_at = timezone.now()
        membership.is_banned = False
        membership.save(update_fields=["left_at", "is_banned"])
        ConversationMember.objects.filter(
            conversation=self.group.conversation, user=self.member,
        ).update(left_at=timezone.now())

        response = self.client.post(
            "/api/v1/chat-groups/join-by-invite/", {"invite_token": self.group.invite_token},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        membership.refresh_from_db()
        self.assertIsNone(membership.left_at)
        self.assertGreater(membership.joined_at, original_joined_at)

        # ConversationMember must be reactivated too - it's the model the
        # RN client actually reads joined_at from for the history boundary.
        cm = ConversationMember.objects.get(conversation=self.group.conversation, user=self.member)
        self.assertIsNone(cm.left_at)
        self.assertGreater(cm.joined_at, original_joined_at)

    def test_reactivation_via_add_members_restamps_joined_at(self):
        self.client.force_authenticate(self.owner)
        self.client.post(
            f"/api/v1/chat-groups/{self.group.id}/add-members/",
            {"userIds": [str(self.member.id)]},
            format="json",
        )
        membership = GroupMembership.objects.get(group=self.group, user=self.member)
        original_joined_at = membership.joined_at

        membership.left_at = timezone.now()
        membership.save(update_fields=["left_at"])
        ConversationMember.objects.filter(
            conversation=self.group.conversation, user=self.member,
        ).update(left_at=timezone.now())

        response = self.client.post(
            f"/api/v1/chat-groups/{self.group.id}/add-members/",
            {"userIds": [str(self.member.id)]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        membership.refresh_from_db()
        self.assertIsNone(membership.left_at)
        self.assertGreater(membership.joined_at, original_joined_at)

    def test_reactivation_via_approve_request_restamps_joined_at(self):
        self.client.force_authenticate(self.member)
        self.client.post(
            "/api/v1/chat-groups/join-by-invite/", {"invite_token": self.group.invite_token},
        )
        membership = GroupMembership.objects.get(group=self.group, user=self.member)
        membership.left_at = timezone.now()
        membership.save(update_fields=["left_at"])
        original_joined_at = membership.joined_at

        join_req = GroupJoinRequest.objects.create(group=self.group, user=self.member)

        self.client.force_authenticate(self.admin)
        response = self.client.post(
            f"/api/v1/chat-groups/{self.group.id}/approve-request/",
            {"request_id": join_req.id},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        membership.refresh_from_db()
        self.assertIsNone(membership.left_at)
        self.assertGreater(membership.joined_at, original_joined_at)

    def test_redundant_join_while_already_active_does_not_change_joined_at(self):
        # Establish initial membership via the invite link - join() itself
        # requires the caller to already be an active member/owner to pass
        # get_object() (see get_queryset()), so it can only be exercised
        # here as the "confirm/no-op" second call, never the first.
        self.client.force_authenticate(self.member)
        self.client.post(
            "/api/v1/chat-groups/join-by-invite/", {"invite_token": self.group.invite_token},
        )
        membership = GroupMembership.objects.get(group=self.group, user=self.member)
        original_joined_at = membership.joined_at

        response = self.client.post(f"/api/v1/chat-groups/{self.group.id}/join/")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        membership.refresh_from_db()
        self.assertEqual(membership.joined_at, original_joined_at)

    def test_redundant_approve_request_while_already_active_does_not_change_joined_at(self):
        self.client.force_authenticate(self.member)
        self.client.post(
            "/api/v1/chat-groups/join-by-invite/", {"invite_token": self.group.invite_token},
        )
        membership = GroupMembership.objects.get(group=self.group, user=self.member)
        original_joined_at = membership.joined_at

        # A join request approved against an already-active member (e.g. a
        # stale request re-approved) must not reset their real join date -
        # this is exactly the case a naive "always restamp in defaults="
        # fix would have broken.
        join_req = GroupJoinRequest.objects.create(group=self.group, user=self.member)

        self.client.force_authenticate(self.admin)
        response = self.client.post(
            f"/api/v1/chat-groups/{self.group.id}/approve-request/",
            {"request_id": join_req.id},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        membership.refresh_from_db()
        self.assertEqual(membership.joined_at, original_joined_at)

    def test_ban_sets_left_at_and_is_banned(self):
        self.client.force_authenticate(self.member)
        self.client.post(
            "/api/v1/chat-groups/join-by-invite/", {"invite_token": self.group.invite_token},
        )

        self.client.force_authenticate(self.owner)
        response = self.client.post(
            f"/api/v1/chat-groups/{self.group.id}/ban/", {"user_id": str(self.member.id)},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        membership = GroupMembership.objects.get(group=self.group, user=self.member)
        self.assertTrue(membership.is_banned)
        self.assertIsNotNone(membership.left_at)

    def test_unban_clears_left_at_restamps_joined_at_and_restores_active_membership(self):
        self.client.force_authenticate(self.member)
        self.client.post(
            "/api/v1/chat-groups/join-by-invite/", {"invite_token": self.group.invite_token},
        )
        membership = GroupMembership.objects.get(group=self.group, user=self.member)
        original_joined_at = membership.joined_at

        self.client.force_authenticate(self.owner)
        self.client.post(f"/api/v1/chat-groups/{self.group.id}/ban/", {"user_id": str(self.member.id)})

        response = self.client.post(
            f"/api/v1/chat-groups/{self.group.id}/unban/", {"user_id": str(self.member.id)},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        membership.refresh_from_db()
        self.assertFalse(membership.is_banned)
        self.assertIsNone(membership.left_at)
        self.assertGreater(membership.joined_at, original_joined_at)

        # members() filters on left_at__isnull=True - regression guard for
        # the bug that would occur if ban() set left_at but unban() forgot
        # to clear it back.
        self.client.force_authenticate(self.owner)
        members_response = self.client.get(f"/api/v1/chat-groups/{self.group.id}/members/")
        member_ids = {m["user"]["id"] for m in members_response.data}
        self.assertIn(str(self.member.id), member_ids)


class InviteLinkAndJoinPolicyTests(APITestCase):
    """
    invite_link() was admin/owner-only for BOTH its GET (view/share the
    current link) and POST (regenerate) branches - the task this covers
    was "allow other members to generate/share the group invite link",
    which GET being admin-only made impossible for a plain member.
    join_by_invite() separately never consulted group.join_policy at all,
    so a group with "Approval required to join" (GroupJoinPolicy.REQUEST)
    still let anyone with the link join immediately - the exact bypass
    request_join()/approve_request() exist to prevent.
    """

    def setUp(self):
        self.owner = User.objects.create_user(
            phone="+237670009920", password="TestPass12!", country="CM",
        )
        self.member = User.objects.create_user(
            phone="+237670009921", password="TestPass12!", country="CM",
        )
        self.stranger = User.objects.create_user(
            phone="+237670009922", password="TestPass12!", country="CM",
        )
        conversation = Conversation.objects.create(
            type=ConversationType.GROUP, title="Invite Policy Test Group", created_by=self.owner,
        )
        self.group = Group.objects.create(
            name="Invite Policy Test Group",
            slug="invite-policy-test-group",
            owner=self.owner,
            conversation=conversation,
            join_policy=GroupJoinPolicy.OPEN,
        )
        GroupMembership.objects.create(group=self.group, user=self.owner, role="owner")
        ConversationMember.objects.create(
            conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER,
        )
        GroupMembership.objects.create(group=self.group, user=self.member, role="member")
        ConversationMember.objects.create(
            conversation=conversation, user=self.member, base_role=BaseConversationRole.MEMBER,
        )

    def test_a_plain_member_can_view_the_invite_link(self):
        self.client.force_authenticate(self.member)
        response = self.client.get(f"/api/v1/chat-groups/{self.group.id}/invite-link/")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertTrue(response.data.get("invite_link"))

    def test_a_non_member_cannot_view_the_invite_link(self):
        # 404, not 403: GroupViewSet.get_queryset() already scopes groups to
        # the requester's own membership, so a non-member's get_object()
        # never resolves far enough to reach this action's own permission
        # check at all - same hide-not-reveal posture this app uses
        # elsewhere for content a caller has no business knowing exists.
        self.client.force_authenticate(self.stranger)
        response = self.client.get(f"/api/v1/chat-groups/{self.group.id}/invite-link/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_plain_member_cannot_regenerate_the_invite_link(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(f"/api/v1/chat-groups/{self.group.id}/invite-link/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_the_owner_can_regenerate_the_invite_link(self):
        self.client.force_authenticate(self.owner)
        first = self.client.get(f"/api/v1/chat-groups/{self.group.id}/invite-link/").data["invite_link"]

        self.client.force_authenticate(self.owner)
        second = self.client.post(f"/api/v1/chat-groups/{self.group.id}/invite-link/").data["invite_link"]

        self.assertNotEqual(first, second)

    def test_join_by_invite_joins_immediately_when_policy_is_open(self):
        self.group.invite_token = "open-policy-token"
        self.group.save(update_fields=["invite_token"])
        self.client.force_authenticate(self.stranger)

        response = self.client.post(
            "/api/v1/chat-groups/join-by-invite/", {"invite_token": "open-policy-token"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data.get("pending"))
        self.assertTrue(
            GroupMembership.objects.filter(
                group=self.group, user=self.stranger, left_at__isnull=True,
            ).exists()
        )

    def test_join_by_invite_creates_a_request_instead_of_joining_when_approval_required(self):
        self.group.join_policy = GroupJoinPolicy.REQUEST
        self.group.invite_token = "request-policy-token"
        self.group.save(update_fields=["join_policy", "invite_token"])
        self.client.force_authenticate(self.stranger)

        response = self.client.post(
            "/api/v1/chat-groups/join-by-invite/", {"invite_token": "request-policy-token"},
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertTrue(response.data.get("pending"))
        self.assertFalse(
            GroupMembership.objects.filter(
                group=self.group, user=self.stranger, left_at__isnull=True,
            ).exists()
        )
        self.assertTrue(
            GroupJoinRequest.objects.filter(group=self.group, user=self.stranger).exists()
        )

    def test_join_policy_is_writable_via_group_patch_by_the_owner(self):
        self.client.force_authenticate(self.owner)
        response = self.client.patch(
            f"/api/v1/chat-groups/{self.group.id}/", {"join_policy": "request"}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.group.refresh_from_db()
        self.assertEqual(self.group.join_policy, GroupJoinPolicy.REQUEST)

    def test_join_policy_cannot_be_changed_by_a_plain_member(self):
        self.client.force_authenticate(self.member)
        response = self.client.patch(
            f"/api/v1/chat-groups/{self.group.id}/", {"join_policy": "request"}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.group.refresh_from_db()
        self.assertEqual(self.group.join_policy, GroupJoinPolicy.OPEN)
