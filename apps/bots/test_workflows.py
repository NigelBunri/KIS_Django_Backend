# apps/bots/test_workflows.py
"""
Phase 2 — no-code workflow builder tests. Covers the engine, lifecycle,
real event publishers (message.created via the existing Nest-webhook
path's Django half, member.joined/left via the new Group publishers),
and the nine end-to-end scenarios the spec calls out explicitly:

  1. event -> trigger matched -> conditions evaluated -> action executed
  2. invalid resource permission -> action rejected
  3. sensitive action -> approval requested -> executed only after approval
  4. duplicate event -> no duplicate action
  5. paused workflow -> no execution
  6. suspended owner -> workflow cannot continue executing
  7. revoked resource access -> subsequent actions rejected
  8. failed action -> appropriate error reporting
  9. infinite-loop / storm protection
"""
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import AccountTier, Subscription, User
from apps.accounts.tiers import ensure_default_account_tiers
from apps.groups.models import GroupBan, GroupMembership, GroupRole
from apps.groups.services import create_group_with_conversation

from . import services
from .models import (
    BotKind,
    BotPermissionScope,
    BotResourceType,
    BotStatus,
    BotWorkflowRun,
    BotWorkflowRunStatus,
    BotWorkflowStatus,
    BotWorkflowTriggerType,
)
from .workflow_engine import dispatch_event_to_workflows, evaluate_conditions, validate_definition


def _make_user(email: str, *, tier_name: str | None = None) -> User:
    user = User.objects.create_user(
        phone=f"+1556{abs(hash(email)) % 10_000_000:07d}",
        password="x", country="CM", email=email,
    )
    if tier_name:
        tier = AccountTier.objects.get(name=tier_name)
        Subscription.objects.create(user=user, tier=tier, status=Subscription.STATUS_ACTIVE)
    return user


def _uuid_suffix() -> str:
    import uuid

    return uuid.uuid4().hex[:8]


