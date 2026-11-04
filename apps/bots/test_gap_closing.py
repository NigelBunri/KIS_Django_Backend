# apps/bots/test_gap_closing.py
"""
Tests for the Phase 2 gap-closing work: real event publishers across
Communities/Partners/Channels/My Feed/Direct Messages, the new
remove_member/mute_member/unmute_member/unban_member action executors,
the extended condition engine (numeric/empty/date/time-window ops), and
the extended scheduler (cron + timezone + duplicate/missed-schedule
safety). Every publisher test drives the REAL HTTP view, not the
engine directly - proving the actual production code path works, not
just the engine in isolation.
"""
import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.tiers import ensure_default_account_tiers
from apps.chat.models import BaseConversationRole, Conversation, ConversationMember, ConversationType
from apps.communities.models import Community, CommunityMembership, CommunityRole
from apps.communities.services import create_community_with_main_conversation
from apps.groups.models import GroupBan, GroupMembership, GroupRole
from apps.groups.services import create_group_with_conversation
from apps.partners.models import Partner, PartnerMembership, PartnerMembershipStatus

from . import services
from .models import (
    BotApprovalRequest,
    BotKind,
    BotPermissionScope,
    BotResourceType,
    BotStatus,
    BotWorkflowRun,
    BotWorkflowRunStatus,
    BotWorkflowStatus,
    BotWorkflowTriggerType,
)
from .test_workflows import _make_user, _uuid_suffix
from .workflow_engine import dispatch_event_to_workflows, is_schedule_due, validate_definition, validate_trigger_config


def _active_bot(owner):
    bot = services.create_bot(owner, name=f"GapBot-{_uuid_suffix()}", kind=BotKind.CUSTOM)
    services.set_status(bot, owner, BotStatus.ACTIVE)
    return bot


def _published_workflow(bot, owner, grant, *, trigger_type, definition, trigger_config=None):
    workflow = services.create_workflow(
        bot, owner, resource_grant_id=grant.id, name=f"WF-{_uuid_suffix()}",
        trigger_type=trigger_type, definition=definition, trigger_config=trigger_config,
    )
    return services.publish_workflow(workflow, owner)


