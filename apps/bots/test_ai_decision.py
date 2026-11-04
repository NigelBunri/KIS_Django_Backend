# apps/bots/test_ai_decision.py
"""
Phase 3 — AI-assisted action selection. Covers: AI stays fully optional
(disabled workflows are byte-for-byte unaffected), a misconfigured or
missing AI connection fails the run rather than silently no-op'ing,
every failure mode (provider error, malformed JSON, out-of-range index,
daily limit) results in ZERO actions executed, a successful selection
only ever runs actions that were already in the workflow's own
then_actions (never anything invented), and a selected action still
passes through the exact same scope/approval-gating any statically
configured action would - proving the AI cannot bypass the workflow
engine, only choose within it.
"""
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from apps.accounts.tiers import ensure_default_account_tiers

from . import services
from .ai_providers import AIProviderError
from .models import (
    BotAIConnection,
    BotAIProvider,
    BotAIUsageLog,
    BotKind,
    BotPermissionScope,
    BotResourceType,
    BotStatus,
    BotWorkflowRun,
    BotWorkflowRunStatus,
    BotWorkflowTriggerType,
)
from .services import encrypt_ai_api_key
from .test_workflows import _make_user, _uuid_suffix


def _active_bot(owner):
    bot = services.create_bot(owner, name=f"AIBot-{_uuid_suffix()}", kind=BotKind.CUSTOM)
    services.set_status(bot, owner, BotStatus.ACTIVE)
    return bot


