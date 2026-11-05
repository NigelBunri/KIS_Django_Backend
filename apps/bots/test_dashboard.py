# apps/bots/test_dashboard.py
"""
Phase 5 — owner dashboard overview, resource-permission display,
webhook delivery history/manual retry, and analytics + CSV export.
Every test proves BOTH that real data surfaces correctly AND that
none of it crosses from one owner's bots into another's.
"""
import csv
import io
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.tiers import ensure_default_account_tiers
from apps.groups.models import GroupMembership, GroupRole
from apps.groups.services import create_group_with_conversation

from . import services
from .models import (
    BotApprovalRequest,
    BotKind,
    BotPermissionScope,
    BotResourceType,
    BotStatus,
    BotWebhookDelivery,
    BotWebhookDeliveryStatus,
    BotWorkflowRunStatus,
    BotWorkflowTriggerType,
)
from .test_workflows import _make_user, _uuid_suffix


def _active_bot(owner, name="DashBot"):
    bot = services.create_bot(owner, name=f"{name}-{_uuid_suffix()}", kind=BotKind.CUSTOM)
    services.set_status(bot, owner, BotStatus.ACTIVE)
    return bot


class DashboardOverviewTests(TestCase):
    def setUp(self):
        cache.clear()
        ensure_default_account_tiers()
        self.owner = _make_user("dash-owner@example.com", tier_name="Business")
        self.other_owner = _make_user("dash-other-owner@example.com", tier_name="Business")
        self.target = _make_user("dash-target@example.com")

        self.group = create_group_with_conversation(owner=self.owner, name="DashG", slug=f"dashg-{_uuid_suffix()}")
        GroupMembership.objects.create(group=self.group, user=self.target, role=GroupRole.MEMBER)
        self.bot = _active_bot(self.owner)
        self.grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            scopes=[BotPermissionScope.SEND_MESSAGES, BotPermissionScope.READ_MEMBERS, BotPermissionScope.MODERATE],
        )
        # A second bot for a DIFFERENT owner, to prove isolation.
        self.other_bot = _active_bot(self.other_owner, "OtherDashBot")

    def test_overview_reflects_real_bot_counts(self):
        data = services.get_owner_dashboard_overview(self.owner)
        self.assertEqual(data["bots"]["total"], 1)
        self.assertEqual(data["bots"]["active"], 1)
        self.assertEqual(data["bots"]["suspended"], 0)

    def test_overview_never_counts_another_owners_bots(self):
        data = services.get_owner_dashboard_overview(self.other_owner)
        self.assertEqual(data["bots"]["total"], 1)
        # Not 2 - self.bot belongs to self.owner, not self.other_owner.

    def test_overview_includes_pending_approval(self):
        result = services.execute_bot_action(self.bot, self.grant, "remove_member", {"user_id": str(self.target.id)})
        self.assertEqual(result["status"], "pending_approval")
        data = services.get_owner_dashboard_overview(self.owner)
        self.assertEqual(len(data["pending_approvals"]), 1)
        self.assertEqual(data["pending_approvals"][0]["bot_id"], str(self.bot.id))

    def test_overview_includes_recent_execution(self):
        workflow = services.publish_workflow(
            services.create_workflow(
                self.bot, self.owner, resource_grant_id=self.grant.id, name=f"DashWF-{_uuid_suffix()}",
                trigger_type=BotWorkflowTriggerType.MEMBER_JOINED, definition={"then_actions": []},
            ),
            self.owner,
        )
        from .workflow_engine import dispatch_event_to_workflows

        dispatch_event_to_workflows(
            resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            event="member.joined", payload={"user_id": str(self.target.id)}, event_id=f"dash-evt-{_uuid_suffix()}",
        )
        data = services.get_owner_dashboard_overview(self.owner)
        self.assertEqual(data["executions"]["succeeded_7d"], 1)
        self.assertEqual(len(data["executions"]["recent"]), 1)
        self.assertEqual(data["executions"]["recent"][0]["workflow_name"], workflow.name)

    def test_overview_http_endpoint(self):
        client = APIClient()
        client.force_authenticate(user=self.owner)
        response = client.get("/api/v1/bots/overview/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("bots", response.json())


class ResourceGrantDisplaySerializerTests(TestCase):
    def setUp(self):
        cache.clear()
        ensure_default_account_tiers()
        self.owner = _make_user("grant-display-owner@example.com", tier_name="Business")
        self.group = create_group_with_conversation(owner=self.owner, name="GrantDisplayG", slug=f"grantdisp-{_uuid_suffix()}")
        self.bot = _active_bot(self.owner)
        self.grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            scopes=[BotPermissionScope.SEND_MESSAGES],
        )

    def test_grant_list_shows_real_resource_name_and_validity(self):
        client = APIClient()
        client.force_authenticate(user=self.owner)
        response = client.get(f"/api/v1/bots/{self.bot.id}/resource-grants/")
        self.assertEqual(response.status_code, 200)
        grant_data = response.json()[0]
        self.assertEqual(grant_data["resource_display_name"], "GrantDisplayG")
        self.assertTrue(grant_data["is_currently_valid"])
        self.assertEqual(grant_data["granted_by"], str(self.owner.id))

    def test_grant_display_name_survives_resource_deletion(self):
        name = services.resolve_resource_display_name(BotResourceType.GROUP, "00000000-0000-0000-0000-000000000000")
        self.assertEqual(name, "Deleted group")

    def test_grant_invalid_after_bot_suspended(self):
        services.set_status_admin(self.bot, self.owner, BotStatus.SUSPENDED, reason="test")
        client = APIClient()
        client.force_authenticate(user=self.owner)
        response = client.get(f"/api/v1/bots/{self.bot.id}/resource-grants/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()[0]["is_currently_valid"])


class WebhookDeliveryDashboardTests(TestCase):
    def setUp(self):
        cache.clear()
        ensure_default_account_tiers()
        self.owner = _make_user("webhook-dash-owner@example.com", tier_name="Business")
        self.other_owner = _make_user("webhook-dash-other@example.com", tier_name="Business")
        self.group = create_group_with_conversation(owner=self.owner, name="WebhookDashG", slug=f"webhookdashg-{_uuid_suffix()}")
        self.bot = _active_bot(self.owner)
        self.grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            scopes=[BotPermissionScope.SEND_MESSAGES],
        )
        self.other_bot = _active_bot(self.other_owner, "OtherWebhookBot")

        client = APIClient()
        client.force_authenticate(user=self.owner)
        res = client.put(f"/api/v1/bots/{self.bot.id}/webhook/", {"url": "https://example.org/hook", "events": ["message.created"]}, format="json")
        self.assertEqual(res.status_code, 201)  # first-time creation

    def _make_failed_delivery(self):
        subscription = self.bot.webhook_subscription
        return BotWebhookDelivery.objects.create(
            subscription=subscription, event="message.created", event_id=f"dash-{_uuid_suffix()}",
            status=BotWebhookDeliveryStatus.FAILED, attempt_count=1, error_message="connection refused",
        )

    def test_delivery_history_lists_real_rows(self):
        self._make_failed_delivery()
        client = APIClient()
        client.force_authenticate(user=self.owner)
        response = client.get(f"/api/v1/bots/{self.bot.id}/webhook/deliveries/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]["status"], "failed")

    def test_other_owner_cannot_list_this_bots_deliveries(self):
        self._make_failed_delivery()
        client = APIClient()
        client.force_authenticate(user=self.other_owner)
        response = client.get(f"/api/v1/bots/{self.bot.id}/webhook/deliveries/")
        self.assertEqual(response.status_code, 404)

    def test_manual_retry_succeeds_and_is_idempotent_on_success(self):
        delivery = self._make_failed_delivery()
        client = APIClient()
        client.force_authenticate(user=self.owner)
        with patch("apps.bots.services.is_safe_external_url", return_value=True), \
             patch("apps.bots.services.hmac") as mock_hmac, \
             patch("apps.bots.services.urllib.request.urlopen") as mock_urlopen:
            mock_hmac.new.return_value.hexdigest.return_value = "sig"
            mock_urlopen.return_value.__enter__.return_value.status = 200
            response = client.post(f"/api/v1/bots/{self.bot.id}/webhook/deliveries/{delivery.id}/retry/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "delivered")
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, BotWebhookDeliveryStatus.DELIVERED)

        # Retrying an already-DELIVERED delivery must be refused - this
        # is the "never create a duplicate business action" guarantee.
        response2 = client.post(f"/api/v1/bots/{self.bot.id}/webhook/deliveries/{delivery.id}/retry/")
        self.assertEqual(response2.status_code, 400)

    def test_dead_letter_can_be_manually_retried(self):
        subscription = self.bot.webhook_subscription
        delivery = BotWebhookDelivery.objects.create(
            subscription=subscription, event="message.created", event_id=f"dash-dl-{_uuid_suffix()}",
            status=BotWebhookDeliveryStatus.DEAD_LETTER, attempt_count=5,
        )
        client = APIClient()
        client.force_authenticate(user=self.owner)
        with patch("apps.bots.services.is_safe_external_url", return_value=False):
            # Still "allowed to attempt" even if this particular retry
            # fails again (unsafe URL) - the point is DEAD_LETTER status
            # alone doesn't block a manual retry attempt.
            response = client.post(f"/api/v1/bots/{self.bot.id}/webhook/deliveries/{delivery.id}/retry/")
        self.assertEqual(response.status_code, 200)

    def test_other_owner_cannot_retry_this_bots_delivery(self):
        delivery = self._make_failed_delivery()
        client = APIClient()
        client.force_authenticate(user=self.other_owner)
        response = client.post(f"/api/v1/bots/{self.bot.id}/webhook/deliveries/{delivery.id}/retry/")
        self.assertEqual(response.status_code, 404)


class AnalyticsAndExportTests(TestCase):
    def setUp(self):
        cache.clear()
        ensure_default_account_tiers()
        self.owner = _make_user("analytics-owner@example.com", tier_name="Business")
        self.other_owner = _make_user("analytics-other@example.com", tier_name="Business")
        self.group = create_group_with_conversation(owner=self.owner, name="AnalyticsG", slug=f"analyticsg-{_uuid_suffix()}")
        self.target = _make_user("analytics-target@example.com")
        GroupMembership.objects.create(group=self.group, user=self.target, role=GroupRole.MEMBER)
        self.bot = _active_bot(self.owner)
        self.grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            scopes=[BotPermissionScope.SEND_MESSAGES, BotPermissionScope.READ_MEMBERS],
        )
        self.workflow = services.publish_workflow(
            services.create_workflow(
                self.bot, self.owner, resource_grant_id=self.grant.id, name=f"AnalyticsWF-{_uuid_suffix()}",
                trigger_type=BotWorkflowTriggerType.MEMBER_JOINED, definition={"then_actions": []},
            ),
            self.owner,
        )
        self.other_bot = _active_bot(self.other_owner, "OtherAnalyticsBot")

        from .workflow_engine import dispatch_event_to_workflows

        dispatch_event_to_workflows(
            resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            event="member.joined", payload={"user_id": str(self.target.id)}, event_id=f"analytics-evt-{_uuid_suffix()}",
        )

    def test_analytics_counts_real_execution(self):
        data = services.get_owner_analytics(self.owner, {})
        self.assertEqual(data["executions"]["total"], 1)
        self.assertEqual(data["executions"]["succeeded"], 1)

    def test_analytics_bot_filter_scopes_correctly(self):
        other_data = services.get_owner_analytics(self.other_owner, {})
        self.assertEqual(other_data["executions"]["total"], 0)

    def test_analytics_status_filter(self):
        data = services.get_owner_analytics(self.owner, {"run_status": BotWorkflowRunStatus.FAILED})
        self.assertEqual(data["executions"]["total"], 0)
        data_ok = services.get_owner_analytics(self.owner, {"run_status": BotWorkflowRunStatus.SUCCEEDED})
        self.assertEqual(data_ok["executions"]["total"], 1)

    def test_analytics_date_range_filter_excludes_out_of_range(self):
        from datetime import timedelta

        from django.utils import timezone

        future = timezone.now() + timedelta(days=1)
        data = services.get_owner_analytics(self.owner, {"date_from": future})
        self.assertEqual(data["executions"]["total"], 0)

    def test_analytics_http_endpoint_permission_scoped(self):
        client = APIClient()
        client.force_authenticate(user=self.owner)
        response = client.get("/api/v1/bots/analytics/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["executions"]["total"], 1)

        client.force_authenticate(user=self.other_owner)
        response2 = client.get("/api/v1/bots/analytics/")
        self.assertEqual(response2.json()["executions"]["total"], 0)

    def test_csv_export_contains_the_real_run_and_respects_ownership(self):
        client = APIClient()
        client.force_authenticate(user=self.owner)
        response = client.get("/api/v1/bots/analytics/export/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        self.assertEqual(rows[0], ["run_id", "bot_name", "workflow_name", "trigger_event", "status", "started_at", "finished_at", "error_message"])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][1], self.bot.name)

        client.force_authenticate(user=self.other_owner)
        response2 = client.get("/api/v1/bots/analytics/export/")
        rows2 = list(csv.reader(io.StringIO(response2.content.decode())))
        self.assertEqual(len(rows2), 1)  # header only - no runs for this owner

    def test_ai_analytics_never_fabricates_a_dollar_cost(self):
        data = services.get_owner_analytics(self.owner, {})
        self.assertNotIn("cost_usd", data["ai"])
        self.assertIn("billing_note", data["ai"])