class WorkflowLifecycleTests(TestCase):
    def setUp(self):
        cache.clear()
        ensure_default_account_tiers()
        self.owner = _make_user("wf-owner@example.com", tier_name="Business")
        self.bot = services.create_bot(self.owner, name="WF Bot", kind=BotKind.RULE_BASED)
        services.set_status(self.bot, self.owner, BotStatus.ACTIVE)
        self.group = create_group_with_conversation(owner=self.owner, name="WFG", slug=f"wfg-{_uuid_suffix()}")
        self.grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            scopes=[BotPermissionScope.SEND_MESSAGES],
        )

    def test_create_workflow_starts_as_draft(self):
        workflow = services.create_workflow(
            self.bot, self.owner, resource_grant_id=self.grant.id, name="Welcome bot",
            trigger_type=BotWorkflowTriggerType.MESSAGE_CREATED,
            definition={"then_actions": [{"type": "send_message", "params": {"text": "hi"}}]},
        )
        self.assertEqual(workflow.status, BotWorkflowStatus.DRAFT)
        self.assertEqual(workflow.version, 1)

    def test_cannot_create_workflow_against_someone_elses_grant(self):
        outsider = _make_user("wf-outsider@example.com", tier_name="Business")
        other_bot = services.create_bot(outsider, name="Other", kind=BotKind.RULE_BASED)
        with self.assertRaises(Exception):
            services.create_workflow(
                other_bot, outsider, resource_grant_id=self.grant.id, name="Hijack",
                trigger_type=BotWorkflowTriggerType.MESSAGE_CREATED, definition={},
            )

    def test_publish_then_pause_then_resume(self):
        workflow = services.create_workflow(
            self.bot, self.owner, resource_grant_id=self.grant.id, name="W",
            trigger_type=BotWorkflowTriggerType.MESSAGE_CREATED,
            definition={"then_actions": [{"type": "send_message", "params": {"text": "hi"}}]},
        )
        workflow = services.publish_workflow(workflow, self.owner)
        self.assertEqual(workflow.status, BotWorkflowStatus.ACTIVE)
        self.assertEqual(workflow.versions.count(), 1)

        workflow = services.pause_workflow(workflow, self.owner)
        self.assertEqual(workflow.status, BotWorkflowStatus.PAUSED)

        workflow = services.resume_workflow(workflow, self.owner)
        self.assertEqual(workflow.status, BotWorkflowStatus.ACTIVE)

    def test_republishing_bumps_version_and_snapshots(self):
        workflow = services.create_workflow(
            self.bot, self.owner, resource_grant_id=self.grant.id, name="W",
            trigger_type=BotWorkflowTriggerType.MESSAGE_CREATED,
            definition={"then_actions": [{"type": "send_message", "params": {"text": "v1"}}]},
        )
        services.publish_workflow(workflow, self.owner)
        services.pause_workflow(workflow, self.owner)
        workflow = services.update_workflow(
            workflow, self.owner,
            definition={"then_actions": [{"type": "send_message", "params": {"text": "v2"}}]},
        )
        workflow = services.publish_workflow(workflow, self.owner)
        self.assertEqual(workflow.version, 2)
        self.assertEqual(workflow.versions.count(), 2)

    def test_cannot_edit_an_active_workflow_directly(self):
        workflow = services.create_workflow(
            self.bot, self.owner, resource_grant_id=self.grant.id, name="W",
            trigger_type=BotWorkflowTriggerType.MESSAGE_CREATED,
            definition={"then_actions": [{"type": "send_message", "params": {"text": "hi"}}]},
        )
        services.publish_workflow(workflow, self.owner)
        with self.assertRaises(Exception):
            services.update_workflow(workflow, self.owner, name="Renamed")

    def test_delete_is_a_soft_delete(self):
        workflow = services.create_workflow(
            self.bot, self.owner, resource_grant_id=self.grant.id, name="W",
            trigger_type=BotWorkflowTriggerType.MESSAGE_CREATED, definition={},
        )
        services.delete_workflow(workflow, self.owner)
        workflow.refresh_from_db()
        self.assertEqual(workflow.status, BotWorkflowStatus.DELETED)
        self.assertIsNotNone(workflow.deleted_at)

    def test_test_workflow_is_a_pure_dry_run_with_no_side_effects(self):
        workflow = services.create_workflow(
            self.bot, self.owner, resource_grant_id=self.grant.id, name="W",
            trigger_type=BotWorkflowTriggerType.MESSAGE_CREATED,
            definition={
                "conditions": {"op": "AND", "rules": [{"field": "message.text", "op": "contains", "value": "refund"}]},
                "then_actions": [{"type": "send_message", "params": {"text": "Sorry to hear that!"}}],
                "else_actions": [{"type": "send_message", "params": {"text": "Thanks!"}}],
            },
        )
        with patch("apps.chat.tasks.send_system_message_as_user") as mock_send:
            result = services.test_workflow(workflow, self.owner, {"text": "I want a refund", "senderId": str(self.owner.id)})
            mock_send.assert_not_called()
        self.assertTrue(result["condition_result"])
        self.assertEqual(result["would_run_actions"][0]["params"]["text"], "Sorry to hear that!")
        self.assertEqual(BotWorkflowRun.objects.count(), 0)


class WorkflowDefinitionValidationTests(TestCase):
    def test_rejects_unsupported_action_type(self):
        with self.assertRaises(Exception):
            validate_definition({"then_actions": [{"type": "delete_the_database", "params": {}}]})

    def test_rejects_unsupported_condition_operator(self):
        with self.assertRaises(Exception):
            validate_definition({"conditions": {"op": "AND", "rules": [{"field": "x", "op": "regex_exec", "value": "."}]}})

    def test_send_message_action_requires_text(self):
        with self.assertRaises(Exception):
            validate_definition({"then_actions": [{"type": "send_message", "params": {}}]})

    def test_and_or_logic(self):
        context = {"message": {"text": "refund please"}, "sender": {"id": "u1"}}
        and_true = evaluate_conditions(
            {"op": "AND", "rules": [
                {"field": "message.text", "op": "contains", "value": "refund"},
                {"field": "sender.id", "op": "equals", "value": "u1"},
            ]}, context,
        )
        self.assertTrue(and_true)
        and_false = evaluate_conditions(
            {"op": "AND", "rules": [
                {"field": "message.text", "op": "contains", "value": "refund"},
                {"field": "sender.id", "op": "equals", "value": "someone-else"},
            ]}, context,
        )
        self.assertFalse(and_false)
        or_true = evaluate_conditions(
            {"op": "OR", "rules": [
                {"field": "message.text", "op": "contains", "value": "nonsense"},
                {"field": "sender.id", "op": "equals", "value": "u1"},
            ]}, context,
        )
        self.assertTrue(or_true)


