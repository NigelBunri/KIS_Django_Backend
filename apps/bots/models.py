# apps/bots/models.py
"""
KIS Bot Platform — Phase 1 data model.

Bots are a new identity/ownership layer, not a new messaging or
membership system: a Bot sends messages through the exact same
apps.chat.tasks.send_system_message_as_user -> Nest's
internal/messages/send-as-user pipeline any other system message already
uses (see apps.prayer.provisioning's welcome message for the existing
precedent), and resource access is granted against the existing
Group/Channel/Partner/Conversation rows (apps.groups, apps.channels,
apps.partners, apps.chat) rather than duplicating them.

Webhook delivery deliberately mirrors apps.partners.models.PartnerWebhook/
PartnerWebhookDelivery's exact shape (events[] JSON list, HMAC-SHA256
secret, retry_limit/retry_backoff_seconds, delivery status/attempt audit
trail) - proven, already-production delivery semantics - but is NOT the
same table: a Bot is not a Partner (a Business-tier user with no Partner
record can still own bots), and extending PartnerWebhook's dispatch path
to double as bot delivery would risk the one thing explicitly ruled out
for this work - breaking an existing, live system for an unrelated
reason entirely.
"""
from __future__ import annotations

import secrets
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.accounts.models import User


def _generate_client_id() -> str:
    return f"bot_{secrets.token_hex(12)}"


def _generate_client_secret() -> str:
    return secrets.token_urlsafe(32)


def _generate_webhook_secret() -> str:
    return secrets.token_urlsafe(24)


class BotStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    SUSPENDED = "suspended", "Suspended"
    DISCONNECTED = "disconnected", "Disconnected"
    DELETED = "deleted", "Deleted"


class BotKind(models.TextChoices):
    # What actually drives the bot's behavior - informational/UX only,
    # never itself a security boundary (permissions are enforced via
    # BotResourceGrant regardless of kind).
    RULE_BASED = "rule_based", "Rule-based"
    CUSTOM = "custom", "Custom-developed"
    AI_POWERED = "ai_powered", "AI-powered"
    HYBRID = "hybrid", "Hybrid"