class CommunityEventPublisherTests(TestCase):
    """Real HTTP paths: CommunityViewSet.join/leave/remove_member/ban,
    CommunityPostViewSet.create/comment."""

    def setUp(self):
        cache.clear()
        ensure_default_account_tiers()
        self.partner_owner = _make_user("gap-cp-owner@example.com")
        self.partner = Partner.objects.create(owner=self.partner_owner, name="GapP", slug=f"gapp-{_uuid_suffix()}")
        self.owner = _make_user("gap-community-owner@example.com", tier_name="Business")
        self.community = create_community_with_main_conversation(
            owner=self.owner, partner=self.partner, name="GapComm", slug=f"gapcomm-{_uuid_suffix()}",
        )
        from apps.communities.models import CommunityJoinPolicy

        self.community.join_policy = CommunityJoinPolicy.OPEN
        self.community.save(update_fields=["join_policy"])
        self.bot = _active_bot(self.owner)
        self.grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.COMMUNITY, resource_id=str(self.community.id),
            scopes=[BotPermissionScope.SEND_MESSAGES, BotPermissionScope.READ_MESSAGES, BotPermissionScope.READ_MEMBERS],
        )
        self.client = APIClient()

    def test_real_http_join_triggers_member_joined(self):
        workflow = _published_workflow(
            self.bot, self.owner, self.grant, trigger_type=BotWorkflowTriggerType.MEMBER_JOINED,
            definition={"then_actions": [{"type": "send_message", "params": {"text": "Welcome!"}}]},
        )
        new_member = _make_user("gap-community-new-member@example.com")
        self.client.force_authenticate(user=new_member)
        with patch("apps.chat.tasks.send_system_message_as_user", return_value={"ok": True}) as mock_send:
            response = self.client.post(f"/api/v1/communities/{self.community.id}/join/")
        self.assertEqual(response.status_code, 200)
        mock_send.assert_called_once()
        self.assertTrue(BotWorkflowRun.objects.filter(workflow=workflow, status=BotWorkflowRunStatus.SUCCEEDED).exists())

    def test_real_http_leave_triggers_member_left(self):
        workflow = _published_workflow(
            self.bot, self.owner, self.grant, trigger_type=BotWorkflowTriggerType.MEMBER_LEFT,
            definition={"then_actions": [{"type": "send_message", "params": {"text": "Bye!"}}]},
        )
        leaving = _make_user("gap-community-leaving@example.com")
        CommunityMembership.objects.create(community=self.community, user=leaving, role=CommunityRole.MEMBER)
        self.client.force_authenticate(user=leaving)
        with patch("apps.chat.tasks.send_system_message_as_user", return_value={"ok": True}) as mock_send:
            response = self.client.post(f"/api/v1/communities/{self.community.id}/leave/")
        self.assertEqual(response.status_code, 200)
        mock_send.assert_called_once()
        self.assertTrue(BotWorkflowRun.objects.filter(workflow=workflow, status=BotWorkflowRunStatus.SUCCEEDED).exists())

    def test_real_http_post_create_triggers_post_created(self):
        workflow = _published_workflow(
            self.bot, self.owner, self.grant, trigger_type=BotWorkflowTriggerType.POST_CREATED,
            definition={"then_actions": [{"type": "send_message", "params": {"text": "New post!"}}]},
        )
        author = _make_user("gap-community-author@example.com")
        CommunityMembership.objects.create(community=self.community, user=author, role=CommunityRole.MEMBER)
        self.client.force_authenticate(user=author)
        with patch("apps.chat.tasks.send_system_message_as_user", return_value={"ok": True}) as mock_send:
            response = self.client.post(
                "/api/v1/posts/", {"community": str(self.community.id), "text_plain": "hello world"}, format="json",
            )
        self.assertEqual(response.status_code, 201)
        mock_send.assert_called_once()
        self.assertTrue(BotWorkflowRun.objects.filter(workflow=workflow, status=BotWorkflowRunStatus.SUCCEEDED).exists())

    def test_real_http_comment_triggers_comment_created(self):
        workflow = _published_workflow(
            self.bot, self.owner, self.grant, trigger_type=BotWorkflowTriggerType.COMMENT_CREATED,
            definition={"then_actions": [{"type": "send_message", "params": {"text": "New comment!"}}]},
        )
        from apps.communities.models import CommunityPost, CommunityPostStatus

        post = CommunityPost.objects.create(
            community=self.community, author=self.owner, text_plain="post", status=CommunityPostStatus.PUBLISHED,
        )
        commenter = _make_user("gap-community-commenter@example.com")
        CommunityMembership.objects.create(community=self.community, user=commenter, role=CommunityRole.MEMBER)
        self.client.force_authenticate(user=commenter)
        with patch("apps.chat.tasks.send_system_message_as_user", return_value={"ok": True}) as mock_send:
            response = self.client.post(f"/api/v1/posts/{post.id}/comment/", {"text": "nice post"}, format="json")
        self.assertEqual(response.status_code, 201)
        mock_send.assert_called_once()
        self.assertTrue(BotWorkflowRun.objects.filter(workflow=workflow, status=BotWorkflowRunStatus.SUCCEEDED).exists())


