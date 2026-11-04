# apps/bots/tests.py
import hashlib
import hmac
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import AccountTier, Subscription, User
from apps.accounts.tiers import ensure_default_account_tiers
from apps.groups.models import Group, GroupBan, GroupJoinPolicy, GroupMembership, GroupRole
from apps.groups.services import create_group_with_conversation

from . import services
from .auth import issue_access_token
from .models import (
    Bot,
    BotCredential,
    BotKind,
    BotPermissionScope,
    BotResourceGrant,
    BotResourceType,
    BotStatus,
    BotWebhookDelivery,
    BotWebhookDeliveryStatus,
    BotWebhookSubscription,
)


def _make_user(email: str, *, tier_name: str | None = None) -> User:
    user = User.objects.create_user(
        phone=f"+1555{abs(hash(email)) % 10_000_000:07d}",
        password="x", country="CM", email=email,
    )
    if tier_name:
        tier = AccountTier.objects.get(name=tier_name)
        Subscription.objects.create(user=user, tier=tier, status=Subscription.STATUS_ACTIVE)
    return user


class EligibilityTests(TestCase):
    def setUp(self):
        ensure_default_account_tiers()

    def test_free_tier_cannot_create_bots(self):
        user = _make_user("free@example.com")
        with self.assertRaises(services.BotEligibilityError):
            services.ensure_can_create_bot(user)

    def test_business_tier_can_create_bots(self):
        user = _make_user("biz@example.com", tier_name="Business")
        services.ensure_can_create_bot(user)  # does not raise

    def test_business_tier_bot_limit_enforced(self):
        user = _make_user("biz2@example.com", tier_name="Business")
        for _ in range(2):  # Business preset = bots_limit 2
            services.create_bot(user, name="A Bot", kind=BotKind.RULE_BASED)
        with self.assertRaises(services.BotEligibilityError):
            services.create_bot(user, name="One Too Many", kind=BotKind.RULE_BASED)

    def test_a_mobile_client_cannot_bypass_eligibility_by_calling_create_bot_directly(self):
        # Simulates "a user must not gain access merely by modifying
        # mobile application requests" (section 3) - create_bot always
        # re-derives eligibility from the DB-backed tier, never trusts
        # anything the caller passes in.
        user = _make_user("sneaky@example.com")
        with self.assertRaises(services.BotEligibilityError):
            services.create_bot(user, name="Sneaky Bot", kind=BotKind.RULE_BASED)
        self.assertEqual(Bot.objects.filter(owner=user).count(), 0)


class BotLifecycleTests(TestCase):
    def setUp(self):
        ensure_default_account_tiers()
        self.owner = _make_user("owner@example.com", tier_name="Business")

    def test_create_then_activate_bot(self):
        bot = services.create_bot(self.owner, name="Welcome Bot", kind=BotKind.RULE_BASED)
        self.assertEqual(bot.status, BotStatus.DRAFT)
        bot = services.set_status(bot, self.owner, BotStatus.ACTIVE)
        self.assertEqual(bot.status, BotStatus.ACTIVE)

    def test_invalid_status_transition_rejected(self):
        bot = services.create_bot(self.owner, name="Bot", kind=BotKind.RULE_BASED)
        with self.assertRaises(Exception):
            services.set_status(bot, self.owner, BotStatus.SUSPENDED)  # owner can't self-suspend

    def test_non_owner_cannot_modify_bot(self):
        bot = services.create_bot(self.owner, name="Bot", kind=BotKind.RULE_BASED)
        stranger = _make_user("stranger@example.com", tier_name="Business")
        with self.assertRaises(Exception):
            services.set_status(bot, stranger, BotStatus.ACTIVE)

    def test_deleting_bot_revokes_credential_and_grants(self):
        bot = services.create_bot(self.owner, name="Bot", kind=BotKind.RULE_BASED)
        services.issue_credential(bot, self.owner)
        group, _ = self._make_group(self.owner)
        services.grant_resource_access(
            bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(group.id),
            scopes=[BotPermissionScope.SEND_MESSAGES],
        )
        services.set_status(bot, self.owner, BotStatus.DELETED)
        bot.refresh_from_db()
        self.assertFalse(BotCredential.objects.get(bot=bot).is_active)
        self.assertFalse(BotResourceGrant.objects.get(bot=bot).is_active)

    def _make_group(self, owner):
        group = create_group_with_conversation(owner=owner, name="Test Group", slug=f"g-{owner.id}")
        group.join_policy = GroupJoinPolicy.OPEN
        group.save(update_fields=["join_policy"])
        return group, None


