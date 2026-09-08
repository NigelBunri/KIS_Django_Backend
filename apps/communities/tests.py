from unittest.mock import patch

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chat.models import Conversation, ConversationMember, ConversationType
from apps.communities.models import (
    Community,
    CommunityBan,
    CommunityJoinPolicy,
    CommunityJoinRequest,
    CommunityJoinRequestStatus,
    CommunityMembership,
    CommunityMembershipStatus,
    CommunityPost,
    CommunityRole,
)
from apps.communities.serializers import CommunityPostSerializer
from apps.notifications.models import Notification


def _make_user(phone, username):
    return User.objects.create_user(
        phone=phone,
        country="NG",
        password="pass1234",
        username=username,
        email=f"{username}@example.com",
    )


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


class CommunityDefaultCrudPermissionTests(TestCase):
    """
    Adversarial/direct-API coverage for the default update/partial_update/
    destroy actions on both viewsets - these previously had no object-level
    permission check at all beyond IsAuthenticated (any authenticated user
    could PATCH/DELETE any community or post). Hits the routes directly,
    not through any UI helper, and asserts actual DB state, not just the
    HTTP status code.
    """

    def setUp(self):
        self.client = APIClient()
        self.owner = _make_user("+2348120000001", "crud-owner")
        self.admin = _make_user("+2348120000002", "crud-admin")
        self.member = _make_user("+2348120000003", "crud-member")
        self.outsider = _make_user("+2348120000004", "crud-outsider")
        self.community = Community.objects.create(
            owner=self.owner, name="CRUD Community", slug="crud-community",
        )
        CommunityMembership.objects.create(community=self.community, user=self.owner, role=CommunityRole.OWNER)
        CommunityMembership.objects.create(community=self.community, user=self.admin, role=CommunityRole.ADMIN)
        CommunityMembership.objects.create(community=self.community, user=self.member, role=CommunityRole.MEMBER)

    def test_outsider_cannot_patch_community(self):
        self.client.force_authenticate(self.outsider)
        res = self.client.patch(
            f"/api/v1/communities/{self.community.id}/", {"name": "Hijacked"}, format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN, res.data)
        self.community.refresh_from_db()
        self.assertEqual(self.community.name, "CRUD Community")

    def test_plain_member_cannot_patch_community(self):
        self.client.force_authenticate(self.member)
        res = self.client.patch(
            f"/api/v1/communities/{self.community.id}/", {"name": "Hijacked"}, format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN, res.data)
        self.community.refresh_from_db()
        self.assertEqual(self.community.name, "CRUD Community")

    def test_admin_can_patch_community(self):
        self.client.force_authenticate(self.admin)
        res = self.client.patch(
            f"/api/v1/communities/{self.community.id}/", {"avatar_url": "https://example.com/a.jpg"}, format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.community.refresh_from_db()
        self.assertEqual(self.community.avatar_url, "https://example.com/a.jpg")

    def test_nobody_can_hard_delete_community_via_default_destroy(self):
        self.client.force_authenticate(self.owner)
        res = self.client.delete(f"/api/v1/communities/{self.community.id}/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN, res.data)
        self.assertTrue(Community.objects.filter(id=self.community.id).exists())

    def test_random_member_cannot_edit_another_users_post(self):
        post = CommunityPost.objects.create(
            community=self.community, author=self.owner, text_plain="Original", text_preview="Original",
        )
        self.client.force_authenticate(self.member)
        res = self.client.patch(f"/api/v1/posts/{post.id}/", {"text_plain": "Hijacked"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN, res.data)
        post.refresh_from_db()
        self.assertEqual(post.text_plain, "Original")

    def test_author_can_edit_own_post(self):
        post = CommunityPost.objects.create(
            community=self.community, author=self.member, text_plain="Original", text_preview="Original",
        )
        self.client.force_authenticate(self.member)
        # text_plain/text_preview are derived, read-only fields - the real
        # writable input is `text`, a rich-text doc (see
        # common.rich_text.process_rich_text_document / ALLOWED_NODES).
        # styled_text is popped by prepare_rich_text_attrs but is never a
        # declared serializer field, so it's silently dropped by DRF before
        # validate() ever runs - not a usable input via the real API.
        edited_doc = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Edited"}]}]}
        res = self.client.patch(f"/api/v1/posts/{post.id}/", {"text": edited_doc}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        post.refresh_from_db()
        self.assertEqual(post.text_plain, "Edited")

    def test_patch_fires_post_updated_realtime_event_exactly_once(self):
        # DRF's UpdateModelMixin.partial_update calls self.update(...)
        # internally, which resolves to CommunityPostViewSet's own update()
        # override - a naive notify call in both update() and
        # partial_update() double-fires on every single PATCH. Found via
        # live verification (two community.post_updated socket deliveries
        # for one edit request).
        post = CommunityPost.objects.create(
            community=self.community, author=self.member, text_plain="Original", text_preview="Original",
        )
        self.client.force_authenticate(self.member)
        edited_doc = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Edited"}]}]}
        with patch("apps.communities.realtime.notify_post_updated") as mock_notify:
            res = self.client.patch(f"/api/v1/posts/{post.id}/", {"text": edited_doc}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertEqual(mock_notify.call_count, 1)

    def test_admin_can_edit_someone_elses_post(self):
        post = CommunityPost.objects.create(
            community=self.community, author=self.member, text_plain="Original", text_preview="Original",
        )
        self.client.force_authenticate(self.admin)
        moderated_doc = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Moderated"}]}]}
        res = self.client.patch(f"/api/v1/posts/{post.id}/", {"text": moderated_doc}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        post.refresh_from_db()
        self.assertEqual(post.text_plain, "Moderated")

    def test_nobody_can_hard_delete_post_via_default_destroy(self):
        post = CommunityPost.objects.create(
            community=self.community, author=self.owner, text_plain="Keep me", text_preview="Keep me",
        )
        self.client.force_authenticate(self.owner)
        res = self.client.delete(f"/api/v1/posts/{post.id}/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN, res.data)
        self.assertTrue(CommunityPost.objects.filter(id=post.id).exists())


class CommunityMembershipLifecycleTests(TestCase):
    """
    Full membership state-machine coverage: join/leave/request-join/
    approve/reject/resubmit/remove/ban/unban, verifying actual
    CommunityMembership.status transitions in the DB - not just response
    status codes.
    """

    def setUp(self):
        self.client = APIClient()
        self.owner = _make_user("+2348130000001", "lifecycle-owner")
        self.user = _make_user("+2348130000002", "lifecycle-user")
        self.community = Community.objects.create(
            owner=self.owner,
            name="Lifecycle Community",
            slug="lifecycle-community",
            join_policy=CommunityJoinPolicy.OPEN,
        )
        CommunityMembership.objects.create(community=self.community, user=self.owner, role=CommunityRole.OWNER)

    def test_join_then_leave_transitions_status(self):
        self.client.force_authenticate(self.user)
        join_res = self.client.post(f"/api/v1/communities/{self.community.id}/join/", {}, format="json")
        self.assertEqual(join_res.status_code, status.HTTP_200_OK, join_res.data)
        membership = CommunityMembership.objects.get(community=self.community, user=self.user)
        self.assertEqual(membership.status, CommunityMembershipStatus.ACTIVE)

        leave_res = self.client.post(f"/api/v1/communities/{self.community.id}/leave/", {}, format="json")
        self.assertEqual(leave_res.status_code, status.HTTP_200_OK, leave_res.data)
        membership.refresh_from_db()
        self.assertEqual(membership.status, CommunityMembershipStatus.LEFT)
        self.assertFalse(
            CommunityMembership.objects.active().filter(community=self.community, user=self.user).exists()
        )

    def test_rejected_join_request_can_be_resubmitted(self):
        self.community.join_policy = CommunityJoinPolicy.REQUEST
        self.community.save(update_fields=["join_policy"])
        self.client.force_authenticate(self.user)

        first = self.client.post(f"/api/v1/communities/{self.community.id}/request-join/", {}, format="json")
        self.assertEqual(first.status_code, status.HTTP_200_OK, first.data)
        join_req = CommunityJoinRequest.objects.get(community=self.community, user=self.user)

        self.client.force_authenticate(self.owner)
        reject_res = self.client.post(
            f"/api/v1/communities/{self.community.id}/reject-request/",
            {"request_id": str(join_req.id)}, format="json",
        )
        self.assertEqual(reject_res.status_code, status.HTTP_200_OK, reject_res.data)
        join_req.refresh_from_db()
        self.assertEqual(join_req.status, CommunityJoinRequestStatus.REJECTED)

        # Resubmitting must reopen the SAME row (unique_together on
        # community+user), not error out or silently no-op.
        self.client.force_authenticate(self.user)
        second = self.client.post(f"/api/v1/communities/{self.community.id}/request-join/", {}, format="json")
        self.assertEqual(second.status_code, status.HTTP_200_OK, second.data)
        self.assertEqual(
            CommunityJoinRequest.objects.filter(community=self.community, user=self.user).count(), 1,
        )
        join_req.refresh_from_db()
        self.assertEqual(join_req.status, CommunityJoinRequestStatus.PENDING)

    def test_approve_request_activates_membership(self):
        self.community.join_policy = CommunityJoinPolicy.REQUEST
        self.community.save(update_fields=["join_policy"])
        self.client.force_authenticate(self.user)
        self.client.post(f"/api/v1/communities/{self.community.id}/request-join/", {}, format="json")
        join_req = CommunityJoinRequest.objects.get(community=self.community, user=self.user)

        self.client.force_authenticate(self.owner)
        approve_res = self.client.post(
            f"/api/v1/communities/{self.community.id}/approve-request/",
            {"request_id": str(join_req.id)}, format="json",
        )
        self.assertEqual(approve_res.status_code, status.HTTP_200_OK, approve_res.data)
        join_req.refresh_from_db()
        self.assertEqual(join_req.status, CommunityJoinRequestStatus.APPROVED)
        membership = CommunityMembership.objects.get(community=self.community, user=self.user)
        self.assertEqual(membership.status, CommunityMembershipStatus.ACTIVE)

    def test_remove_member_is_non_permanent_and_allows_rejoin(self):
        self.client.force_authenticate(self.user)
        self.client.post(f"/api/v1/communities/{self.community.id}/join/", {}, format="json")

        self.client.force_authenticate(self.owner)
        remove_res = self.client.post(
            f"/api/v1/communities/{self.community.id}/members/remove/",
            {"user_id": str(self.user.id)}, format="json",
        )
        self.assertEqual(remove_res.status_code, status.HTTP_200_OK, remove_res.data)
        membership = CommunityMembership.objects.get(community=self.community, user=self.user)
        self.assertEqual(membership.status, CommunityMembershipStatus.REMOVED)
        self.assertFalse(CommunityBan.objects.filter(community=self.community, user=self.user).exists())

        # A removed (not banned) user can rejoin freely.
        self.client.force_authenticate(self.user)
        rejoin_res = self.client.post(f"/api/v1/communities/{self.community.id}/join/", {}, format="json")
        self.assertEqual(rejoin_res.status_code, status.HTTP_200_OK, rejoin_res.data)
        membership.refresh_from_db()
        self.assertEqual(membership.status, CommunityMembershipStatus.ACTIVE)

    def test_ban_blocks_rejoin_until_unbanned(self):
        self.client.force_authenticate(self.user)
        self.client.post(f"/api/v1/communities/{self.community.id}/join/", {}, format="json")

        self.client.force_authenticate(self.owner)
        ban_res = self.client.post(
            f"/api/v1/communities/{self.community.id}/ban/",
            {"user_id": str(self.user.id)}, format="json",
        )
        self.assertEqual(ban_res.status_code, status.HTTP_200_OK, ban_res.data)
        membership = CommunityMembership.objects.get(community=self.community, user=self.user)
        self.assertEqual(membership.status, CommunityMembershipStatus.BANNED)
        self.assertTrue(CommunityBan.objects.filter(community=self.community, user=self.user).exists())

        # Banned user cannot rejoin.
        self.client.force_authenticate(self.user)
        blocked_rejoin = self.client.post(f"/api/v1/communities/{self.community.id}/join/", {}, format="json")
        self.assertEqual(blocked_rejoin.status_code, status.HTTP_403_FORBIDDEN, blocked_rejoin.data)
        membership.refresh_from_db()
        self.assertEqual(membership.status, CommunityMembershipStatus.BANNED)

        # Also excluded from the active members list while banned.
        self.client.force_authenticate(self.owner)
        members_res = self.client.get(f"/api/v1/communities/{self.community.id}/members/")
        member_user_ids = {m["user"]["id"] for m in members_res.data}
        self.assertNotIn(str(self.user.id), member_user_ids)

        # Unban lifts the ban and allows rejoining, but does not silently
        # restore membership on its own (BANNED -> LEFT, not ACTIVE).
        unban_res = self.client.post(
            f"/api/v1/communities/{self.community.id}/unban/",
            {"user_id": str(self.user.id)}, format="json",
        )
        self.assertEqual(unban_res.status_code, status.HTTP_200_OK, unban_res.data)
        self.assertFalse(CommunityBan.objects.filter(community=self.community, user=self.user).exists())
        membership.refresh_from_db()
        self.assertEqual(membership.status, CommunityMembershipStatus.LEFT)

        self.client.force_authenticate(self.user)
        rejoin_res = self.client.post(f"/api/v1/communities/{self.community.id}/join/", {}, format="json")
        self.assertEqual(rejoin_res.status_code, status.HTTP_200_OK, rejoin_res.data)
        membership.refresh_from_db()
        self.assertEqual(membership.status, CommunityMembershipStatus.ACTIVE)


class CommunityRoleManagementTests(TestCase):
    """
    Covers promote/demote via the canonical members/set-role endpoint
    (what CommunityInfoPage.tsx now actually calls), including the
    guardrails around the owner role.
    """

    def setUp(self):
        self.client = APIClient()
        self.owner = _make_user("+2348140000001", "role-owner")
        self.member = _make_user("+2348140000002", "role-member")
        self.outsider_member = _make_user("+2348140000003", "role-outsider-member")
        self.community = Community.objects.create(
            owner=self.owner, name="Role Community", slug="role-community",
        )
        CommunityMembership.objects.create(community=self.community, user=self.owner, role=CommunityRole.OWNER)
        CommunityMembership.objects.create(community=self.community, user=self.member, role=CommunityRole.MEMBER)
        CommunityMembership.objects.create(
            community=self.community, user=self.outsider_member, role=CommunityRole.MEMBER,
        )

    def test_owner_can_promote_and_demote_member(self):
        self.client.force_authenticate(self.owner)
        promote_res = self.client.post(
            f"/api/v1/communities/{self.community.id}/members/set-role/",
            {"user_id": str(self.member.id), "role": "admin"}, format="json",
        )
        self.assertEqual(promote_res.status_code, status.HTTP_200_OK, promote_res.data)
        membership = CommunityMembership.objects.get(community=self.community, user=self.member)
        self.assertEqual(membership.role, CommunityRole.ADMIN)

        demote_res = self.client.post(
            f"/api/v1/communities/{self.community.id}/members/set-role/",
            {"user_id": str(self.member.id), "role": "member"}, format="json",
        )
        self.assertEqual(demote_res.status_code, status.HTTP_200_OK, demote_res.data)
        membership.refresh_from_db()
        self.assertEqual(membership.role, CommunityRole.MEMBER)

    def test_plain_member_cannot_change_roles(self):
        self.client.force_authenticate(self.outsider_member)
        res = self.client.post(
            f"/api/v1/communities/{self.community.id}/members/set-role/",
            {"user_id": str(self.member.id), "role": "admin"}, format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN, res.data)
        membership = CommunityMembership.objects.get(community=self.community, user=self.member)
        self.assertEqual(membership.role, CommunityRole.MEMBER)

    def test_owner_role_cannot_be_modified_or_assigned(self):
        self.client.force_authenticate(self.owner)
        demote_owner_res = self.client.post(
            f"/api/v1/communities/{self.community.id}/members/set-role/",
            {"user_id": str(self.owner.id), "role": "member"}, format="json",
        )
        self.assertEqual(demote_owner_res.status_code, status.HTTP_400_BAD_REQUEST, demote_owner_res.data)

        assign_owner_res = self.client.post(
            f"/api/v1/communities/{self.community.id}/members/set-role/",
            {"user_id": str(self.member.id), "role": "owner"}, format="json",
        )
        self.assertEqual(assign_owner_res.status_code, status.HTTP_400_BAD_REQUEST, assign_owner_res.data)
        membership = CommunityMembership.objects.get(community=self.community, user=self.member)
        self.assertEqual(membership.role, CommunityRole.MEMBER)


class CommunityNotificationTriggerTests(TestCase):
    """
    Asserts an actual Notification row is created for each Community
    lifecycle trigger, not merely that the triggering action returned
    success - a gap in the original Phase 5 pass that let a real bug
    through: the `ban` action was missing its persistent-notification
    call entirely (only `block_member` had it), found via live
    verification and fixed alongside these tests.
    """

    def setUp(self):
        self.client = APIClient()
        self.owner = _make_user("+2348150000001", "notif-owner")
        self.member = _make_user("+2348150000002", "notif-member")
        self.requester = _make_user("+2348150000003", "notif-requester")
        self.community = Community.objects.create(
            owner=self.owner,
            name="Notification Community",
            slug="notification-community",
            join_policy=CommunityJoinPolicy.REQUEST,
        )
        CommunityMembership.objects.create(community=self.community, user=self.owner, role=CommunityRole.OWNER)
        CommunityMembership.objects.create(community=self.community, user=self.member, role=CommunityRole.MEMBER)

    def test_join_request_created_notifies_admins_only(self):
        self.client.force_authenticate(self.requester)
        res = self.client.post(
            f"/api/v1/communities/{self.community.id}/request-join/", {}, format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)

        self.assertTrue(
            Notification.objects.filter(
                user_id=self.owner.id, type="community.join_request.created",
            ).exists()
        )
        # Plain (non-admin) member must NOT be notified of join requests.
        self.assertFalse(
            Notification.objects.filter(
                user_id=self.member.id, type="community.join_request.created",
            ).exists()
        )

    def test_join_request_decided_notifies_requester(self):
        self.client.force_authenticate(self.requester)
        self.client.post(f"/api/v1/communities/{self.community.id}/request-join/", {}, format="json")
        join_req = CommunityJoinRequest.objects.get(community=self.community, user=self.requester)

        self.client.force_authenticate(self.owner)
        res = self.client.post(
            f"/api/v1/communities/{self.community.id}/approve-request/",
            {"request_id": str(join_req.id)}, format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)

        notif = Notification.objects.filter(
            user_id=self.requester.id, type="community.join_request.decided",
        ).first()
        self.assertIsNotNone(notif)

    def test_role_change_notifies_affected_member(self):
        self.client.force_authenticate(self.owner)
        res = self.client.post(
            f"/api/v1/communities/{self.community.id}/members/set-role/",
            {"user_id": str(self.member.id), "role": "admin"}, format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)

        self.assertTrue(
            Notification.objects.filter(
                user_id=self.member.id, type="community.role_changed",
            ).exists()
        )

    def test_removal_notifies_affected_member(self):
        self.client.force_authenticate(self.owner)
        res = self.client.post(
            f"/api/v1/communities/{self.community.id}/members/remove/",
            {"user_id": str(self.member.id)}, format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)

        self.assertTrue(
            Notification.objects.filter(
                user_id=self.member.id, type="community.member_removed",
            ).exists()
        )

    def test_ban_notifies_affected_member(self):
        self.client.force_authenticate(self.owner)
        res = self.client.post(
            f"/api/v1/communities/{self.community.id}/ban/",
            {"user_id": str(self.member.id)}, format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)

        notif = Notification.objects.filter(
            user_id=self.member.id, type="community.member_banned",
        ).first()
        self.assertIsNotNone(notif)
        # No moderation detail (reason, banned_by) leaked into the
        # notification body shown to the banned user.
        self.assertNotIn("reason", notif.body.lower())

    def test_block_member_also_notifies_affected_member(self):
        # members/block is the second ban entrypoint (_ban_user's other
        # caller) - must independently trigger the same notification,
        # not just the /ban/ action.
        self.client.force_authenticate(self.owner)
        res = self.client.post(
            f"/api/v1/communities/{self.community.id}/members/block/",
            {"user_id": str(self.member.id)}, format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)

        self.assertTrue(
            Notification.objects.filter(
                user_id=self.member.id, type="community.member_banned",
            ).exists()
        )