class PartnerEventPublisherTests(TestCase):
    """Real HTTP paths: PartnerViewSet.subscribe/moderate_member,
    PartnerPostViewSet.create/comment."""

    def setUp(self):
        cache.clear()
        ensure_default_account_tiers()
        self.owner = _make_user("gap-partner-owner@example.com", tier_name="Business")
        conversation = Conversation.objects.create(type=ConversationType.GROUP, title="P", created_by=self.owner)
        ConversationMember.objects.create(conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER)
        self.partner = Partner.objects.create(
            owner=self.owner, name="GapPartner", slug=f"gappartner-{_uuid_suffix()}", main_conversation=conversation,
        )
        self.bot = _active_bot(self.owner)
        self.grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.PARTNER, resource_id=str(self.partner.id),
            scopes=[BotPermissionScope.SEND_MESSAGES, BotPermissionScope.READ_MESSAGES, BotPermissionScope.READ_MEMBERS],
        )
        self.client = APIClient()

    def test_real_http_subscribe_triggers_member_joined(self):
        workflow = _published_workflow(
            self.bot, self.owner, self.grant, trigger_type=BotWorkflowTriggerType.MEMBER_JOINED,
            definition={"then_actions": [{"type": "send_message", "params": {"text": "Welcome!"}}]},
        )
        new_member = _make_user("gap-partner-new-member@example.com")
        self.client.force_authenticate(user=new_member)
        with patch("apps.chat.tasks.send_system_message_as_user", return_value={"ok": True}) as mock_send:
            response = self.client.post(f"/api/v1/partners/{self.partner.id}/subscribe/")
        self.assertEqual(response.status_code, 200)
        mock_send.assert_called_once()
        self.assertTrue(BotWorkflowRun.objects.filter(workflow=workflow, status=BotWorkflowRunStatus.SUCCEEDED).exists())

    def test_real_http_kick_triggers_member_left(self):
        workflow = _published_workflow(
            self.bot, self.owner, self.grant, trigger_type=BotWorkflowTriggerType.MEMBER_LEFT,
            definition={"then_actions": [{"type": "send_message", "params": {"text": "Removed."}}]},
        )
        target = _make_user("gap-partner-kick-target@example.com")
        PartnerMembership.objects.create(partner=self.partner, user=target, status=PartnerMembershipStatus.SUBSCRIBER, role="subscriber")
        self.client.force_authenticate(user=self.owner)
        with patch("apps.chat.tasks.send_system_message_as_user", return_value={"ok": True}) as mock_send:
            response = self.client.post(
                f"/api/v1/partners/{self.partner.id}/members/{target.id}/moderate/", {"action": "kick"}, format="json",
            )
        self.assertEqual(response.status_code, 200)
        mock_send.assert_called_once()
        self.assertTrue(BotWorkflowRun.objects.filter(workflow=workflow, status=BotWorkflowRunStatus.SUCCEEDED).exists())

    def test_real_http_post_create_triggers_post_created(self):
        workflow = _published_workflow(
            self.bot, self.owner, self.grant, trigger_type=BotWorkflowTriggerType.POST_CREATED,
            definition={"then_actions": [{"type": "send_message", "params": {"text": "New post!"}}]},
        )
        self.client.force_authenticate(user=self.owner)
        with patch("apps.chat.tasks.send_system_message_as_user", return_value={"ok": True}) as mock_send:
            response = self.client.post(
                "/api/v1/partners/posts/", {"partner": str(self.partner.id), "text_plain": "hello"}, format="json",
            )
        self.assertEqual(response.status_code, 201)
        mock_send.assert_called_once()
        self.assertTrue(BotWorkflowRun.objects.filter(workflow=workflow, status=BotWorkflowRunStatus.SUCCEEDED).exists())