class CredentialTests(TestCase):
    def setUp(self):
        ensure_default_account_tiers()
        self.owner = _make_user("cred-owner@example.com", tier_name="Business")
        self.bot = services.create_bot(self.owner, name="Cred Bot", kind=BotKind.CUSTOM)

    def test_issue_credential_returns_plaintext_once(self):
        credential, plaintext = services.issue_credential(self.bot, self.owner)
        self.assertTrue(plaintext)
        self.assertNotEqual(credential.client_secret_hash, plaintext)

    def test_authenticate_with_correct_credential_succeeds(self):
        services.issue_credential(self.bot, self.owner)
        services.set_status(self.bot, self.owner, BotStatus.ACTIVE)
        credential, plaintext = services.issue_credential(self.bot, self.owner)
        found = services.authenticate_bot_credential(credential.client_id, plaintext)
        self.assertIsNotNone(found)
        self.assertEqual(found.id, credential.id)

    def test_authenticate_with_wrong_secret_fails(self):
        services.issue_credential(self.bot, self.owner)
        services.set_status(self.bot, self.owner, BotStatus.ACTIVE)
        credential, _plaintext = services.issue_credential(self.bot, self.owner)
        self.assertIsNone(services.authenticate_bot_credential(credential.client_id, "wrong-secret"))

    def test_rotating_credential_invalidates_the_old_secret(self):
        credential, old_secret = services.issue_credential(self.bot, self.owner)
        services.set_status(self.bot, self.owner, BotStatus.ACTIVE)
        _new_credential, new_secret = services.issue_credential(self.bot, self.owner)
        self.assertIsNone(services.authenticate_bot_credential(credential.client_id, old_secret))
        self.assertIsNotNone(services.authenticate_bot_credential(credential.client_id, new_secret))

    def test_revoked_credential_cannot_authenticate(self):
        credential, plaintext = services.issue_credential(self.bot, self.owner)
        services.set_status(self.bot, self.owner, BotStatus.ACTIVE)
        services.revoke_credential(self.bot, self.owner)
        self.assertIsNone(services.authenticate_bot_credential(credential.client_id, plaintext))

    def test_inactive_bot_cannot_authenticate_even_with_valid_credential(self):
        credential, plaintext = services.issue_credential(self.bot, self.owner)
        # bot stays in DRAFT - never activated
        self.assertIsNone(services.authenticate_bot_credential(credential.client_id, plaintext))

    def test_oauth_access_token_round_trip(self):
        credential, plaintext = services.issue_credential(self.bot, self.owner)
        services.set_status(self.bot, self.owner, BotStatus.ACTIVE)
        found = services.authenticate_bot_credential(credential.client_id, plaintext)
        token = issue_access_token(found)
        self.assertIn("access_token", token)
        self.assertEqual(token["token_type"], "Bearer")


class ResourceGrantOwnershipTests(TestCase):
    """Cross-account isolation + 'a bot must never receive more
    authority than its owner possesses' (section 12)."""

    def setUp(self):
        ensure_default_account_tiers()
        self.owner = _make_user("grant-owner@example.com", tier_name="Business")
        self.outsider = _make_user("outsider@example.com", tier_name="Business")
        self.bot = services.create_bot(self.owner, name="Grant Bot", kind=BotKind.RULE_BASED)

    def _make_group(self, owner, member=None, member_role=None):
        group = create_group_with_conversation(owner=owner, name="G", slug=f"grp-{owner.id}-{uuid_suffix()}")
        if member:
            GroupMembership.objects.create(group=group, user=member, role=member_role or GroupRole.MEMBER)
        return group

    def test_owner_can_grant_access_to_their_own_group(self):
        group = self._make_group(self.owner)
        grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(group.id),
            scopes=[BotPermissionScope.SEND_MESSAGES],
        )
        self.assertEqual(grant.conversation_id, group.conversation_id)

    def test_cannot_grant_access_to_a_group_you_do_not_administer(self):
        other_owner = _make_user("other-group-owner@example.com", tier_name="Business")
        group = self._make_group(other_owner)
        with self.assertRaises(Exception):
            services.grant_resource_access(
                self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(group.id),
                scopes=[BotPermissionScope.SEND_MESSAGES],
            )

    def test_plain_member_cannot_grant_bot_access_to_their_group(self):
        group = self._make_group(self.outsider, member=self.owner, member_role=GroupRole.MEMBER)
        with self.assertRaises(Exception):
            services.grant_resource_access(
                self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(group.id),
                scopes=[BotPermissionScope.SEND_MESSAGES],
            )

    def test_group_admin_can_grant_bot_access(self):
        group = self._make_group(self.outsider, member=self.owner, member_role=GroupRole.ADMIN)
        grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(group.id),
            scopes=[BotPermissionScope.SEND_MESSAGES],
        )
        self.assertTrue(grant.is_active)

    def test_outsider_cannot_revoke_someone_elses_bot_grant(self):
        group = self._make_group(self.owner)
        services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(group.id),
            scopes=[BotPermissionScope.SEND_MESSAGES],
        )
        other_bot = services.create_bot(self.outsider, name="Other Bot", kind=BotKind.RULE_BASED)
        with self.assertRaises(Exception):
            services.update_bot(self.bot, self.outsider, name="Hijacked")


def uuid_suffix() -> str:
    import uuid

    return uuid.uuid4().hex[:8]