class AIDecisionTests(TestCase):
    def setUp(self):
        cache.clear()
        ensure_default_account_tiers()
        self.owner = _make_user("ai-decision-owner@example.com", tier_name="Business")
        from apps.groups.models import GroupMembership, GroupRole
        from apps.groups.services import create_group_with_conversation

        self.group = create_group_with_conversation(owner=self.owner, name="AIG", slug=f"aig-{_uuid_suffix()}")
        self.target = _make_user("ai-decision-target@example.com")
        GroupMembership.objects.create(group=self.group, user=self.target, role=GroupRole.MEMBER)
        self.bot = _active_bot(self.owner)
        self.grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            scopes=[BotPermissionScope.SEND_MESSAGES, BotPermissionScope.READ_MEMBERS, BotPermissionScope.MODERATE],
        )

    def _connect_ai(self, **overrides):
        kwargs = dict(provider=BotAIProvider.ANTHROPIC, api_key="test-anthropic-key", model="claude-3-5-haiku-latest")
        kwargs.update(overrides)
        return services.connect_ai_provider(self.bot, self.owner, **kwargs)

    def _workflow_with_two_candidates(self, *, ai_enabled=True, instructions=""):
        definition = {
            "then_actions": [
                {"type": "send_notification", "params": {"user_id": str(self.target.id), "title": "A", "body": "A"}, "label": "notify"},
                {"type": "send_message", "params": {"text": "Welcome!"}, "label": "welcome message"},
            ],
            "ai_decision": {"enabled": ai_enabled, "instructions": instructions},
        }
        workflow = services.create_workflow(
            self.bot, self.owner, resource_grant_id=self.grant.id, name=f"AIWF-{_uuid_suffix()}",
            trigger_type=BotWorkflowTriggerType.MEMBER_JOINED, definition=definition,
        )
        return services.publish_workflow(workflow, self.owner)

    def _dispatch(self, workflow, event_id=None):
        from .workflow_engine import dispatch_event_to_workflows

        dispatch_event_to_workflows(
            resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            event="member.joined", payload={"user_id": str(self.target.id)},
            event_id=event_id or f"ai-evt-{_uuid_suffix()}",
        )
        return BotWorkflowRun.objects.filter(workflow=workflow).order_by("-started_at").first()

    # --- Optionality -----------------------------------------------------

    def test_ai_decision_disabled_runs_all_then_actions_unaffected(self):
        workflow = self._workflow_with_two_candidates(ai_enabled=False)
        with patch("apps.notifications.services.create_notification") as mock_notify, \
             patch("apps.chat.tasks.send_system_message_as_user", return_value={"ok": True}) as mock_send:
            mock_notify.return_value.id = "notif-1"
            run = self._dispatch(workflow)
        self.assertEqual(run.status, BotWorkflowRunStatus.SUCCEEDED)
        mock_notify.assert_called_once()
        mock_send.assert_called_once()
        self.assertFalse(BotAIUsageLog.objects.filter(bot=self.bot).exists())

    def test_ai_decision_enabled_without_connection_fails_safely(self):
        workflow = self._workflow_with_two_candidates(ai_enabled=True)
        with patch("apps.notifications.services.create_notification") as mock_notify, \
             patch("apps.chat.tasks.send_system_message_as_user", return_value={"ok": True}) as mock_send:
            run = self._dispatch(workflow)
        self.assertEqual(run.status, BotWorkflowRunStatus.FAILED)
        self.assertIn("no active AI connection", run.error_message)
        mock_notify.assert_not_called()
        mock_send.assert_not_called()

    # --- Successful selection ---------------------------------------------

    def test_ai_decision_runs_only_the_selected_candidate(self):
        self._connect_ai()
        workflow = self._workflow_with_two_candidates()
        with patch("apps.bots.ai_decision.call_ai_provider", return_value={"text": '{"selected": [1], "reasoning": "welcome is appropriate"}', "prompt_tokens": 50, "completion_tokens": 10}) as mock_call, \
             patch("apps.notifications.services.create_notification") as mock_notify, \
             patch("apps.chat.tasks.send_system_message_as_user", return_value={"ok": True}) as mock_send:
            run = self._dispatch(workflow)
        mock_call.assert_called_once()
        mock_notify.assert_not_called()
        mock_send.assert_called_once()
        self.assertEqual(run.status, BotWorkflowRunStatus.SUCCEEDED)
        ai_step = next(s for s in run.steps_log if s["action"] == "ai_decision")
        self.assertEqual(ai_step["selected_indices"], [1])
        usage = BotAIUsageLog.objects.get(bot=self.bot)
        self.assertEqual(usage.status, "success")
        self.assertEqual(usage.selected_indices, [1])
        self.assertEqual(usage.prompt_tokens, 50)

    def test_ai_decision_selecting_none_is_a_successful_no_op(self):
        self._connect_ai()
        workflow = self._workflow_with_two_candidates()
        with patch("apps.bots.ai_decision.call_ai_provider", return_value={"text": '{"selected": [], "reasoning": "nothing warranted"}', "prompt_tokens": None, "completion_tokens": None}), \
             patch("apps.notifications.services.create_notification") as mock_notify, \
             patch("apps.chat.tasks.send_system_message_as_user", return_value={"ok": True}) as mock_send:
            run = self._dispatch(workflow)
        mock_notify.assert_not_called()
        mock_send.assert_not_called()
        self.assertEqual(run.status, BotWorkflowRunStatus.SUCCEEDED)

    # --- Fail-safe on every failure mode -----------------------------------

    def test_ai_decision_provider_error_executes_nothing(self):
        self._connect_ai()
        workflow = self._workflow_with_two_candidates()
        with patch("apps.bots.ai_decision.call_ai_provider", side_effect=AIProviderError("timeout")), \
             patch("apps.notifications.services.create_notification") as mock_notify, \
             patch("apps.chat.tasks.send_system_message_as_user", return_value={"ok": True}) as mock_send:
            run = self._dispatch(workflow)
        self.assertEqual(run.status, BotWorkflowRunStatus.FAILED)
        self.assertIn("timeout", run.error_message)
        mock_notify.assert_not_called()
        mock_send.assert_not_called()
        usage = BotAIUsageLog.objects.get(bot=self.bot)
        self.assertEqual(usage.status, "error")

    def test_ai_decision_malformed_json_executes_nothing(self):
        self._connect_ai()
        workflow = self._workflow_with_two_candidates()
        with patch("apps.bots.ai_decision.call_ai_provider", return_value={"text": "not json at all", "prompt_tokens": None, "completion_tokens": None}), \
             patch("apps.notifications.services.create_notification") as mock_notify, \
             patch("apps.chat.tasks.send_system_message_as_user", return_value={"ok": True}) as mock_send:
            run = self._dispatch(workflow)
        self.assertEqual(run.status, BotWorkflowRunStatus.FAILED)
        mock_notify.assert_not_called()
        mock_send.assert_not_called()

    def test_ai_decision_out_of_range_index_executes_nothing(self):
        self._connect_ai()
        workflow = self._workflow_with_two_candidates()
        with patch("apps.bots.ai_decision.call_ai_provider", return_value={"text": '{"selected": [99]}', "prompt_tokens": None, "completion_tokens": None}), \
             patch("apps.notifications.services.create_notification") as mock_notify, \
             patch("apps.chat.tasks.send_system_message_as_user", return_value={"ok": True}) as mock_send:
            run = self._dispatch(workflow)
        self.assertEqual(run.status, BotWorkflowRunStatus.FAILED)
        mock_notify.assert_not_called()
        mock_send.assert_not_called()

    def test_ai_decision_respects_daily_request_limit(self):
        self._connect_ai(daily_request_limit=1)
        workflow = self._workflow_with_two_candidates()
        with patch("apps.bots.ai_decision.call_ai_provider", return_value={"text": '{"selected": []}', "prompt_tokens": None, "completion_tokens": None}):
            run1 = self._dispatch(workflow, event_id="ai-limit-evt-1")
        self.assertEqual(run1.status, BotWorkflowRunStatus.SUCCEEDED)
        with patch("apps.bots.ai_decision.call_ai_provider") as mock_call:
            run2 = self._dispatch(workflow, event_id="ai-limit-evt-2")
        mock_call.assert_not_called()
        self.assertEqual(run2.status, BotWorkflowRunStatus.FAILED)
        self.assertIn("daily request limit", run2.error_message)

    # --- AI cannot bypass existing enforcement -----------------------------

    def test_ai_selected_action_still_rejected_if_unsupported_for_resource_type(self):
        """mute_member is Partner-only - proving the AI's selection runs
        through the exact same approval-gated path a static action
        would (mute_member is sensitive, so it queues for approval
        before resource-type validation ever runs - identical to the
        static-workflow case tested in test_gap_closing.py's
        test_mute_member_is_partner_only); the resource-type rejection
        only surfaces once a human actually approves it, never earlier
        and never because the AI picked it instead of a static config."""
        self._connect_ai()
        definition = {
            "then_actions": [
                {"type": "execute_action", "params": {"action": "mute_member", "details": {"user_id": str(self.target.id)}}, "label": "mute"},
            ],
            "ai_decision": {"enabled": True},
        }
        workflow = services.publish_workflow(
            services.create_workflow(
                self.bot, self.owner, resource_grant_id=self.grant.id, name=f"AIWF-mute-{_uuid_suffix()}",
                trigger_type=BotWorkflowTriggerType.MEMBER_JOINED, definition=definition,
            ),
            self.owner,
        )
        with patch("apps.bots.ai_decision.call_ai_provider", return_value={"text": '{"selected": [0]}', "prompt_tokens": None, "completion_tokens": None}):
            run = self._dispatch(workflow)
        self.assertEqual(run.status, BotWorkflowRunStatus.SUCCEEDED)
        step = next(s for s in run.steps_log if s["action"] == "execute_action")
        self.assertEqual(step["result"]["status"], "pending_approval")

        from .models import BotApprovalRequest

        approval = BotApprovalRequest.objects.get(id=step["result"]["approval_id"])
        decided = services.decide_approval(approval, self.owner, True)
        self.assertIn("mute_member is only supported for Partner resources", decided.execution_error)

    def test_ai_selected_sensitive_action_still_requires_approval(self):
        """remove_member is sensitive/approval-gated - the AI choosing it
        must queue an approval, not execute it immediately, exactly like
        a human-configured static action would."""
        self._connect_ai()
        definition = {
            "then_actions": [
                {"type": "execute_action", "params": {"action": "remove_member", "details": {"user_id": str(self.target.id)}}, "label": "remove"},
            ],
            "ai_decision": {"enabled": True},
        }
        workflow = services.publish_workflow(
            services.create_workflow(
                self.bot, self.owner, resource_grant_id=self.grant.id, name=f"AIWF-remove-{_uuid_suffix()}",
                trigger_type=BotWorkflowTriggerType.MEMBER_JOINED, definition=definition,
            ),
            self.owner,
        )
        with patch("apps.bots.ai_decision.call_ai_provider", return_value={"text": '{"selected": [0]}', "prompt_tokens": None, "completion_tokens": None}):
            run = self._dispatch(workflow)
        self.assertEqual(run.status, BotWorkflowRunStatus.SUCCEEDED)
        step = next(s for s in run.steps_log if s["action"] == "execute_action")
        self.assertEqual(step["result"]["status"], "pending_approval")
        from apps.groups.models import GroupMembership

        self.assertIsNone(GroupMembership.objects.get(group=self.group, user=self.target).left_at)

    # --- Usage visibility endpoint ------------------------------------------

    def test_ai_usage_endpoint_reports_calls_and_tokens(self):
        from rest_framework.test import APIClient

        self._connect_ai()
        workflow = self._workflow_with_two_candidates()
        with patch("apps.bots.ai_decision.call_ai_provider", return_value={"text": '{"selected": [1]}', "prompt_tokens": 42, "completion_tokens": 7}), \
             patch("apps.chat.tasks.send_system_message_as_user", return_value={"ok": True}):
            self._dispatch(workflow)
        with patch("apps.bots.ai_decision.call_ai_provider", side_effect=AIProviderError("down")):
            self._dispatch(workflow, event_id="ai-usage-evt-2")

        client = APIClient()
        client.force_authenticate(user=self.owner)
        response = client.get(f"/api/v1/bots/{self.bot.id}/ai-usage/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["summary"]["total_calls"], 2)
        self.assertEqual(body["summary"]["error_calls"], 1)
        self.assertEqual(body["summary"]["total_prompt_tokens"], 42)
        self.assertEqual(len(body["recent"]), 2)