class ChannelEventPublisherTests(TestCase):
    """Real HTTP paths: ChannelMembershipView (free-tier join/cancel),
    BroadcastChannelContentListCreateView + ChannelContentPublishView."""

    def setUp(self):
        cache.clear()
        ensure_default_account_tiers()
        self.owner = _make_user("gap-channel-owner@example.com", tier_name="Business")
        from apps.broadcasts.models import BroadcastChannel, ChannelMembershipTier

        self.channel = BroadcastChannel.objects.create(
            owner_type="user", owner_id=self.owner.id, owner_user=self.owner,
            handle=f"gapch-{_uuid_suffix()}", display_name="Gap Channel",
        )
        self.tier = ChannelMembershipTier.objects.create(
            channel=self.channel, title="Free", price_cents=0, currency="USD",
        )
        self.bot = _active_bot(self.owner)
        self.grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.CHANNEL, resource_id=str(self.channel.id),
            scopes=[BotPermissionScope.SEND_MESSAGES, BotPermissionScope.READ_MESSAGES, BotPermissionScope.READ_MEMBERS],
        )
        self.client = APIClient()

    def test_real_http_free_tier_join_triggers_member_joined(self):
        # Channels have no backing Conversation (BroadcastChannel never
        # sets grant.conversation_id - see _resolve_conversation_id_for_
        # resource), so send_message is correctly unsupported here; this
        # proves the event -> trigger -> condition path instead, using an
        # empty action list so there's nothing resource-specific to fail.
        workflow = _published_workflow(
            self.bot, self.owner, self.grant, trigger_type=BotWorkflowTriggerType.MEMBER_JOINED,
            definition={"then_actions": []},
        )
        new_member = _make_user("gap-channel-new-member@example.com")
        self.client.force_authenticate(user=new_member)
        response = self.client.post(
            f"/api/v1/broadcasts/channels/{self.channel.id}/membership/", {"tier_id": str(self.tier.id)}, format="json",
        )
        self.assertIn(response.status_code, (200, 201))
        run = BotWorkflowRun.objects.filter(workflow=workflow).first()
        self.assertIsNotNone(run)
        self.assertEqual(run.status, BotWorkflowRunStatus.SUCCEEDED)
        self.assertTrue(run.condition_result)
        self.assertEqual(run.event_context.get("member", {}).get("id"), str(new_member.id))

    def test_real_http_cancel_triggers_member_left(self):
        workflow = _published_workflow(
            self.bot, self.owner, self.grant, trigger_type=BotWorkflowTriggerType.MEMBER_LEFT,
            definition={"then_actions": []},
        )
        member = _make_user("gap-channel-leaving@example.com")
        self.client.force_authenticate(user=member)
        self.client.post(f"/api/v1/broadcasts/channels/{self.channel.id}/membership/", {"tier_id": str(self.tier.id)}, format="json")
        response = self.client.delete(
            f"/api/v1/broadcasts/channels/{self.channel.id}/membership/", {"tier_id": str(self.tier.id)}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        run = BotWorkflowRun.objects.filter(workflow=workflow).first()
        self.assertIsNotNone(run)
        self.assertEqual(run.status, BotWorkflowRunStatus.SUCCEEDED)
        self.assertTrue(run.condition_result)
        self.assertEqual(run.event_context.get("member", {}).get("id"), str(member.id))

    def test_real_http_publish_triggers_post_created(self):
        workflow = _published_workflow(
            self.bot, self.owner, self.grant, trigger_type=BotWorkflowTriggerType.POST_CREATED,
            definition={"then_actions": []},
        )
        self.client.force_authenticate(user=self.owner)
        create_res = self.client.post(
            f"/api/v1/broadcasts/channels/{self.channel.id}/contents/",
            {"content_type": "text", "title": "Hello", "text_plain": "Hello world"}, format="json",
        )
        self.assertEqual(create_res.status_code, 201)
        content_id = create_res.json()["id"]
        publish_res = self.client.post(f"/api/v1/broadcasts/channel-contents/{content_id}/publish/")
        self.assertEqual(publish_res.status_code, 200)
        run = BotWorkflowRun.objects.filter(workflow=workflow).first()
        self.assertIsNotNone(run)
        self.assertEqual(run.status, BotWorkflowRunStatus.SUCCEEDED)
        self.assertTrue(run.condition_result)
        self.assertEqual(run.event_context.get("post", {}).get("id"), content_id)


class FeedEventPublisherTests(TestCase):
    """Real HTTP path: BroadcastFeedEntriesView.post (My Feed)."""

    def setUp(self):
        cache.clear()
        ensure_default_account_tiers()
        self.owner = _make_user("gap-feed-owner@example.com", tier_name="Business")
        self.bot = _active_bot(self.owner)
        self.grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.FEED_PROFILE, resource_id=str(self.owner.id),
            scopes=[BotPermissionScope.SEND_MESSAGES, BotPermissionScope.READ_MESSAGES],
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def test_real_http_feed_post_triggers_post_created(self):
        # Feed profiles have no backing Conversation either (My Feed
        # entries are JSON blobs on BroadcastFeedProfile.feeds, not chat
        # messages), so send_message is correctly unsupported here too -
        # see the ChannelEventPublisherTests comment for the same reasoning.
        workflow = _published_workflow(
            self.bot, self.owner, self.grant, trigger_type=BotWorkflowTriggerType.POST_CREATED,
            definition={"then_actions": []},
        )
        response = self.client.post(
            "/api/v1/broadcasts/profiles/feeds/", {"title": "My update", "summary": "Hello feed"}, format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        post_id = response.json()["feed"]["id"]
        run = BotWorkflowRun.objects.filter(workflow=workflow).first()
        self.assertIsNotNone(run)
        self.assertEqual(run.status, BotWorkflowRunStatus.SUCCEEDED)
        self.assertTrue(run.condition_result)
        self.assertEqual(run.event_context.get("post", {}).get("id"), str(post_id))


class DirectMessageTriggerTests(TestCase):
    """Confirms message.created/edited already fires for a DIRECT_
    CONVERSATION grant through the existing dispatch_bot_events_for_
    conversation path - no new publisher code, this is verification
    only per the audit's conclusion that Direct Messages already work."""

    def setUp(self):
        cache.clear()
        ensure_default_account_tiers()
        self.owner = _make_user("gap-dm-owner@example.com", tier_name="Business")
        self.other = _make_user("gap-dm-other@example.com")
        self.conversation = Conversation.objects.create(type=ConversationType.DIRECT, title="DM", created_by=self.owner)
        ConversationMember.objects.create(conversation=self.conversation, user=self.owner, base_role=BaseConversationRole.OWNER)
        ConversationMember.objects.create(conversation=self.conversation, user=self.other, base_role=BaseConversationRole.MEMBER)
        self.bot = _active_bot(self.owner)
        self.grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.DIRECT_CONVERSATION, resource_id=str(self.conversation.id),
            scopes=[BotPermissionScope.SEND_MESSAGES, BotPermissionScope.READ_MESSAGES],
        )

    def test_message_created_fires_for_direct_conversation_grant(self):
        self.assertEqual(self.grant.conversation_id, self.conversation.id)
        workflow = _published_workflow(
            self.bot, self.owner, self.grant, trigger_type=BotWorkflowTriggerType.MESSAGE_CREATED,
            definition={"then_actions": [{"type": "send_message", "params": {"text": "auto-reply"}}]},
        )
        with patch("apps.chat.tasks.send_system_message_as_user", return_value={"ok": True}) as mock_send:
            services.dispatch_bot_events_for_conversation(
                self.conversation.id, "message.created", {"text": "hi", "senderId": str(self.other.id)},
                event_id="dm-evt-1",
            )
        mock_send.assert_called_once()
        self.assertTrue(BotWorkflowRun.objects.filter(workflow=workflow, status=BotWorkflowRunStatus.SUCCEEDED).exists())


class NewActionExecutorTests(TestCase):
    """unban_member/remove_member/mute_member/unmute_member - approval
    gating for the restrictive ones, immediate execution for the
    corrective ones."""

    def setUp(self):
        cache.clear()
        ensure_default_account_tiers()
        self.owner = _make_user("gap-actions-owner@example.com", tier_name="Business")
        self.group = create_group_with_conversation(owner=self.owner, name="GapG", slug=f"gapg-{_uuid_suffix()}")
        self.target = _make_user("gap-actions-target@example.com")
        GroupMembership.objects.create(group=self.group, user=self.target, role=GroupRole.MEMBER)
        self.bot = _active_bot(self.owner)
        self.grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            scopes=[BotPermissionScope.MODERATE],
        )

    def test_remove_member_requires_approval_then_executes(self):
        result = services.execute_bot_action(self.bot, self.grant, "remove_member", {"user_id": str(self.target.id)})
        self.assertEqual(result["status"], "pending_approval")
        self.assertTrue(GroupMembership.objects.get(group=self.group, user=self.target).left_at is None)
        approval = BotApprovalRequest.objects.get(id=result["approval_id"])
        services.decide_approval(approval, self.owner, True)
        self.assertIsNotNone(GroupMembership.objects.get(group=self.group, user=self.target).left_at)

    def test_unban_member_executes_immediately_no_approval(self):
        GroupBan.objects.create(group=self.group, user=self.target)
        GroupMembership.objects.filter(group=self.group, user=self.target).update(is_banned=True)
        before = BotApprovalRequest.objects.count()
        result = services.execute_bot_action(self.bot, self.grant, "unban_member", {"user_id": str(self.target.id)})
        self.assertEqual(result["status"], "executed")
        self.assertEqual(BotApprovalRequest.objects.count(), before)
        self.assertFalse(GroupBan.objects.filter(group=self.group, user=self.target).exists())

    def test_mute_member_is_partner_only(self):
        # mute_member is sensitive, so execute_bot_action queues approval
        # before it ever reaches _execute_mute_member's own resource_type
        # check - the resource-type rejection only surfaces once the
        # approval is actually decided and the executor runs.
        result = services.execute_bot_action(self.bot, self.grant, "mute_member", {"user_id": str(self.target.id)})
        self.assertEqual(result["status"], "pending_approval")
        approval = BotApprovalRequest.objects.get(id=result["approval_id"])
        services.decide_approval(approval, self.owner, True)
        approval.refresh_from_db()
        self.assertTrue(approval.execution_error)

    def test_mute_and_unmute_member_on_partner(self):
        conversation = Conversation.objects.create(type=ConversationType.GROUP, title="P", created_by=self.owner)
        ConversationMember.objects.create(conversation=conversation, user=self.owner, base_role=BaseConversationRole.OWNER)
        partner = Partner.objects.create(owner=self.owner, name="GapMuteP", slug=f"gapmutep-{_uuid_suffix()}", main_conversation=conversation)
        PartnerMembership.objects.create(partner=partner, user=self.target, status=PartnerMembershipStatus.SUBSCRIBER, role="subscriber")
        partner_grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.PARTNER, resource_id=str(partner.id),
            scopes=[BotPermissionScope.MODERATE],
        )
        result = services.execute_bot_action(self.bot, partner_grant, "mute_member", {"user_id": str(self.target.id)})
        self.assertEqual(result["status"], "pending_approval")
        approval = BotApprovalRequest.objects.get(id=result["approval_id"])
        services.decide_approval(approval, self.owner, True)
        self.assertTrue(PartnerMembership.objects.get(partner=partner, user=self.target).is_muted)

        unmute_result = services.execute_bot_action(self.bot, partner_grant, "unmute_member", {"user_id": str(self.target.id)})
        self.assertEqual(unmute_result["status"], "executed")
        self.assertFalse(PartnerMembership.objects.get(partner=partner, user=self.target).is_muted)


