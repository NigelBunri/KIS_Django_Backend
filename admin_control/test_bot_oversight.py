"""
Phase 5 — KIS Bot Platform superadmin oversight. Mirrors
admin_control/test_incidents.py's own access-control test conventions
(_grant_role helper, plain is_staff NOT being sufficient on its own).
Covers: access restriction, platform stats, bot investigation (with
the privacy boundary - no knowledge/memory content, no plaintext
secrets), suspend/reinstate, credential revocation, and audit records.

Run:
  python3 manage.py test admin_control.test_bot_oversight apps.bots --keepdb -v 2
"""
from __future__ import annotations

from django.test import TestCase
from rest_framework.test import APIClient

from admin_control.models import AdminAuditEntry
from admin_control.roles import AdminRole, AdminRoleAssignment, AdminRolePermission
from apps.accounts.tiers import ensure_default_account_tiers
from apps.bots import services
from apps.bots.models import BotKind, BotKnowledgeSource, BotMemoryConfig, BotMemoryEntry, BotPermissionScope, BotResourceType, BotStatus
from apps.bots.test_workflows import _make_user, _uuid_suffix
from apps.groups.models import GroupMembership, GroupRole
from apps.groups.services import create_group_with_conversation

LIST_URL = "/control/admin/bots/"
STATS_URL = "/control/admin/bots/stats/"
ABUSE_URL = "/control/admin/bots/abuse/"


def _grant_role(user, *, permissions):
    role = AdminRole.objects.create(name=f"role-{user.id}-{_uuid_suffix()}")
    AdminRolePermission.objects.create(role=role, app_label="*", permissions=permissions)
    AdminRoleAssignment.objects.create(user=user, role=role, is_active=True)


def _grant_super_admin(user):
    role = AdminRole.objects.create(name=f"super-{user.id}-{_uuid_suffix()}", is_super_role=True)
    AdminRoleAssignment.objects.create(user=user, role=role, is_active=True)


def _active_bot(owner, name="OversightBot"):
    bot = services.create_bot(owner, name=f"{name}-{_uuid_suffix()}", kind=BotKind.CUSTOM)
    services.set_status(bot, owner, BotStatus.ACTIVE)
    return bot


def _detail_url(bot_id):
    return f"{LIST_URL}{bot_id}/"


class BotOversightAccessControlTests(TestCase):
    def setUp(self):
        ensure_default_account_tiers()
        self.client = APIClient()
        self.owner = _make_user("oversight-access-owner@example.com", tier_name="Business")
        self.bot = _active_bot(self.owner)

    def test_plain_staff_without_admin_control_role_is_denied(self):
        staff = _make_user("oversight-plain-staff@example.com")
        staff.is_staff = True
        staff.save(update_fields=["is_staff"])
        self.client.force_authenticate(staff)
        response = self.client.get(LIST_URL)
        self.assertEqual(response.status_code, 403)

    def test_ordinary_user_is_denied(self):
        ordinary = _make_user("oversight-ordinary@example.com")
        self.client.force_authenticate(ordinary)
        response = self.client.get(LIST_URL)
        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_is_denied(self):
        response = self.client.get(LIST_URL)
        self.assertEqual(response.status_code, 401)

    def test_user_with_bots_view_permission_is_allowed(self):
        admin = _make_user("oversight-perm-admin@example.com")
        _grant_role(admin, permissions=["bots.view"])
        self.client.force_authenticate(admin)
        response = self.client.get(LIST_URL)
        self.assertEqual(response.status_code, 200)

    def test_super_admin_is_allowed(self):
        super_admin = _make_user("oversight-super@example.com")
        _grant_super_admin(super_admin)
        self.client.force_authenticate(super_admin)
        response = self.client.get(LIST_URL)
        self.assertEqual(response.status_code, 200)