class Bot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="owned_bots")
    # Optional - set only when this bot is scoped under a Partner the
    # owner actually owns/administers (validated in services.py, never
    # trusted from client input alone). A Business-tier owner with no
    # Partner record leaves this null; the bot is still fully theirs.
    partner = models.ForeignKey(
        "partners.Partner",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bots",
    )
    name = models.CharField(max_length=120)
    # Bot handles live in their own namespace (not apps.accounts.User.username)
    # so a bot can never collide with, or be confused for, a human account.
    handle = models.SlugField(max_length=64, unique=True)
    avatar_url = models.URLField(max_length=2048, blank=True, default="")
    description = models.TextField(blank=True, default="")
    kind = models.CharField(max_length=20, choices=BotKind.choices, default=BotKind.RULE_BASED)
    status = models.CharField(max_length=20, choices=BotStatus.choices, default=BotStatus.DRAFT, db_index=True)
    # Rule-based trigger/response configuration (section 5A) and any other
    # owner-editable behavior config. Validated shape, not executed as
    # code - see services.py's workflow evaluator.
    config = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "bots_bot"
        indexes = [
            models.Index(fields=["owner", "status"]),
            models.Index(fields=["partner", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.handle})"

    @property
    def is_deleted(self) -> bool:
        return self.status == BotStatus.DELETED or self.deleted_at is not None


class BotAIProvider(models.TextChoices):
    OPENAI = "openai", "OpenAI"
    ANTHROPIC = "anthropic", "Anthropic"
    GEMINI = "gemini", "Google Gemini"
    CUSTOM = "custom", "Custom endpoint"


class BotAIConnection(models.Model):
    """One optional, owner-supplied AI provider connection per bot.
    Never exposed to the mobile app or any public API after creation -
    api_key is write-only at the serializer layer, same posture as
    BotCredential.client_secret below."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    bot = models.OneToOneField(Bot, on_delete=models.CASCADE, related_name="ai_connection")
    provider = models.CharField(max_length=20, choices=BotAIProvider.choices)
    # Owner's own key for OPENAI/ANTHROPIC/GEMINI, or the full endpoint
    # for CUSTOM - encrypted at rest via a dedicated Fernet envelope
    # (settings.BOTS_AI_CREDENTIAL_KEK), mirroring apps.books.encryption's
    # proven KEK pattern exactly (env-configured in production, a
    # SECRET_KEY-derived fallback only in DEBUG) rather than sharing that
    # key across two unrelated secret domains. See services.py's
    # encrypt_ai_api_key/decrypt_ai_api_key.
    api_key_encrypted = models.TextField(blank=True, default="")
    custom_endpoint_url = models.URLField(max_length=1024, blank=True, default="")
    model = models.CharField(max_length=120, blank=True, default="")
    system_instructions = models.TextField(blank=True, default="")
    daily_request_limit = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "bots_ai_connection"


class BotCredential(models.Model):
    """OAuth2 client-credentials pair for an externally-hosted bot
    (section 7). client_secret is stored hashed (Django's own password
    hasher, same as User passwords) and shown to the owner only once, at
    creation/rotation time - never again, mirroring BotCredential's own
    "secret display only when initially generated" requirement."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    bot = models.OneToOneField(Bot, on_delete=models.CASCADE, related_name="credential")
    client_id = models.CharField(max_length=64, unique=True, default=_generate_client_id)
    client_secret_hash = models.CharField(max_length=255)
    # Explicit, auditable scopes (section 12) - never wider than what the
    # owner's own resource grants (BotResourceGrant) separately allow at
    # runtime; this is the credential's ceiling, resource grants are the
    # per-resource floor actually checked on every call.
    scopes = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    rotated_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_used_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        db_table = "bots_credential"


class BotResourceType(models.TextChoices):
    DIRECT_CONVERSATION = "direct_conversation", "Private conversation"
    GROUP = "group", "Group"
    CHANNEL = "channel", "Channel"
    COMMUNITY = "community", "Community"
    PARTNER = "partner", "Partner"
    FEED_PROFILE = "feed_profile", "Feed profile"


class BotPermissionScope(models.TextChoices):
    # Deliberately coarse-grained for Phase 1 (section 12) - fine enough
    # to be meaningful, not so granular the owner-facing UI becomes
    # unmanageable. Extend this list, not the enforcement call sites,
    # when a new scope is needed.
    READ_MESSAGES = "read_messages", "Read messages"
    SEND_MESSAGES = "send_messages", "Send messages"
    READ_MEMBERS = "read_members", "Read member/participant info"
    MODERATE = "moderate", "Moderation actions"
    PUBLISH_CONTENT = "publish_content", "Publish feed/channel content"
    MANAGE_RESOURCE = "manage_resource", "Manage resource settings"


class BotResourceGrant(models.Model):
    """The resource-adapter capability registry (section 8G): one row per
    (bot, resource) connection, with its own explicit, auditable
    permission scopes - connecting a bot at Partner level never implies
    access to every resource under that Partner (section 8F's explicit
    requirement). Extensible to a new BotResourceType without a schema
    change; resource_id is validated against the real owning model for
    its resource_type in services.py (grant_resource_access), never
    trusted bare."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    bot = models.ForeignKey(Bot, on_delete=models.CASCADE, related_name="resource_grants")
    resource_type = models.CharField(max_length=32, choices=BotResourceType.choices)
    resource_id = models.CharField(max_length=64)
    # The underlying Conversation this resource maps to, denormalized at
    # grant time so the Nest message-event webhook dispatcher (see
    # apps.chat.views.webhook_dispatch) can match an incoming
    # conversation_id straight to its bot grants with one indexed lookup,
    # instead of re-deriving Group/Channel/Partner -> Conversation on
    # every single message sent anywhere in KIS.
    conversation_id = models.UUIDField(null=True, blank=True, db_index=True)
    scopes = models.JSONField(default=list, blank=True)
    granted_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name="bot_grants_issued",
    )
    is_active = models.BooleanField(default=True)
    # Non-trivial actions (section 12's approval-workflow requirement)
    # that this grant has pre-approved to run without a human in the
    # loop. Anything not listed here always queues a BotApprovalRequest
    # instead of executing.
    auto_approved_actions = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "bots_resource_grant"
        indexes = [
            models.Index(fields=["resource_type", "resource_id"]),
            models.Index(fields=["conversation_id", "is_active"]),
            models.Index(fields=["bot", "is_active"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["bot", "resource_type", "resource_id"],
                name="uniq_bot_resource_grant",
            ),
        ]


class BotWebhookSubscription(models.Model):
    """Mirrors PartnerWebhook's proven shape (see module docstring) -
    intentionally not a shared/parent table with it, so nothing here can
    ever affect existing Partner webhook delivery."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    bot = models.OneToOneField(Bot, on_delete=models.CASCADE, related_name="webhook_subscription")
    url = models.URLField(max_length=500)
    events = models.JSONField(default=list, blank=True)
    secret = models.CharField(max_length=128, default=_generate_webhook_secret)
    is_active = models.BooleanField(default=True)
    retry_limit = models.PositiveIntegerField(default=5)
    retry_backoff_seconds = models.PositiveIntegerField(default=30)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "bots_webhook_subscription"


class BotWebhookDeliveryStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    DELIVERED = "delivered", "Delivered"
    FAILED = "failed", "Failed"
    DEAD_LETTER = "dead_letter", "Dead letter"


class BotWebhookDelivery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subscription = models.ForeignKey(
        BotWebhookSubscription, on_delete=models.CASCADE, related_name="deliveries",
    )
    event = models.CharField(max_length=64)
    # Idempotency key (section 9) - the same (event, event_id) must never
    # be delivered twice even if the dispatcher is invoked more than once
    # for the same underlying occurrence (e.g. a retried Nest call).
    event_id = models.CharField(max_length=128, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=16, choices=BotWebhookDeliveryStatus.choices, default=BotWebhookDeliveryStatus.PENDING,
    )
    attempt_count = models.PositiveIntegerField(default=0)
    response_code = models.PositiveIntegerField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")
    next_retry_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "bots_webhook_delivery"
        indexes = [
            models.Index(fields=["subscription", "status"]),
            models.Index(fields=["status", "next_retry_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["subscription", "event_id"],
                name="uniq_bot_webhook_delivery_idempotency",
            ),
        ]


class BotActivityAction(models.TextChoices):
    CREATED = "created", "Bot created"
    UPDATED = "updated", "Bot updated"
    STATUS_CHANGED = "status_changed", "Status changed"
    CREDENTIAL_ROTATED = "credential_rotated", "Credential rotated"
    CREDENTIAL_REVOKED = "credential_revoked", "Credential revoked"
    RESOURCE_GRANTED = "resource_granted", "Resource access granted"
    RESOURCE_REVOKED = "resource_revoked", "Resource access revoked"
    MESSAGE_SENT = "message_sent", "Message sent"
    EVENT_DELIVERED = "event_delivered", "Event delivered"
    EVENT_FAILED = "event_failed", "Event delivery failed"
    APPROVAL_REQUESTED = "approval_requested", "Approval requested"
    APPROVAL_DECIDED = "approval_decided", "Approval decided"
    SUSPENDED_BY_ADMIN = "suspended_by_admin", "Suspended by KIS admin"


class BotActivityLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    bot = models.ForeignKey(Bot, on_delete=models.CASCADE, related_name="activity_log")
    action = models.CharField(max_length=32, choices=BotActivityAction.choices)
    resource_type = models.CharField(max_length=32, blank=True, default="")
    resource_id = models.CharField(max_length=64, blank=True, default="")
    actor = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="bot_actions_performed",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "bots_activity_log"
        indexes = [
            models.Index(fields=["bot", "-created_at"]),
        ]


class BotApprovalStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    DECLINED = "declined", "Declined"
    EXPIRED = "expired", "Expired"


class BotApprovalRequest(models.Model):
    """Human-in-the-loop gate for sensitive bot actions (section 12) -
    banning members, removing admins, deleting content, anything billing/
    ownership-related. A bot's requested action is recorded here instead
    of executed; it only actually runs once an authorized human on the
    resource approves it (services.py's decide_approval)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    bot = models.ForeignKey(Bot, on_delete=models.CASCADE, related_name="approval_requests")
    resource_grant = models.ForeignKey(
        BotResourceGrant, on_delete=models.CASCADE, related_name="approval_requests",
    )
    action = models.CharField(max_length=64)
    details = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=16, choices=BotApprovalStatus.choices, default=BotApprovalStatus.PENDING,
    )
    decided_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="bot_approvals_decided",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "bots_approval_request"
        indexes = [
            models.Index(fields=["bot", "status"]),
        ]