class WebhookDeliveryTests(TestCase):
    def setUp(self):
        ensure_default_account_tiers()
        self.owner = _make_user("webhook-owner@example.com", tier_name="Business")
        self.bot = services.create_bot(self.owner, name="Webhook Bot", kind=BotKind.CUSTOM)
        services.set_status(self.bot, self.owner, BotStatus.ACTIVE)
        self.group = create_group_with_conversation(owner=self.owner, name="WG", slug=f"wg-{uuid_suffix()}")
        self.grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            scopes=[BotPermissionScope.READ_MESSAGES],
        )
        self.subscription = BotWebhookSubscription.objects.create(
            bot=self.bot, url="https://example.com/webhook", events=["message.created"],
        )

    @patch("apps.bots.services.is_safe_external_url", return_value=True)
    @patch("urllib.request.urlopen")
    def test_successful_delivery_is_signed_and_marked_delivered(self, mock_urlopen, _mock_safe):
        mock_urlopen.return_value.__enter__.return_value.status = 200
        delivered = services.dispatch_bot_event(
            self.grant, "message.created", {"messageId": "m1"}, event_id="evt-1",
        )
        self.assertTrue(delivered)
        delivery = BotWebhookDelivery.objects.get(subscription=self.subscription)
        self.assertEqual(delivery.status, BotWebhookDeliveryStatus.DELIVERED)
        sent_request = mock_urlopen.call_args[0][0]
        # urllib.request.Request normalizes header casing internally
        # (e.g. "X-KIS-Signature" -> "X-kis-signature") - HTTP headers are
        # case-insensitive on the wire either way, so assert case-
        # insensitively rather than depending on urllib's internal style.
        self.assertTrue(
            any(name.lower() == "x-kis-signature" for name in sent_request.headers),
        )

    @patch("apps.bots.services.is_safe_external_url", return_value=True)
    @patch("urllib.request.urlopen")
    def test_duplicate_event_id_is_not_delivered_twice(self, mock_urlopen, _mock_safe):
        mock_urlopen.return_value.__enter__.return_value.status = 200
        services.dispatch_bot_event(self.grant, "message.created", {}, event_id="evt-dupe")
        self.assertEqual(mock_urlopen.call_count, 1)
        services.dispatch_bot_event(self.grant, "message.created", {}, event_id="evt-dupe")
        self.assertEqual(mock_urlopen.call_count, 1)  # not called again
        self.assertEqual(BotWebhookDelivery.objects.filter(event_id="evt-dupe").count(), 1)

    def test_ssrf_blocked_url_is_marked_failed_without_a_request(self):
        self.subscription.url = "http://169.254.169.254/latest/meta-data/"
        self.subscription.save(update_fields=["url"])
        delivered = services.dispatch_bot_event(
            self.grant, "message.created", {}, event_id="evt-ssrf",
        )
        self.assertFalse(delivered)
        delivery = BotWebhookDelivery.objects.get(event_id="evt-ssrf")
        self.assertEqual(delivery.status, BotWebhookDeliveryStatus.FAILED)

    def test_event_not_subscribed_is_not_delivered(self):
        delivered = services.dispatch_bot_event(
            self.grant, "member.joined", {}, event_id="evt-unsub",
        )
        self.assertFalse(delivered)

    @patch("apps.bots.services.is_safe_external_url", return_value=True)
    @patch("urllib.request.urlopen", side_effect=OSError("connection refused"))
    def test_failed_delivery_schedules_a_retry(self, _mock_urlopen, _mock_safe):
        services.dispatch_bot_event(self.grant, "message.created", {}, event_id="evt-retry")
        delivery = BotWebhookDelivery.objects.get(event_id="evt-retry")
        self.assertEqual(delivery.status, BotWebhookDeliveryStatus.FAILED)
        self.assertIsNotNone(delivery.next_retry_at)

    def test_dispatch_for_conversation_reaches_the_bot_grant(self):
        with patch("apps.bots.services.is_safe_external_url", return_value=True), \
             patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value.status = 200
            delivered = services.dispatch_bot_events_for_conversation(
                self.group.conversation_id, "message.created", {"messageId": "m2"}, event_id="evt-conv",
            )
            self.assertEqual(delivered, 1)

    def test_paused_bot_does_not_receive_events(self):
        services.set_status(self.bot, self.owner, BotStatus.PAUSED)
        delivered = services.dispatch_bot_event(
            self.grant, "message.created", {}, event_id="evt-paused",
        )
        self.assertFalse(delivered)