class BotOversightInvestigationTests(TestCase):
    def setUp(self):
        ensure_default_account_tiers()
        self.client = APIClient()
        self.admin = _make_user("oversight-investigate-admin@example.com")
        _grant_super_admin(self.admin)
        self.client.force_authenticate(self.admin)

        self.owner = _make_user("oversight-investigate-owner@example.com", tier_name="Business")
        self.group = create_group_with_conversation(owner=self.owner, name="OversightG", slug=f"oversightg-{_uuid_suffix()}")
        self.bot = _active_bot(self.owner)
        self.grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            scopes=[BotPermissionScope.SEND_MESSAGES],
        )

    def test_list_shows_the_real_bot(self):
        response = self.client.get(LIST_URL)
        self.assertEqual(response.status_code, 200)
        names = [b["name"] for b in response.json()["bots"]]
        self.assertIn(self.bot.name, names)

    def test_list_search_filters_by_name(self):
        response = self.client.get(LIST_URL, {"q": self.bot.name})
        self.assertEqual(len(response.json()["bots"]), 1)
        response_miss = self.client.get(LIST_URL, {"q": "definitely-not-a-real-bot-name"})
        self.assertEqual(len(response_miss.json()["bots"]), 0)

    def test_detail_shows_resource_connections_and_logs_audit_view(self):
        before = AdminAuditEntry.objects.filter(action_type="bot.admin_viewed").count()
        response = self.client.get(_detail_url(self.bot.id))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["bot"]["name"], self.bot.name)
        self.assertEqual(len(body["resource_connections"]), 1)
        self.assertEqual(body["resource_connections"][0]["resource_display_name"], "OversightG")
        after = AdminAuditEntry.objects.filter(action_type="bot.admin_viewed").count()
        self.assertEqual(after, before + 1)

    def test_detail_never_exposes_credential_secret_or_ai_api_key(self):
        services.issue_credential(self.bot, self.owner)
        services.connect_ai_provider(self.bot, self.owner, provider="anthropic", api_key="sk-ant-should-never-leak")
        response = self.client.get(_detail_url(self.bot.id))
        body_text = response.content.decode()
        self.assertNotIn("sk-ant-should-never-leak", body_text)
        self.assertIn("has_api_key", response.json()["ai_connection"])
        self.assertTrue(response.json()["ai_connection"]["has_api_key"])
        self.assertNotIn("client_secret_hash", body_text)

    def test_detail_never_exposes_knowledge_or_memory_content(self):
        source = BotKnowledgeSource.objects.create(
            bot=self.bot, source_type="text", title="Private note",
            extracted_text="THIS SHOULD NEVER APPEAR IN ADMIN RESPONSES", status="ready",
        )
        config = BotMemoryConfig.objects.create(bot=self.bot, enabled=True)
        BotMemoryEntry.objects.create(bot=self.bot, content="THIS MEMORY CONTENT SHOULD NEVER LEAK EITHER")

        response = self.client.get(_detail_url(self.bot.id))
        body_text = response.content.decode()
        self.assertNotIn("THIS SHOULD NEVER APPEAR IN ADMIN RESPONSES", body_text)
        self.assertNotIn("THIS MEMORY CONTENT SHOULD NEVER LEAK EITHER", body_text)
        # Metadata (counts/status) IS expected to be present.
        self.assertEqual(response.json()["knowledge"]["total_sources"], 1)
        self.assertEqual(response.json()["memory"]["entry_count"], 1)
        self.assertTrue(response.json()["memory"]["enabled"])

    def test_nonexistent_bot_is_404(self):
        response = self.client.get(_detail_url("00000000-0000-0000-0000-000000000000"))
        self.assertEqual(response.status_code, 404)