class WorkflowEndToEndTests(TestCase):
    """The nine required end-to-end scenarios, against a real published
    workflow driven through dispatch_event_to_workflows (the exact
    function the real message/member event publishers call) rather
    than calling run_workflow directly - this is what proves the whole
    pipeline, not just the engine in isolation."""

    def setUp(self):
        cache.clear()
        ensure_default_account_tiers()
        self.owner = _make_user("wf-e2e-owner@example.com", tier_name="Business")
        self.bot = services.create_bot(self.owner, name="E2E Bot", kind=BotKind.CUSTOM)
        services.set_status(self.bot, self.owner, BotStatus.ACTIVE)
        self.group = create_group_with_conversation(owner=self.owner, name="E2EG", slug=f"e2eg-{_uuid_suffix()}")
        self.grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            scopes=[BotPermissionScope.SEND_MESSAGES],
        )
        self.workflow = services.create_workflow(
            self.bot, self.owner, resource_grant_id=self.grant.id, name="Refund responder",
            trigger_type=BotWorkflowTriggerType.MESSAGE_CREATED,
            definition={
                "conditions": {"op": "AND", "rules": [{"field": "message.text", "op": "contains", "value": "refund"}]},
                "then_actions": [{"type": "send_message", "params": {"text": "We will help with your refund."}}],
                "else_actions": [],
            },
        )
        services.publish_workflow(self.workflow, self.owner)

    def _dispatch(self, text="I need a refund", event_id="evt-1", sender_id=None):
        with patch("apps.chat.tasks.send_system_message_as_user", return_value={"ok": True}) as mock_send:
            dispatch_event_to_workflows(
                resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
                event="message.created", payload={"text": text, "senderId": sender_id or str(self.owner.id)},
                event_id=event_id,
            )
        return mock_send

    # 1. event -> trigger matched -> conditions evaluated -> action executed
    def test_matching_event_runs_the_then_action(self):
        mock_send = self._dispatch(text="I need a refund", event_id="evt-match")
        mock_send.assert_called_once()
        run = BotWorkflowRun.objects.get(workflow=self.workflow, event_id__startswith="evt-match")
        self.assertEqual(run.status, BotWorkflowRunStatus.SUCCEEDED)
        self.assertTrue(run.condition_result)

    def test_non_matching_event_runs_no_action(self):
        mock_send = self._dispatch(text="just saying hi", event_id="evt-nomatch")
        mock_send.assert_not_called()
        run = BotWorkflowRun.objects.get(workflow=self.workflow, event_id__startswith="evt-nomatch")
        self.assertFalse(run.condition_result)
        self.assertEqual(run.steps_log, [])

    # 2. invalid resource permission -> action rejected
    def test_invalid_grant_denies_the_run(self):
        self.grant.scopes = []
        self.grant.save(update_fields=["scopes"])
        mock_send = self._dispatch(text="refund", event_id="evt-noscopematch")
        mock_send.assert_not_called()
        run = BotWorkflowRun.objects.get(workflow=self.workflow, event_id__startswith="evt-noscopematch")
        self.assertEqual(run.status, BotWorkflowRunStatus.FAILED)

    # 3. sensitive action -> approval requested -> executed only after approval
    def test_sensitive_action_requires_approval_then_executes(self):
        mod_grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            scopes=[BotPermissionScope.MODERATE],
        )
        target = _make_user("wf-ban-target@example.com")
        GroupMembership.objects.create(group=self.group, user=target, role=GroupRole.MEMBER)
        mod_workflow = services.create_workflow(
            self.bot, self.owner, resource_grant_id=mod_grant.id, name="Auto-moderate",
            trigger_type=BotWorkflowTriggerType.MESSAGE_CREATED,
            definition={
                "conditions": {"op": "AND", "rules": [{"field": "message.text", "op": "contains", "value": "spam"}]},
                "then_actions": [{"type": "execute_action", "params": {
                    "action": "ban_member", "details": {"user_id": str(target.id)},
                }}],
            },
        )
        services.publish_workflow(mod_workflow, self.owner)

        dispatch_event_to_workflows(
            resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            event="message.created", payload={"text": "this is spam", "senderId": str(target.id)},
            event_id="evt-spam",
        )
        self.assertFalse(GroupBan.objects.filter(group=self.group, user=target).exists())
        run = BotWorkflowRun.objects.get(workflow=mod_workflow, event_id__startswith="evt-spam")
        self.assertEqual(run.status, BotWorkflowRunStatus.SUCCEEDED)
        self.assertEqual(run.steps_log[0]["result"]["status"], "pending_approval")

        from .models import BotApprovalRequest

        approval = BotApprovalRequest.objects.get(id=run.steps_log[0]["result"]["approval_id"])
        services.decide_approval(approval, self.owner, True)
        self.assertTrue(GroupBan.objects.filter(group=self.group, user=target).exists())

    # 4. duplicate event -> no duplicate action
    def test_duplicate_event_id_never_runs_the_action_twice(self):
        mock_send = self._dispatch(text="refund", event_id="evt-dupe")
        self.assertEqual(mock_send.call_count, 1)
        mock_send = self._dispatch(text="refund", event_id="evt-dupe")
        mock_send.assert_not_called()
        self.assertEqual(
            BotWorkflowRun.objects.filter(workflow=self.workflow, event_id__startswith="evt-dupe").count(), 1,
        )

    # 5. paused workflow -> no execution
    def test_paused_workflow_does_not_run(self):
        services.pause_workflow(self.workflow, self.owner)
        mock_send = self._dispatch(text="refund", event_id="evt-paused")
        mock_send.assert_not_called()
        self.assertFalse(BotWorkflowRun.objects.filter(workflow=self.workflow, event_id__startswith="evt-paused").exists())

    # 6. suspended owner -> workflow cannot continue executing
    def test_suspended_owner_blocks_further_execution(self):
        self.owner.is_active = False
        self.owner.save(update_fields=["is_active"])
        mock_send = self._dispatch(text="refund", event_id="evt-suspended")
        mock_send.assert_not_called()
        run = BotWorkflowRun.objects.get(workflow=self.workflow, event_id__startswith="evt-suspended")
        self.assertEqual(run.status, BotWorkflowRunStatus.DENIED)

    # 7. revoked resource access -> subsequent actions rejected
    def test_revoked_grant_blocks_further_execution(self):
        services.revoke_resource_access(self.bot, self.owner, self.grant.id)
        mock_send = self._dispatch(text="refund", event_id="evt-revoked")
        mock_send.assert_not_called()
        # revoking deactivates the grant, so dispatch_event_to_workflows'
        # own query (status=ACTIVE, grant.is_active=True) no longer
        # matches this workflow at all - nothing to run, nothing denied.
        self.assertFalse(BotWorkflowRun.objects.filter(workflow=self.workflow, event_id__startswith="evt-revoked").exists())

    # 8. failed action -> appropriate error reporting
    def test_failing_action_is_recorded_with_an_error_message(self):
        with patch("apps.chat.tasks.send_system_message_as_user", side_effect=RuntimeError("Nest unreachable")):
            dispatch_event_to_workflows(
                resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
                event="message.created", payload={"text": "refund", "senderId": str(self.owner.id)},
                event_id="evt-fail",
            )
        run = BotWorkflowRun.objects.get(workflow=self.workflow, event_id__startswith="evt-fail")
        self.assertEqual(run.status, BotWorkflowRunStatus.FAILED)
        self.assertIn("Nest unreachable", run.error_message)
        self.assertEqual(run.steps_log[0]["status"], "error")

    # 9. infinite-loop / storm protection
    def test_a_bots_own_message_never_retriggers_its_own_workflow(self):
        mock_send = self._dispatch(text="refund", event_id="evt-self", sender_id=str(self.bot.id))
        mock_send.assert_not_called()
        self.assertFalse(BotWorkflowRun.objects.filter(workflow=self.workflow, event_id__startswith="evt-self").exists())

    def test_run_rate_storm_auto_pauses_the_workflow(self):
        with patch.object(services, "check_and_increment_bot_daily_usage"), \
             patch("apps.chat.tasks.send_system_message_as_user", return_value={"ok": True}), \
             patch("apps.bots.workflow_engine.MAX_RUNS_PER_MINUTE", 2), \
             patch("apps.bots.workflow_engine.WORKFLOW_ABUSE_THRESHOLD", 2):
            for i in range(6):
                dispatch_event_to_workflows(
                    resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
                    event="message.created", payload={"text": "refund", "senderId": str(self.owner.id)},
                    event_id=f"evt-storm-{i}",
                )
        self.workflow.refresh_from_db()
        self.assertEqual(self.workflow.status, BotWorkflowStatus.PAUSED)