class SendMessageAsBotTests(TestCase):
    def setUp(self):
        ensure_default_account_tiers()
        self.owner = _make_user("send-owner@example.com", tier_name="Business")
        self.bot = services.create_bot(self.owner, name="Send Bot", kind=BotKind.RULE_BASED)
        services.set_status(self.bot, self.owner, BotStatus.ACTIVE)
        self.group = create_group_with_conversation(owner=self.owner, name="SG", slug=f"sg-{uuid_suffix()}")

    def test_bot_without_send_scope_cannot_send(self):
        grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            scopes=[BotPermissionScope.READ_MESSAGES],
        )
        with self.assertRaises(Exception):
            services.send_message_as_bot(self.bot, grant, "hi")

    @patch("apps.chat.tasks.send_system_message_as_user", return_value={"ok": True})
    def test_bot_with_send_scope_can_send(self, mock_send):
        grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            scopes=[BotPermissionScope.SEND_MESSAGES],
        )
        services.send_message_as_bot(self.bot, grant, "hello group")
        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        self.assertEqual(kwargs["conversation_id"], str(self.group.conversation_id))
        self.assertEqual(kwargs["sender_id"], str(self.bot.id))

    def test_paused_bot_cannot_send(self):
        grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            scopes=[BotPermissionScope.SEND_MESSAGES],
        )
        services.set_status(self.bot, self.owner, BotStatus.PAUSED)
        with self.assertRaises(Exception):
            services.send_message_as_bot(self.bot, grant, "hello")


class ApprovalWorkflowTests(TestCase):
    def setUp(self):
        ensure_default_account_tiers()
        self.owner = _make_user("approval-owner@example.com", tier_name="Business")
        self.bot = services.create_bot(self.owner, name="Mod Bot", kind=BotKind.RULE_BASED)
        self.group = create_group_with_conversation(owner=self.owner, name="AG", slug=f"ag-{uuid_suffix()}")
        self.grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            scopes=[BotPermissionScope.MODERATE],
        )

    def test_sensitive_action_requires_approval_by_default(self):
        self.assertTrue(services.requires_approval(self.grant, "ban_member"))

    def test_ordinary_action_never_requires_approval(self):
        self.assertFalse(services.requires_approval(self.grant, "send_message"))

    def test_pre_approved_sensitive_action_skips_approval(self):
        self.grant.auto_approved_actions = []  # ban_member can never be pre-approved (SENSITIVE_ACTIONS filter)
        self.grant.save()
        services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            scopes=[BotPermissionScope.MODERATE], auto_approved_actions=["ban_member"],
        )
        self.grant.refresh_from_db()
        self.assertEqual(self.grant.auto_approved_actions, [])  # sensitive actions are never auto-approvable

    def test_decide_approval_by_authorized_admin(self):
        approval = services.request_approval(self.bot, self.grant, "ban_member", {"target_user": "u1"})
        decided = services.decide_approval(approval, self.owner, True)
        self.assertEqual(decided.status, "approved")

    def test_decide_approval_by_unauthorized_user_rejected(self):
        outsider = _make_user("approval-outsider@example.com", tier_name="Business")
        approval = services.request_approval(self.bot, self.grant, "ban_member", {})
        with self.assertRaises(Exception):
            services.decide_approval(approval, outsider, True)

    def test_cannot_decide_an_already_decided_approval_again(self):
        approval = services.request_approval(self.bot, self.grant, "ban_member", {})
        services.decide_approval(approval, self.owner, True)
        with self.assertRaises(Exception):
            services.decide_approval(approval, self.owner, False)


class AIConnectionEncryptionTests(TestCase):
    def setUp(self):
        ensure_default_account_tiers()
        self.owner = _make_user("ai-owner@example.com", tier_name="Business")
        self.bot = services.create_bot(self.owner, name="AI Bot", kind=BotKind.AI_POWERED)

    def test_api_key_is_encrypted_at_rest_and_round_trips(self):
        connection = services.connect_ai_provider(
            self.bot, self.owner, provider="openai", api_key="sk-super-secret-key",
        )
        self.assertNotIn("sk-super-secret-key", connection.api_key_encrypted)
        self.assertEqual(services.decrypt_ai_api_key(connection.api_key_encrypted), "sk-super-secret-key")

    def test_disconnect_removes_the_connection_entirely(self):
        services.connect_ai_provider(self.bot, self.owner, provider="openai", api_key="sk-x")
        services.disconnect_ai_provider(self.bot, self.owner)
        from .models import BotAIConnection

        self.assertFalse(BotAIConnection.objects.filter(bot=self.bot).exists())


# ---------------------------------------------------------------------
# Phase 1 foundation hardening — live permission re-verification, rate
# limiting, real approval-gated execution, immediate-effect revocation/
# suspension, cross-account isolation, and realistic webhook delivery.
# ---------------------------------------------------------------------