class ConditionOperatorTests(TestCase):
    def test_numeric_ops(self):
        from .workflow_engine import evaluate_conditions

        ctx = {"score": 10}
        self.assertTrue(evaluate_conditions({"rules": [{"field": "score", "op": "gt", "value": 5}]}, ctx))
        self.assertFalse(evaluate_conditions({"rules": [{"field": "score", "op": "lt", "value": 5}]}, ctx))
        self.assertTrue(evaluate_conditions({"rules": [{"field": "score", "op": "gte", "value": 10}]}, ctx))
        self.assertTrue(evaluate_conditions({"rules": [{"field": "score", "op": "lte", "value": 10}]}, ctx))

    def test_numeric_op_against_wrong_type_is_false_not_a_crash(self):
        from .workflow_engine import evaluate_conditions

        self.assertFalse(evaluate_conditions({"rules": [{"field": "x", "op": "gt", "value": 5}]}, {"x": {"nested": True}}))
        self.assertFalse(evaluate_conditions({"rules": [{"field": "missing", "op": "gt", "value": 5}]}, {}))

    def test_empty_ops(self):
        from .workflow_engine import evaluate_conditions

        self.assertTrue(evaluate_conditions({"rules": [{"field": "x", "op": "is_empty"}]}, {"x": ""}))
        self.assertTrue(evaluate_conditions({"rules": [{"field": "missing", "op": "is_empty"}]}, {}))
        self.assertTrue(evaluate_conditions({"rules": [{"field": "x", "op": "is_not_empty"}]}, {"x": "hi"}))

    def test_date_ops(self):
        from .workflow_engine import evaluate_conditions

        now = timezone.now()
        earlier = (now - datetime.timedelta(days=1)).isoformat()
        later = (now + datetime.timedelta(days=1)).isoformat()
        self.assertTrue(evaluate_conditions({"rules": [{"field": "t", "op": "before", "value": later}]}, {"t": now.isoformat()}))
        self.assertTrue(evaluate_conditions({"rules": [{"field": "t", "op": "after", "value": earlier}]}, {"t": now.isoformat()}))

    def test_within_last_minutes(self):
        from .workflow_engine import evaluate_conditions

        now_iso = timezone.now().isoformat()
        old_iso = (timezone.now() - datetime.timedelta(hours=3)).isoformat()
        self.assertTrue(evaluate_conditions({"rules": [{"field": "t", "op": "within_last_minutes", "value": 5}]}, {"t": now_iso}))
        self.assertFalse(evaluate_conditions({"rules": [{"field": "t", "op": "within_last_hours", "value": 1}]}, {"t": old_iso}))

    def test_malformed_definition_never_crashes_validation_or_evaluation(self):
        from .workflow_engine import evaluate_conditions

        with self.assertRaises(Exception):
            validate_definition({"conditions": {"rules": [{"field": "x", "op": "nonexistent_op", "value": 1}]}})
        # evaluate_conditions itself must still degrade safely even if
        # something slipped past validation (e.g. an older stored
        # workflow) - never raise, never crash the execution worker.
        result = evaluate_conditions({"rules": [{"field": "x", "op": "totally_bogus", "value": 1}]}, {"x": 1})
        self.assertFalse(result)