class WorkflowMemberEventPublisherTests(TestCase):
    """Proves the NEW member.joined/member.left publishers work through
    the real HTTP views (apps.groups.views), not just by calling the
    engine directly - this is the actual event-publisher audit item
    being exercised end to end."""

    def setUp(self):
        cache.clear()
        ensure_default_account_tiers()
        self.group_owner = _make_user("wf-member-owner@example.com", tier_name="Business")
        self.group = create_group_with_conversation(owner=self.group_owner, name="MG", slug=f"mg-{_uuid_suffix()}")
        from apps.groups.models import GroupJoinPolicy

        self.group.join_policy = GroupJoinPolicy.OPEN
        self.group.save(update_fields=["join_policy"])

        self.bot_owner = _make_user("wf-member-bot-owner@example.com", tier_name="Business")
        GroupMembership.objects.create(group=self.group, user=self.bot_owner, role=GroupRole.ADMIN)
        self.bot = services.create_bot(self.bot_owner, name="Welcomer", kind=BotKind.CUSTOM)
        services.set_status(self.bot, self.bot_owner, BotStatus.ACTIVE)
        self.grant = services.grant_resource_access(
            self.bot, self.bot_owner, resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            scopes=[BotPermissionScope.SEND_MESSAGES, BotPermissionScope.READ_MEMBERS],
        )
        self.workflow = services.create_workflow(
            self.bot, self.bot_owner, resource_grant_id=self.grant.id, name="Welcome new members",
            trigger_type=BotWorkflowTriggerType.MEMBER_JOINED,
            definition={"then_actions": [{"type": "send_message", "params": {"text": "Welcome!"}}]},
        )
        services.publish_workflow(self.workflow, self.bot_owner)

    def test_real_http_add_member_triggers_the_workflow(self):
        # GroupViewSet.get_queryset only makes a group visible to its
        # owner or an existing member (even for OPEN groups), so a
        # never-before-seen stranger calling join() against a group ID
        # they have no other way to discover 404s before join() even
        # runs - that's pre-existing GroupViewSet access control, not a
        # Phase 2 concern. The admin add-members path is the real HTTP
        # route a group's own admin actually uses to bring in a new
        # member, and it is reachable here since self.bot_owner already
        # has admin visibility into self.group.
        new_member = _make_user("wf-new-member@example.com")
        client = APIClient()
        client.force_authenticate(user=self.bot_owner)
        with patch("apps.chat.tasks.send_system_message_as_user", return_value={"ok": True}) as mock_send:
            response = client.post(
                f"/api/v1/chat-groups/{self.group.id}/add-members/",
                {"userIds": [str(new_member.id)]}, format="json",
            )
        self.assertEqual(response.status_code, 200)
        mock_send.assert_called_once()
        self.assertTrue(BotWorkflowRun.objects.filter(workflow=self.workflow, status=BotWorkflowRunStatus.SUCCEEDED).exists())

    def test_real_http_leave_triggers_a_member_left_workflow(self):
        leave_workflow = services.create_workflow(
            self.bot, self.bot_owner, resource_grant_id=self.grant.id, name="Farewell",
            trigger_type=BotWorkflowTriggerType.MEMBER_LEFT,
            definition={"then_actions": [{"type": "send_message", "params": {"text": "Bye!"}}]},
        )
        services.publish_workflow(leave_workflow, self.bot_owner)

        leaving_member = _make_user("wf-leaving-member@example.com")
        GroupMembership.objects.create(group=self.group, user=leaving_member, role=GroupRole.MEMBER)
        client = APIClient()
        client.force_authenticate(user=leaving_member)
        with patch("apps.chat.tasks.send_system_message_as_user", return_value={"ok": True}) as mock_send:
            response = client.post(f"/api/v1/chat-groups/{self.group.id}/leave/")
        self.assertEqual(response.status_code, 200)
        mock_send.assert_called_once()
        self.assertTrue(BotWorkflowRun.objects.filter(workflow=leave_workflow, status=BotWorkflowRunStatus.SUCCEEDED).exists())