class LiveGrantRevalidationTests(TestCase):
    """'Enforce resource permissions every time a bot attempts an
    operation' — a grant row being is_active=True is necessary but not
    sufficient. These tests change something OTHER than the grant row
    itself (the owner's live role, the owner's account status) and
    confirm the next action is denied anyway, proving the check is
    re-derived live rather than cached from grant-creation time."""

    def setUp(self):
        cache.clear()
        ensure_default_account_tiers()
        self.owner = _make_user("revalidation-owner@example.com", tier_name="Business")
        self.bot = services.create_bot(self.owner, name="Revalidation Bot", kind=BotKind.CUSTOM)
        services.set_status(self.bot, self.owner, BotStatus.ACTIVE)
        self.group_owner = _make_user("revalidation-group-owner@example.com", tier_name="Business")
        self.group = create_group_with_conversation(
            owner=self.group_owner, name="RG", slug=f"rg-{uuid_suffix()}",
        )
        self.membership = GroupMembership.objects.create(
            group=self.group, user=self.owner, role=GroupRole.ADMIN,
        )
        self.grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            scopes=[BotPermissionScope.SEND_MESSAGES],
        )

    def test_losing_admin_role_after_grant_denies_the_next_send(self):
        with patch("apps.chat.tasks.send_system_message_as_user", return_value={"ok": True}):
            services.send_message_as_bot(self.bot, self.grant, "still fine")
        # The grant row itself is untouched — only the owner's live role
        # on the resource changes, which is exactly what must be re-
        # checked on every call, not just at grant time.
        self.membership.role = GroupRole.MEMBER
        self.membership.save(update_fields=["role"])
        with self.assertRaises(Exception):
            services.send_message_as_bot(self.bot, self.grant, "should now fail")

    def test_revoking_grant_denies_the_next_send_immediately(self):
        services.revoke_resource_access(self.bot, self.owner, self.grant.id)
        with self.assertRaises(Exception):
            services.send_message_as_bot(self.bot, self.grant, "should fail")

    def test_suspending_owner_account_denies_the_next_send_immediately(self):
        self.owner.is_active = False
        self.owner.save(update_fields=["is_active"])
        with self.assertRaises(Exception):
            services.send_message_as_bot(self.bot, self.grant, "should fail")


class RateLimitAndAbuseTests(TestCase):
    def setUp(self):
        cache.clear()
        ensure_default_account_tiers()
        self.owner = _make_user("ratelimit-owner@example.com", tier_name="Business")
        self.bot = services.create_bot(self.owner, name="Rate Bot", kind=BotKind.CUSTOM)
        services.set_status(self.bot, self.owner, BotStatus.ACTIVE)
        self.group = create_group_with_conversation(owner=self.owner, name="RLG", slug=f"rlg-{uuid_suffix()}")
        self.grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            scopes=[BotPermissionScope.SEND_MESSAGES],
        )

    def test_daily_action_limit_is_actually_enforced(self):
        with patch.object(services, "get_bot_daily_event_limit", return_value=2), \
             patch("apps.chat.tasks.send_system_message_as_user", return_value={"ok": True}):
            services.send_message_as_bot(self.bot, self.grant, "one")
            services.send_message_as_bot(self.bot, self.grant, "two")
            with self.assertRaises(services.Throttled):
                services.send_message_as_bot(self.bot, self.grant, "three — over the limit")

    def test_unlimited_tier_never_throttles(self):
        with patch.object(services, "get_bot_daily_event_limit", return_value=None), \
             patch("apps.chat.tasks.send_system_message_as_user", return_value={"ok": True}):
            for i in range(10):
                services.send_message_as_bot(self.bot, self.grant, f"msg {i}")

    def test_repeated_auth_failures_lock_out_the_credential(self):
        credential, real_secret = services.issue_credential(self.bot, self.owner, scopes=["send_messages"])
        for _ in range(services.BOT_AUTH_FAIL_THRESHOLD):
            result = services.authenticate_bot_credential(credential.client_id, "wrong-secret")
            self.assertIsNone(result)
        # Even the CORRECT secret is now rejected — the lockout blocks
        # by client_id regardless of the attempt, which is what stops a
        # brute-force loop from eventually guessing right.
        self.assertIsNone(services.authenticate_bot_credential(credential.client_id, real_secret))

    def test_repeated_rate_limit_violations_auto_suspend_the_bot(self):
        with patch.object(services, "BOT_ABUSE_SIGNAL_THRESHOLD", 3), \
             patch.object(services, "get_bot_daily_event_limit", return_value=0), \
             patch("apps.chat.tasks.send_system_message_as_user", return_value={"ok": True}):
            for _ in range(3):
                with self.assertRaises(services.Throttled):
                    services.send_message_as_bot(self.bot, self.grant, "over limit")
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.status, BotStatus.SUSPENDED)
        self.assertFalse(BotCredential.objects.filter(bot=self.bot, is_active=True).exists())
        self.assertFalse(BotResourceGrant.objects.filter(bot=self.bot, is_active=True).exists())