class SchedulingTests(TestCase):
    def setUp(self):
        cache.clear()
        ensure_default_account_tiers()

    def test_interval_config_requires_valid_range(self):
        with self.assertRaises(Exception):
            validate_trigger_config("schedule", {"interval_minutes": 1})
        cfg = validate_trigger_config("schedule", {"interval_minutes": 30})
        self.assertEqual(cfg["interval_minutes"], 30)
        self.assertEqual(cfg["timezone"], "UTC")

    def test_cron_config_rejects_malformed_expression(self):
        with self.assertRaises(Exception):
            validate_trigger_config("schedule", {"cron": "not a cron"})
        with self.assertRaises(Exception):
            validate_trigger_config("schedule", {"cron": "99 99 * * *"})

    def test_cron_config_rejects_bad_timezone(self):
        with self.assertRaises(Exception):
            validate_trigger_config("schedule", {"cron": "0 9 * * *", "timezone": "Not/AZone"})

    def test_interval_is_schedule_due(self):
        cfg = {"interval_minutes": 10, "timezone": "UTC"}
        now = timezone.now()
        self.assertTrue(is_schedule_due(cfg, None, now))
        self.assertFalse(is_schedule_due(cfg, now - datetime.timedelta(minutes=5), now))
        self.assertTrue(is_schedule_due(cfg, now - datetime.timedelta(minutes=11), now))

    def test_cron_is_schedule_due_respects_weekday_and_timezone(self):
        cfg = validate_trigger_config("schedule", {"cron": "0 9 * * 1-5", "timezone": "America/New_York"})
        ny = ZoneInfo("America/New_York")
        monday_9am = datetime.datetime(2026, 11, 2, 9, 0, tzinfo=ny).astimezone(datetime.timezone.utc)
        monday_10am = datetime.datetime(2026, 11, 2, 10, 0, tzinfo=ny).astimezone(datetime.timezone.utc)
        sunday_9am = datetime.datetime(2026, 11, 1, 9, 0, tzinfo=ny).astimezone(datetime.timezone.utc)
        self.assertTrue(is_schedule_due(cfg, None, monday_9am))
        self.assertFalse(is_schedule_due(cfg, None, monday_10am))
        self.assertFalse(is_schedule_due(cfg, None, sunday_9am))

    def test_cron_duplicate_protection_within_same_minute(self):
        cfg = validate_trigger_config("schedule", {"cron": "0 9 * * *", "timezone": "UTC"})
        hit = datetime.datetime(2026, 11, 4, 9, 0, tzinfo=datetime.timezone.utc)
        self.assertTrue(is_schedule_due(cfg, None, hit))
        self.assertFalse(is_schedule_due(cfg, hit, hit))

    def test_real_end_to_end_scheduled_workflow_fires_via_celery_beat_sweep(self):
        from .tasks import run_due_scheduled_workflows

        owner = _make_user("gap-schedule-owner@example.com", tier_name="Business")
        group = create_group_with_conversation(owner=owner, name="SchedG", slug=f"schedg-{_uuid_suffix()}")
        bot = _active_bot(owner)
        grant = services.grant_resource_access(
            bot, owner, resource_type=BotResourceType.GROUP, resource_id=str(group.id),
            scopes=[BotPermissionScope.SEND_MESSAGES],
        )
        workflow = _published_workflow(
            bot, owner, grant, trigger_type=BotWorkflowTriggerType.SCHEDULE,
            definition={"then_actions": [{"type": "send_message", "params": {"text": "scheduled ping"}}]},
            trigger_config={"interval_minutes": 5, "timezone": "UTC"},
        )
        with patch("apps.chat.tasks.send_system_message_as_user", return_value={"ok": True}) as mock_send:
            triggered = run_due_scheduled_workflows()
        self.assertEqual(triggered, 1)
        mock_send.assert_called_once()
        self.assertTrue(BotWorkflowRun.objects.filter(workflow=workflow, status=BotWorkflowRunStatus.SUCCEEDED).exists())

        # Immediately sweeping again must not re-trigger (last_schedule_run_at
        # was just stamped, interval hasn't elapsed again).
        with patch("apps.chat.tasks.send_system_message_as_user", return_value={"ok": True}) as mock_send_2:
            triggered_again = run_due_scheduled_workflows()
        self.assertEqual(triggered_again, 0)
        mock_send_2.assert_not_called()


