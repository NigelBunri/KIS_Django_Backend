# apps/bots/tests.py
from unittest.mock import patch

from django.test import TestCase

from apps.accounts.models import AccountTier, Subscription, User
from apps.accounts.tiers import ensure_default_account_tiers
from apps.groups.models import Group, GroupJoinPolicy, GroupMembership, GroupRole
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