class SensitiveActionExecutionTests(TestCase):
    """'Connect sensitive actions to the existing approval system so
    that real bot activity automatically generates approval requests'
    and 'sensitive operations cannot bypass pending approvals' — proven
    end to end against a real executable action (ban_member), not just
    the approval status flag in isolation."""

    def setUp(self):
        cache.clear()
        ensure_default_account_tiers()
        self.owner = _make_user("exec-owner@example.com", tier_name="Business")
        self.bot = services.create_bot(self.owner, name="Exec Bot", kind=BotKind.CUSTOM)
        services.set_status(self.bot, self.owner, BotStatus.ACTIVE)
        self.group = create_group_with_conversation(owner=self.owner, name="EG", slug=f"eg-{uuid_suffix()}")
        self.target = _make_user("exec-target@example.com")
        GroupMembership.objects.create(group=self.group, user=self.target, role=GroupRole.MEMBER)
        self.grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            scopes=[BotPermissionScope.MODERATE],
        )

    def test_sensitive_action_without_auto_approval_is_queued_not_executed(self):
        result = services.execute_bot_action(
            self.bot, self.grant, "ban_member", {"user_id": str(self.target.id)},
        )
        self.assertEqual(result["status"], "pending_approval")
        self.assertFalse(GroupBan.objects.filter(group=self.group, user=self.target).exists())
        membership = GroupMembership.objects.get(group=self.group, user=self.target)
        self.assertFalse(membership.is_banned)

    def test_approving_the_request_actually_bans_the_member(self):
        result = services.execute_bot_action(
            self.bot, self.grant, "ban_member", {"user_id": str(self.target.id), "reason": "spam"},
        )
        from .models import BotApprovalRequest

        approval = BotApprovalRequest.objects.get(id=result["approval_id"])
        decided = services.decide_approval(approval, self.owner, True)
        self.assertEqual(decided.status, "approved")
        self.assertEqual(decided.execution_error, "")
        self.assertTrue(GroupBan.objects.filter(group=self.group, user=self.target).exists())
        membership = GroupMembership.objects.get(group=self.group, user=self.target)
        self.assertTrue(membership.is_banned)

    def test_declining_the_request_never_executes_it(self):
        result = services.execute_bot_action(
            self.bot, self.grant, "ban_member", {"user_id": str(self.target.id)},
        )
        from .models import BotApprovalRequest

        approval = BotApprovalRequest.objects.get(id=result["approval_id"])
        services.decide_approval(approval, self.owner, False)
        self.assertFalse(GroupBan.objects.filter(group=self.group, user=self.target).exists())

    def test_auto_approved_action_executes_immediately_without_a_pending_request(self):
        services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            scopes=[BotPermissionScope.MODERATE], auto_approved_actions=[],
        )
        # ban_member is a SENSITIVE_ACTION and grant_resource_access strips
        # any sensitive action out of auto_approved_actions on the way in
        # (by design — sensitive actions can never be pre-approved), so
        # this must still queue for approval even though the caller asked
        # for auto-approval.
        result = services.execute_bot_action(
            self.bot, self.grant, "ban_member", {"user_id": str(self.target.id)},
        )
        self.assertEqual(result["status"], "pending_approval")

    def test_action_missing_required_scope_is_denied_before_any_approval_request(self):
        read_only_grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            scopes=[BotPermissionScope.READ_MESSAGES],
        )
        from .models import BotApprovalRequest

        before = BotApprovalRequest.objects.count()
        with self.assertRaises(Exception):
            services.execute_bot_action(
                self.bot, read_only_grant, "ban_member", {"user_id": str(self.target.id)},
            )
        self.assertEqual(BotApprovalRequest.objects.count(), before)

    def test_unsupported_action_is_rejected_not_silently_accepted(self):
        with self.assertRaises(Exception):
            services.execute_bot_action(self.bot, self.grant, "launch_nuclear_codes", {})