class BotOversightAdministrativeActionsTests(TestCase):
    def setUp(self):
        ensure_default_account_tiers()
        self.client = APIClient()
        self.admin = _make_user("oversight-actions-admin@example.com")
        _grant_super_admin(self.admin)
        self.client.force_authenticate(self.admin)

        self.owner = _make_user("oversight-actions-owner@example.com", tier_name="Business")
        self.bot = _active_bot(self.owner)

    def test_suspend_takes_effect_and_is_audited(self):
        response = self.client.patch(_detail_url(self.bot.id), {"status": "suspended", "reason": "policy violation"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.status, BotStatus.SUSPENDED)
        self.assertTrue(AdminAuditEntry.objects.filter(action_type="bot.admin_status_changed", target_pk=str(self.bot.id)).exists())
        # The owner can no longer self-service out of SUSPENDED (Phase 1
        # behaviour preserved) - only another admin PATCH or set_status_admin can.
        with self.assertRaises(Exception):
            services.set_status(self.bot, self.owner, BotStatus.ACTIVE)

    def test_reinstate_after_suspend(self):
        self.client.patch(_detail_url(self.bot.id), {"status": "suspended"}, format="json")
        response = self.client.patch(_detail_url(self.bot.id), {"status": "active"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.status, BotStatus.ACTIVE)

    def test_invalid_status_rejected(self):
        response = self.client.patch(_detail_url(self.bot.id), {"status": "deleted"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_credential_revocation_takes_effect_immediately(self):
        credential, _secret = services.issue_credential(self.bot, self.owner)
        response = self.client.post(f"{LIST_URL}{self.bot.id}/credential/revoke/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["revoked"])
        credential.refresh_from_db()
        self.assertFalse(credential.is_active)
        self.assertTrue(AdminAuditEntry.objects.filter(action_type="bot.admin_credential_revoked").exists())

    def test_revoking_with_no_credential_is_a_safe_no_op(self):
        response = self.client.post(f"{LIST_URL}{self.bot.id}/credential/revoke/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["revoked"])

    def test_admin_note_is_recorded_as_immutable_audit_entry(self):
        response = self.client.post(f"{LIST_URL}{self.bot.id}/notes/", {"note": "Contacted owner about suspicious activity."}, format="json")
        self.assertEqual(response.status_code, 201)
        entry = AdminAuditEntry.objects.get(action_type="bot.admin_note", target_pk=str(self.bot.id))
        self.assertEqual(entry.metadata["note"], "Contacted owner about suspicious activity.")

    def test_empty_note_rejected(self):
        response = self.client.post(f"{LIST_URL}{self.bot.id}/notes/", {"note": "  "}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_ordinary_user_cannot_suspend_a_bot_they_dont_own(self):
        outsider = _make_user("oversight-actions-outsider@example.com")
        self.client.force_authenticate(outsider)
        response = self.client.patch(_detail_url(self.bot.id), {"status": "suspended"}, format="json")
        self.assertEqual(response.status_code, 403)
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.status, BotStatus.ACTIVE)


class BotOversightStatsAndAbuseTests(TestCase):
    def setUp(self):
        ensure_default_account_tiers()
        self.client = APIClient()
        self.admin = _make_user("oversight-stats-admin@example.com")
        _grant_super_admin(self.admin)
        self.client.force_authenticate(self.admin)
        self.owner = _make_user("oversight-stats-owner@example.com", tier_name="Business")
        self.bot = _active_bot(self.owner)

    def test_stats_reflects_real_bot_count(self):
        response = self.client.get(STATS_URL)
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.json()["total_bots"], 1)
        self.assertGreaterEqual(response.json()["active_bots"], 1)
        self.assertEqual(len(response.json()["creation_series_30d"]), 30)

    def test_abuse_feed_shows_real_suspension_event(self):
        services.set_status_admin(self.bot, self.admin, BotStatus.SUSPENDED, reason="abuse test")
        response = self.client.get(ABUSE_URL)
        self.assertEqual(response.status_code, 200)
        actions = [e["action"] for e in response.json()["events"]]
        self.assertIn("suspended_by_admin", actions)

    def test_abuse_feed_denied_to_non_admin(self):
        self.client.force_authenticate(self.owner)
        response = self.client.get(ABUSE_URL)
        self.assertEqual(response.status_code, 403)