class InvalidPayloadSafetyTests(TestCase):
    """An invalid/malformed event payload must never crash the worker -
    it is either safely ignored or recorded as a FAILED run."""

    def setUp(self):
        cache.clear()
        ensure_default_account_tiers()
        self.owner = _make_user("gap-invalid-owner@example.com", tier_name="Business")
        self.group = create_group_with_conversation(owner=self.owner, name="InvG", slug=f"invg-{_uuid_suffix()}")
        self.bot = _active_bot(self.owner)
        self.grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            scopes=[BotPermissionScope.SEND_MESSAGES],
        )

    def test_missing_payload_fields_do_not_crash_dispatch(self):
        workflow = _published_workflow(
            self.bot, self.owner, self.grant, trigger_type=BotWorkflowTriggerType.MESSAGE_CREATED,
            definition={
                "conditions": {"rules": [{"field": "message.text", "op": "contains", "value": "x"}]},
                "then_actions": [{"type": "send_message", "params": {"text": "hi"}}],
            },
        )
        # No exception should propagate from a completely empty payload.
        dispatch_event_to_workflows(
            resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            event="message.created", payload={}, event_id="invalid-evt-1",
        )
        run = BotWorkflowRun.objects.filter(workflow=workflow, event_id__startswith="invalid-evt-1").first()
        self.assertIsNotNone(run)
        self.assertFalse(run.condition_result)

    def test_non_dict_action_params_fail_the_run_not_the_worker(self):
        workflow = _published_workflow(
            self.bot, self.owner, self.grant, trigger_type=BotWorkflowTriggerType.MESSAGE_CREATED,
            definition={"then_actions": [{"type": "execute_action", "params": {"action": "ban_member", "details": "not-a-dict"}}]},
        )
        try:
            dispatch_event_to_workflows(
                resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
                event="message.created", payload={"text": "hi", "senderId": str(self.owner.id)},
                event_id="invalid-evt-2",
            )
        except Exception as exc:  # noqa: BLE001
            self.fail(f"dispatch_event_to_workflows must never raise, got {exc!r}")
        run = BotWorkflowRun.objects.filter(workflow=workflow, event_id__startswith="invalid-evt-2").first()
        self.assertIsNotNone(run)