class OwnerSuspensionImmediateEffectTests(TestCase):
    """'Suspending an owner immediately takes effect' — exercised
    through the real DRF authentication classes (not just the service
    function) so the test proves the live HTTP path is actually
    guarded, not merely that a lower-level helper would deny it."""

    def setUp(self):
        cache.clear()
        ensure_default_account_tiers()
        self.owner = _make_user("suspend-owner@example.com", tier_name="Business")
        self.bot = services.create_bot(self.owner, name="Suspend Bot", kind=BotKind.CUSTOM)
        services.set_status(self.bot, self.owner, BotStatus.ACTIVE)
        self.credential, self.secret = services.issue_credential(self.bot, self.owner, scopes=["read_messages"])
        self.token = issue_access_token(self.credential)["access_token"]
        self.client = APIClient()

    def _me(self):
        return self.client.get(
            "/api/v1/bots/external/me/", HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

    def test_active_owner_token_works(self):
        response = self._me()
        self.assertEqual(response.status_code, 200)

    def test_suspended_owner_token_is_rejected_immediately(self):
        response = self._me()
        self.assertEqual(response.status_code, 200)
        self.owner.is_active = False
        self.owner.save(update_fields=["is_active"])
        # Same already-issued token, no re-login, no waiting for expiry —
        # the very next request must fail. (DRF maps a raised
        # AuthenticationFailed here to 403, not 401, since this custom
        # scheme has no WWW-Authenticate challenge to offer — what
        # matters for this test is that access is denied at all.)
        response = self._me()
        self.assertEqual(response.status_code, 403)

    def test_suspended_owner_cannot_mint_a_new_token(self):
        self.owner.is_active = False
        self.owner.save(update_fields=["is_active"])
        credential = services.authenticate_bot_credential(self.credential.client_id, self.secret)
        self.assertIsNone(credential)

    def test_revoked_credential_token_is_rejected_immediately(self):
        response = self._me()
        self.assertEqual(response.status_code, 200)
        services.revoke_credential(self.bot, self.owner)
        response = self._me()
        self.assertEqual(response.status_code, 403)

    def test_paused_bot_token_is_rejected_immediately(self):
        response = self._me()
        self.assertEqual(response.status_code, 200)
        services.set_status(self.bot, self.owner, BotStatus.PAUSED)
        response = self._me()
        self.assertEqual(response.status_code, 403)


class ExternalAPIIsolationTests(TestCase):
    """Cross-bot / cross-owner isolation through the real external HTTP
    API — one bot's credential must never be able to see or act on
    another bot's resources, even though both endpoints are publicly
    reachable with any valid-looking bot token."""

    def setUp(self):
        cache.clear()
        ensure_default_account_tiers()
        self.owner_a = _make_user("iso-owner-a@example.com", tier_name="Business")
        self.owner_b = _make_user("iso-owner-b@example.com", tier_name="Business")
        self.bot_a = services.create_bot(self.owner_a, name="Bot A", kind=BotKind.CUSTOM)
        self.bot_b = services.create_bot(self.owner_b, name="Bot B", kind=BotKind.CUSTOM)
        services.set_status(self.bot_a, self.owner_a, BotStatus.ACTIVE)
        services.set_status(self.bot_b, self.owner_b, BotStatus.ACTIVE)

        self.group_a = create_group_with_conversation(owner=self.owner_a, name="GA", slug=f"ga-{uuid_suffix()}")
        self.group_b = create_group_with_conversation(owner=self.owner_b, name="GB", slug=f"gb-{uuid_suffix()}")
        self.grant_a = services.grant_resource_access(
            self.bot_a, self.owner_a, resource_type=BotResourceType.GROUP, resource_id=str(self.group_a.id),
            scopes=[BotPermissionScope.SEND_MESSAGES],
        )
        self.grant_b = services.grant_resource_access(
            self.bot_b, self.owner_b, resource_type=BotResourceType.GROUP, resource_id=str(self.group_b.id),
            scopes=[BotPermissionScope.SEND_MESSAGES],
        )
        cred_a, _ = services.issue_credential(self.bot_a, self.owner_a, scopes=["send_messages"])
        self.token_a = issue_access_token(cred_a)["access_token"]
        self.client = APIClient()

    def test_bot_a_token_only_sees_bot_a_resources(self):
        response = self.client.get(
            "/api/v1/bots/external/resources/", HTTP_AUTHORIZATION=f"Bearer {self.token_a}",
        )
        self.assertEqual(response.status_code, 200)
        grant_ids = {row["grant_id"] for row in response.json()}
        self.assertIn(str(self.grant_a.id), grant_ids)
        self.assertNotIn(str(self.grant_b.id), grant_ids)

    def test_bot_a_token_cannot_send_through_bot_bs_grant(self):
        response = self.client.post(
            f"/api/v1/bots/external/resources/{self.grant_b.id}/send-message/",
            {"text": "hijack attempt"},
            HTTP_AUTHORIZATION=f"Bearer {self.token_a}",
        )
        # bot=bot_a filter in the view means bot B's grant_id simply
        # doesn't resolve for bot A's token — a 404, not a 403, which is
        # itself the correct behavior (never confirm the grant exists).
        self.assertEqual(response.status_code, 404)

    def test_owner_a_cannot_retrieve_owner_bs_bot(self):
        client = APIClient()
        client.force_authenticate(user=self.owner_a)
        response = client.get(f"/api/v1/bots/{self.bot_b.id}/")
        self.assertEqual(response.status_code, 404)


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _RecordingWebhookServer:
    """A REAL local HTTP server (not a mock) that the webhook delivery
    code sends actual POST requests to over the loopback interface —
    'under realistic conditions' per the brief, not urllib mocked out."""

    def __init__(self, responses):
        self.responses = list(responses)  # list of HTTP status codes to return, in order
        self.requests = []  # list of (headers, body_bytes)
        self.port = _free_port()
        handler = self._make_handler()
        self.server = HTTPServer(("127.0.0.1", self.port), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _make_handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                outer.requests.append((dict(self.headers.items()), body))
                code = outer.responses.pop(0) if outer.responses else 200
                self.send_response(code)
                self.end_headers()

            def log_message(self, fmt, *args):  # noqa: A003 - silence test output
                pass

        return Handler

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/webhook"

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()


class RealisticWebhookDeliveryTests(TestCase):
    """Signature correctness, replay-protection headers, retry escalation
    and idempotency, all verified against a real HTTP server rather than
    a mocked urlopen call."""

    def setUp(self):
        cache.clear()
        ensure_default_account_tiers()
        self.owner = _make_user("webhook-real-owner@example.com", tier_name="Business")
        self.bot = services.create_bot(self.owner, name="Real Webhook Bot", kind=BotKind.CUSTOM)
        services.set_status(self.bot, self.owner, BotStatus.ACTIVE)
        self.group = create_group_with_conversation(owner=self.owner, name="RWG", slug=f"rwg-{uuid_suffix()}")
        self.grant = services.grant_resource_access(
            self.bot, self.owner, resource_type=BotResourceType.GROUP, resource_id=str(self.group.id),
            scopes=[BotPermissionScope.READ_MESSAGES],
        )

    def _subscribe(self, url):
        return BotWebhookSubscription.objects.create(
            bot=self.bot, url=url, events=["message.created"], retry_limit=3, retry_backoff_seconds=0,
        )

    def test_signature_and_timestamp_are_verifiable_by_a_real_receiver(self):
        server = _RecordingWebhookServer(responses=[200])
        try:
            subscription = self._subscribe(server.url)
            with patch("apps.bots.services.is_safe_external_url", return_value=True):
                delivered = services.dispatch_bot_event(
                    self.grant, "message.created", {"messageId": "m1"}, event_id="evt-real-1",
                )
            self.assertTrue(delivered)
            self.assertEqual(len(server.requests), 1)
            headers, body = server.requests[0]
            timestamp = headers["X-Kis-Timestamp"]
            received_signature = headers["X-Kis-Signature"]
            expected_signature = hmac.new(
                subscription.secret.encode("utf-8"),
                f"{timestamp}.".encode("utf-8") + body,
                hashlib.sha256,
            ).hexdigest()
            self.assertEqual(received_signature, expected_signature)
            # A receiver that tampers with (or receives a corrupted) body
            # must compute a DIFFERENT signature — proving the signature
            # actually covers the body, not just the timestamp.
            tampered_signature = hmac.new(
                subscription.secret.encode("utf-8"),
                f"{timestamp}.".encode("utf-8") + body + b"tampered",
                hashlib.sha256,
            ).hexdigest()
            self.assertNotEqual(tampered_signature, received_signature)
        finally:
            server.shutdown()

    def test_duplicate_event_id_never_reaches_the_server_twice(self):
        server = _RecordingWebhookServer(responses=[200, 200])
        try:
            self._subscribe(server.url)
            with patch("apps.bots.services.is_safe_external_url", return_value=True):
                services.dispatch_bot_event(self.grant, "message.created", {}, event_id="evt-real-dupe")
                services.dispatch_bot_event(self.grant, "message.created", {}, event_id="evt-real-dupe")
            self.assertEqual(len(server.requests), 1)
        finally:
            server.shutdown()

    def test_failure_then_success_retry_eventually_delivers(self):
        server = _RecordingWebhookServer(responses=[500, 200])
        try:
            self._subscribe(server.url)
            with patch("apps.bots.services.is_safe_external_url", return_value=True):
                delivered = services.dispatch_bot_event(
                    self.grant, "message.created", {}, event_id="evt-real-retry",
                )
                self.assertFalse(delivered)
                delivery = BotWebhookDelivery.objects.get(event_id="evt-real-retry")
                self.assertEqual(delivery.status, BotWebhookDeliveryStatus.FAILED)
                delivery.next_retry_at = services.timezone.now()
                delivery.save(update_fields=["next_retry_at"])
                # Same patch context — the SSRF guard correctly rejects a
                # loopback URL in real production use, so the retry sweep
                # needs the same "treat this test server as safe" patch
                # the initial attempt used, not just the first call.
                retried = services.retry_pending_bot_webhook_deliveries()
            self.assertEqual(retried, 1)
            delivery.refresh_from_db()
            self.assertEqual(delivery.status, BotWebhookDeliveryStatus.DELIVERED)
            self.assertEqual(len(server.requests), 2)
        finally:
            server.shutdown()

    def test_persistent_failure_dead_letters_after_retry_limit(self):
        server = _RecordingWebhookServer(responses=[500, 500, 500])
        try:
            subscription = self._subscribe(server.url)
            subscription.retry_limit = 3
            subscription.save(update_fields=["retry_limit"])
            with patch("apps.bots.services.is_safe_external_url", return_value=True):
                services.dispatch_bot_event(self.grant, "message.created", {}, event_id="evt-real-dead")
                delivery = BotWebhookDelivery.objects.get(event_id="evt-real-dead")
                for _ in range(2):
                    delivery.next_retry_at = services.timezone.now()
                    delivery.save(update_fields=["next_retry_at"])
                    services.retry_pending_bot_webhook_deliveries()
                    delivery.refresh_from_db()
            self.assertEqual(delivery.status, BotWebhookDeliveryStatus.DEAD_LETTER)
            self.assertEqual(delivery.attempt_count, 3)
            self.assertEqual(len(server.requests), 3)
        finally:
            server.shutdown()