class WorkflowAPITests(TestCase):
    def setUp(self):
        cache.clear()
        ensure_default_account_tiers()
        self.owner = _make_user("wf-api-owner@example.com", tier_name="Business")
        self.bot = services.create_bot(self.owner, name="API Bot", kind=BotKind.CUSTOM)
        services.set_status(self.bot, self.owner, BotStatus.ACTIVE)
        self.group = create_group_with_conversation(owner=self.owner, name="APIG", slug=f"apig-{_uuid_suffix()}")
        self.grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            scopes=[BotPermissionScope.SEND_MESSAGES],
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def test_full_crud_and_lifecycle_via_http(self):
        create_res = self.client.post(
            f"/api/v1/bots/{self.bot.id}/workflows/",
            {
                "resource_grant_id": str(self.grant.id), "name": "HTTP workflow",
                "trigger_type": "message.created",
                "definition": {"then_actions": [{"type": "send_message", "params": {"text": "hi"}}]},
            },
            format="json",
        )
        self.assertEqual(create_res.status_code, 201)
        workflow_id = create_res.json()["id"]

        list_res = self.client.get(f"/api/v1/bots/{self.bot.id}/workflows/")
        self.assertEqual(list_res.status_code, 200)
        self.assertEqual(len(list_res.json()), 1)

        test_res = self.client.post(
            f"/api/v1/bots/{self.bot.id}/workflows/{workflow_id}/test/",
            {"sample_payload": {"text": "hi", "senderId": str(self.owner.id)}}, format="json",
        )
        self.assertEqual(test_res.status_code, 200)
        self.assertEqual(test_res.json()["would_run_actions"][0]["params"]["text"], "hi")

        publish_res = self.client.post(f"/api/v1/bots/{self.bot.id}/workflows/{workflow_id}/publish/")
        self.assertEqual(publish_res.status_code, 200)
        self.assertEqual(publish_res.json()["status"], "active")

        pause_res = self.client.post(f"/api/v1/bots/{self.bot.id}/workflows/{workflow_id}/pause/")
        self.assertEqual(pause_res.status_code, 200)
        self.assertEqual(pause_res.json()["status"], "paused")

        resume_res = self.client.post(f"/api/v1/bots/{self.bot.id}/workflows/{workflow_id}/resume/")
        self.assertEqual(resume_res.status_code, 200)
        self.assertEqual(resume_res.json()["status"], "active")

        versions_res = self.client.get(f"/api/v1/bots/{self.bot.id}/workflows/{workflow_id}/versions/")
        self.assertEqual(versions_res.status_code, 200)
        self.assertEqual(len(versions_res.json()), 1)

        runs_res = self.client.get(f"/api/v1/bots/{self.bot.id}/workflows/{workflow_id}/runs/")
        self.assertEqual(runs_res.status_code, 200)

        delete_res = self.client.delete(f"/api/v1/bots/{self.bot.id}/workflows/{workflow_id}/")
        self.assertEqual(delete_res.status_code, 204)

    def test_outsider_cannot_see_or_manage_another_owners_workflow(self):
        workflow = services.create_workflow(
            self.bot, self.owner, resource_grant_id=self.grant.id, name="Private",
            trigger_type=BotWorkflowTriggerType.MESSAGE_CREATED, definition={},
        )
        outsider = _make_user("wf-api-outsider@example.com", tier_name="Business")
        outsider_bot = services.create_bot(outsider, name="Outsider Bot", kind=BotKind.CUSTOM)
        client = APIClient()
        client.force_authenticate(user=outsider)
        response = client.get(f"/api/v1/bots/{outsider_bot.id}/workflows/{workflow.id}/")
        self.assertEqual(response.status_code, 404)
