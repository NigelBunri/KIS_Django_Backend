# apps/bots/services.py
"""
KIS Bot Platform business logic — eligibility, bot lifecycle, credential
issuance, resource-grant validation, webhook dispatch, and activity
logging. Deliberately the ONLY place these decisions are made; views.py
stays thin.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import urllib.request
import uuid
from datetime import timedelta
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.text import slugify
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.feature_gate import has_feature
from apps.accounts.models import User
from apps.accounts.tiers import get_user_tier_features, normalize_limit_value
from common.url_safety import is_safe_external_url

from .models import (
    Bot,
    BotActivityAction,
    BotActivityLog,
    BotAIConnection,
    BotApprovalRequest,
    BotApprovalStatus,
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

SENSITIVE_ACTIONS = {
    "ban_member",
    "remove_admin",
    "change_ownership",
    "delete_content",
    "financial_action",
    "change_billing",
}


# ---------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------

class BotEligibilityError(PermissionDenied):
    pass


def ensure_bot_platform_eligible(user: User) -> dict:
    """Raises BotEligibilityError unless `user`'s current AccountTier
    (apps.accounts.tiers — the single real entitlement source; never
    re-derived from a mobile-supplied flag) includes bots_enabled. Returns
    the aggregated feature dict so callers can read limits off the same
    lookup without querying twice."""
    features = get_user_tier_features(user)
    if not features.get("bots_enabled"):
        raise BotEligibilityError(
            "Your current plan does not include the Bot Platform. Upgrade to a Business "
            "plan or higher to create bots."
        )
    return features


def get_bot_limit(user) -> int | None:
    features = get_user_tier_features(user)
    return normalize_limit_value(features.get("bots_limit"), default=0)


def ensure_can_create_bot(user: User) -> None:
    features = ensure_bot_platform_eligible(user)
    limit = normalize_limit_value(features.get("bots_limit"), default=0)
    if limit is not None:
        existing = Bot.objects.filter(owner=user).exclude(status=BotStatus.DELETED).count()
        if existing >= limit:
            raise BotEligibilityError(
                f"Your plan allows up to {limit} bot(s). Delete an existing bot or upgrade your plan."
            )


def get_bot_daily_event_limit(user) -> int | None:
    features = get_user_tier_features(user)
    return normalize_limit_value(features.get("bot_events_per_day_limit"), default=100)


# ---------------------------------------------------------------------
# Bot lifecycle
# ---------------------------------------------------------------------

def _slugify_handle(name: str) -> str:
    base = slugify(name)[:48] or "bot"
    candidate = f"{base}-{uuid.uuid4().hex[:6]}"
    return candidate


def log_activity(
    bot: Bot,
    action: str,
    *,
    actor: User | None = None,
    resource_type: str = "",
    resource_id: str = "",
    metadata: dict | None = None,
) -> None:
    BotActivityLog.objects.create(
        bot=bot,
        action=action,
        actor=actor,
        resource_type=resource_type,
        resource_id=str(resource_id or ""),
        metadata=metadata or {},
    )


def create_bot(
    owner: User,
    *,
    name: str,
    description: str = "",
    avatar_url: str = "",
    kind: str = BotKind.RULE_BASED,
    partner=None,
) -> Bot:
    ensure_can_create_bot(owner)
    name = (name or "").strip()
    if not name:
        raise ValidationError({"name": "A bot name is required."})
    if partner is not None and partner.owner_id != owner.id:
        # Owner-only for Phase 1 - a partner ADMIN connecting their own
        # bot at partner level is deliberately deferred (section 8F's
        # delegation model) rather than approximated incorrectly here.
        raise PermissionDenied("Only the Partner owner can create a bot scoped to this Partner.")

    handle = _slugify_handle(name)
    for _ in range(5):
        try:
            with transaction.atomic():
                bot = Bot.objects.create(
                    owner=owner,
                    partner=partner,
                    name=name[:120],
                    handle=handle,
                    description=description[:4000],
                    avatar_url=avatar_url[:2048],
                    kind=kind,
                    status=BotStatus.DRAFT,
                )
                log_activity(bot, BotActivityAction.CREATED, actor=owner)
                return bot
        except IntegrityError:
            handle = _slugify_handle(name)
    raise ValidationError({"name": "Could not allocate a unique bot handle. Try a different name."})


def update_bot(bot: Bot, user: User, **fields) -> Bot:
    _require_owner(bot, user)
    allowed = {"name", "description", "avatar_url", "config", "kind"}
    changed = []
    for key, value in fields.items():
        if key not in allowed or value is None:
            continue
        setattr(bot, key, value)
        changed.append(key)
    if changed:
        bot.save(update_fields=[*changed, "updated_at"])
        log_activity(bot, BotActivityAction.UPDATED, actor=user, metadata={"fields": changed})
    return bot


def _require_owner(bot: Bot, user: User) -> None:
    if bot.owner_id != user.id:
        raise PermissionDenied("You do not own this bot.")


VALID_TRANSITIONS = {
    BotStatus.DRAFT: {BotStatus.ACTIVE, BotStatus.DELETED},
    BotStatus.ACTIVE: {BotStatus.PAUSED, BotStatus.DISCONNECTED, BotStatus.SUSPENDED, BotStatus.DELETED},
    BotStatus.PAUSED: {BotStatus.ACTIVE, BotStatus.DISCONNECTED, BotStatus.SUSPENDED, BotStatus.DELETED},
    BotStatus.DISCONNECTED: {BotStatus.ACTIVE, BotStatus.DELETED},
    BotStatus.SUSPENDED: {BotStatus.DELETED},  # only KIS admin can lift a suspension (set_status_admin)
    BotStatus.DELETED: set(),
}


def set_status(bot: Bot, user: User, new_status: str) -> Bot:
    _require_owner(bot, user)
    if new_status not in VALID_TRANSITIONS.get(bot.status, set()):
        raise ValidationError({"status": f"Cannot move a bot from {bot.status} to {new_status}."})
    if new_status == BotStatus.ACTIVE and bot.status == BotStatus.DRAFT:
        ensure_bot_platform_eligible(user)
    bot.status = new_status
    if new_status == BotStatus.DELETED:
        bot.deleted_at = timezone.now()
        bot.save(update_fields=["status", "deleted_at", "updated_at"])
        # Owner-initiated delete immediately revokes credentials and
        # resource access too — "View owner must be able to immediately
        # disconnect a bot and revoke its credentials" (section 15).
        BotCredential.objects.filter(bot=bot, is_active=True).update(
            is_active=False, revoked_at=timezone.now(),
        )
        BotResourceGrant.objects.filter(bot=bot, is_active=True).update(
            is_active=False, revoked_at=timezone.now(),
        )
    else:
        bot.save(update_fields=["status", "updated_at"])
    log_activity(bot, BotActivityAction.STATUS_CHANGED, actor=user, metadata={"status": new_status})
    return bot


def set_status_admin(bot: Bot, admin_user: User, new_status: str, *, reason: str = "") -> Bot:
    """KIS superadmin path (section 17) — bypasses VALID_TRANSITIONS'
    owner-only SUSPENDED exit, since lifting/imposing a suspension is
    exactly the one transition an owner must never self-service."""
    bot.status = new_status
    bot.save(update_fields=["status", "updated_at"])
    log_activity(
        bot, BotActivityAction.SUSPENDED_BY_ADMIN if new_status == BotStatus.SUSPENDED else BotActivityAction.STATUS_CHANGED,
        actor=admin_user, metadata={"status": new_status, "reason": reason},
    )
    return bot


# ---------------------------------------------------------------------
# Credentials (OAuth2 client-credentials pair)
# ---------------------------------------------------------------------

def issue_credential(bot: Bot, user: User, *, scopes: list[str] | None = None) -> tuple[BotCredential, str]:
    """Returns (credential_row, plaintext_client_secret). The plaintext
    secret is NEVER persisted or retrievable again after this call
    returns — only its hash is stored (same hasher as User passwords)."""
    _require_owner(bot, user)
    from .models import _generate_client_secret

    plaintext_secret = _generate_client_secret()
    valid_scopes = [s for s in (scopes or []) if s in BotPermissionScope.values]
    already_existed = BotCredential.objects.filter(bot=bot).exists()
    credential, _created = BotCredential.objects.update_or_create(
        bot=bot,
        defaults={
            "client_secret_hash": make_password(plaintext_secret),
            "scopes": valid_scopes,
            "is_active": True,
            "revoked_at": None,
            "rotated_at": timezone.now() if already_existed else None,
        },
    )
    log_activity(bot, BotActivityAction.CREDENTIAL_ROTATED, actor=user)
    return credential, plaintext_secret


def rotate_credential(bot: Bot, user: User) -> tuple[BotCredential, str]:
    return issue_credential(bot, user, scopes=getattr(bot.credential, "scopes", None) if hasattr(bot, "credential") else None)


def revoke_credential(bot: Bot, user: User) -> None:
    _require_owner(bot, user)
    updated = BotCredential.objects.filter(bot=bot, is_active=True).update(
        is_active=False, revoked_at=timezone.now(),
    )
    if updated:
        log_activity(bot, BotActivityAction.CREDENTIAL_REVOKED, actor=user)


def authenticate_bot_credential(client_id: str, client_secret: str) -> BotCredential | None:
    try:
        credential = BotCredential.objects.select_related("bot").get(client_id=client_id, is_active=True)
    except BotCredential.DoesNotExist:
        return None
    if credential.bot.status not in (BotStatus.ACTIVE,):
        return None
    if not check_password(client_secret, credential.client_secret_hash):
        return None
    return credential


# ---------------------------------------------------------------------
# Resource grants — the extensible resource-adapter registry (section 8G)
# ---------------------------------------------------------------------

def _resolve_conversation_id_for_resource(resource_type: str, resource_id: str) -> uuid.UUID | None:
    if resource_type == BotResourceType.DIRECT_CONVERSATION:
        return uuid.UUID(str(resource_id))
    if resource_type == BotResourceType.GROUP:
        from apps.groups.models import Group

        group = Group.objects.filter(id=resource_id).select_related("conversation").first()
        return group.conversation_id if group else None
    if resource_type == BotResourceType.CHANNEL:
        from apps.broadcasts.models import BroadcastChannel

        channel = BroadcastChannel.objects.filter(id=resource_id).first()
        return getattr(channel, "conversation_id", None) if channel else None
    if resource_type == BotResourceType.COMMUNITY:
        from apps.communities.models import Community

        community = Community.objects.filter(id=resource_id).first()
        return getattr(community, "main_conversation_id", None) if community else None
    if resource_type == BotResourceType.PARTNER:
        from apps.partners.models import Partner

        partner = Partner.objects.filter(id=resource_id).first()
        return getattr(partner, "main_conversation_id", None) if partner else None
    return None


def _user_may_grant_resource(user: User, resource_type: str, resource_id: str) -> bool:
    """A bot must never receive more authority than its owner possesses
    (section 12) - this is the one gate that enforces it for every
    resource type, checked BEFORE a grant is ever created."""
    if resource_type == BotResourceType.DIRECT_CONVERSATION:
        from apps.chat.models import ConversationMember

        return ConversationMember.objects.filter(
            conversation_id=resource_id, user=user, left_at__isnull=True,
        ).exists()

    if resource_type == BotResourceType.GROUP:
        from apps.groups.models import Group, GroupMembership, GroupRole

        group = Group.objects.filter(id=resource_id).first()
        if not group:
            return False
        if group.owner_id == user.id:
            return True
        return GroupMembership.objects.filter(
            group=group, user=user, left_at__isnull=True,
            role__in=(GroupRole.OWNER, GroupRole.ADMIN),
        ).exists()

    if resource_type == BotResourceType.CHANNEL:
        from apps.broadcasts.models import BroadcastChannel
        from apps.broadcasts.views import _user_can_manage_channel

        channel = BroadcastChannel.objects.filter(id=resource_id).first()
        return bool(channel) and _user_can_manage_channel(user, channel)

    if resource_type == BotResourceType.COMMUNITY:
        from apps.communities.models import Community, CommunityMembership, CommunityRole

        community = Community.objects.filter(id=resource_id).first()
        if not community:
            return False
        if community.owner_id == user.id:
            return True
        return CommunityMembership.objects.filter(
            community=community, user=user, left_at__isnull=True,
            role__in=(CommunityRole.OWNER, CommunityRole.ADMIN),
        ).exists()

    if resource_type == BotResourceType.PARTNER:
        from apps.partners.models import Partner
        from apps.partners.services import get_partner_user_roles

        partner = Partner.objects.filter(id=resource_id).first()
        if not partner:
            return False
        if partner.owner_id == user.id:
            return True
        return bool(get_partner_user_roles(partner, user) & {"owner", "admin"})

    if resource_type == BotResourceType.FEED_PROFILE:
        # A feed profile is always the owner's own profile-level feed
        # (apps.broadcasts.models.BroadcastFeedProfile) - ownership is
        # identity itself, no separate membership check applies.
        return str(resource_id) == str(user.id)

    return False


def grant_resource_access(
    bot: Bot,
    user: User,
    *,
    resource_type: str,
    resource_id: str,
    scopes: list[str],
    auto_approved_actions: list[str] | None = None,
) -> BotResourceGrant:
    _require_owner(bot, user)
    if resource_type not in BotResourceType.values:
        raise ValidationError({"resource_type": "Unsupported resource type."})
    if not _user_may_grant_resource(user, resource_type, resource_id):
        raise PermissionDenied(
            "You must be an owner/admin of this resource to connect a bot to it."
        )
    valid_scopes = [s for s in (scopes or []) if s in BotPermissionScope.values]
    if not valid_scopes:
        raise ValidationError({"scopes": "At least one valid permission scope is required."})
    auto_approved = [a for a in (auto_approved_actions or []) if a not in SENSITIVE_ACTIONS]

    conversation_id = _resolve_conversation_id_for_resource(resource_type, resource_id)
    grant, _created = BotResourceGrant.objects.update_or_create(
        bot=bot,
        resource_type=resource_type,
        resource_id=str(resource_id),
        defaults={
            "conversation_id": conversation_id,
            "scopes": valid_scopes,
            "granted_by": user,
            "is_active": True,
            "auto_approved_actions": auto_approved,
            "revoked_at": None,
        },
    )
    log_activity(
        bot, BotActivityAction.RESOURCE_GRANTED, actor=user,
        resource_type=resource_type, resource_id=resource_id,
        metadata={"scopes": valid_scopes},
    )
    return grant


def revoke_resource_access(bot: Bot, user: User, grant_id) -> None:
    _require_owner(bot, user)
    grant = BotResourceGrant.objects.filter(bot=bot, id=grant_id, is_active=True).first()
    if not grant:
        return
    grant.is_active = False
    grant.revoked_at = timezone.now()
    grant.save(update_fields=["is_active", "revoked_at", "updated_at"])
    log_activity(
        bot, BotActivityAction.RESOURCE_REVOKED, actor=user,
        resource_type=grant.resource_type, resource_id=grant.resource_id,
    )


def grant_has_scope(grant: BotResourceGrant, scope: str) -> bool:
    return scope in (grant.scopes or [])


def requires_approval(grant: BotResourceGrant, action: str) -> bool:
    if action in SENSITIVE_ACTIONS:
        return action not in (grant.auto_approved_actions or [])
    return False


def request_approval(bot: Bot, grant: BotResourceGrant, action: str, details: dict) -> BotApprovalRequest:
    request_row = BotApprovalRequest.objects.create(
        bot=bot, resource_grant=grant, action=action, details=details or {},
        expires_at=timezone.now() + timedelta(hours=48),
    )
    log_activity(
        bot, BotActivityAction.APPROVAL_REQUESTED, resource_type=grant.resource_type,
        resource_id=grant.resource_id, metadata={"action": action, "approval_request_id": str(request_row.id)},
    )
    return request_row


def decide_approval(approval: BotApprovalRequest, decider: User, approve: bool) -> BotApprovalRequest:
    if approval.status != BotApprovalStatus.PENDING:
        raise ValidationError({"status": "This approval request has already been decided."})
    if not _user_may_grant_resource(decider, approval.resource_grant.resource_type, approval.resource_grant.resource_id):
        raise PermissionDenied("You are not authorized to decide approvals for this resource.")
    approval.status = BotApprovalStatus.APPROVED if approve else BotApprovalStatus.DECLINED
    approval.decided_by = decider
    approval.decided_at = timezone.now()
    approval.save(update_fields=["status", "decided_by", "decided_at"])
    log_activity(
        approval.bot, BotActivityAction.APPROVAL_DECIDED, actor=decider,
        resource_type=approval.resource_grant.resource_type, resource_id=approval.resource_grant.resource_id,
        metadata={"action": approval.action, "approved": approve},
    )
    return approval


# ---------------------------------------------------------------------
# Sending a message as a bot — reuses the EXACT existing system-message
# pipeline (apps.chat.tasks.send_system_message_as_user -> Nest's
# internal/messages/send-as-user), not a new send path.
# ---------------------------------------------------------------------

def send_message_as_bot(bot: Bot, grant: BotResourceGrant, text: str, *, client_id: str | None = None) -> dict:
    if bot.status != BotStatus.ACTIVE:
        raise PermissionDenied("This bot is not active.")
    if not grant.is_active or grant.bot_id != bot.id:
        raise PermissionDenied("This bot has no active grant for that resource.")
    if not grant_has_scope(grant, BotPermissionScope.SEND_MESSAGES):
        raise PermissionDenied("This bot does not have send_messages permission on this resource.")
    if not grant.conversation_id:
        raise ValidationError({"resource": "This resource has no linked conversation to send into."})

    from apps.chat.tasks import send_system_message_as_user

    result = send_system_message_as_user(
        conversation_id=str(grant.conversation_id),
        sender_id=str(bot.id),
        text=text,
        client_id=client_id or f"bot-msg:{bot.id}:{uuid.uuid4().hex}",
    )
    log_activity(
        bot, BotActivityAction.MESSAGE_SENT, resource_type=grant.resource_type,
        resource_id=grant.resource_id, metadata={"preview": text[:200]},
    )
    return result


# ---------------------------------------------------------------------
# Webhook delivery — mirrors apps.partners.services.dispatch_partner_
# webhooks' proven semantics (HMAC-SHA256 signing, SSRF guard, retry
# backoff, delivery audit trail) against BotWebhookSubscription/
# BotWebhookDelivery instead, so nothing here can affect existing
# Partner webhook delivery.
# ---------------------------------------------------------------------

def dispatch_bot_event(grant: BotResourceGrant, event: str, payload: dict, *, event_id: str) -> bool:
    bot = grant.bot
    if bot.status != BotStatus.ACTIVE or not grant.is_active:
        return False
    subscription = getattr(bot, "webhook_subscription", None)
    if not subscription or not subscription.is_active:
        return False
    events = subscription.events or []
    if events and event not in events and "*" not in events:
        return False

    delivery, created = BotWebhookDelivery.objects.get_or_create(
        subscription=subscription,
        event_id=event_id,
        defaults={"event": event, "payload": payload, "status": BotWebhookDeliveryStatus.PENDING},
    )
    if not created and delivery.status == BotWebhookDeliveryStatus.DELIVERED:
        # Idempotency (section 9) - the same occurrence must never be
        # delivered twice even if this is invoked again for it.
        return True

    return _attempt_delivery(subscription, delivery)


def _attempt_delivery(subscription: BotWebhookSubscription, delivery: BotWebhookDelivery) -> bool:
    if not is_safe_external_url(subscription.url):
        delivery.status = BotWebhookDeliveryStatus.FAILED
        delivery.error_message = "Webhook URL is not an allowed external address."
        delivery.save(update_fields=["status", "error_message", "updated_at"])
        return False

    body = {
        "event": delivery.event,
        "event_id": delivery.event_id,
        "bot_id": str(subscription.bot_id),
        "payload": delivery.payload or {},
        "delivered_at": timezone.now().isoformat(),
    }
    body_bytes = json.dumps(body).encode("utf-8")
    signature = hmac.new(subscription.secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-KIS-Bot-Event": delivery.event,
        "X-KIS-Bot-Id": str(subscription.bot_id),
        "X-KIS-Signature": signature,
    }
    request = urllib.request.Request(subscription.url, data=body_bytes, headers=headers, method="POST")

    delivery.attempt_count += 1
    try:
        with urllib.request.urlopen(request, timeout=6) as resp:
            delivery.response_code = resp.status
            if 200 <= resp.status < 300:
                delivery.status = BotWebhookDeliveryStatus.DELIVERED
                subscription.last_sent_at = timezone.now()
                subscription.last_error = ""
                ok = True
            else:
                delivery.status = BotWebhookDeliveryStatus.FAILED
                delivery.error_message = f"HTTP {resp.status}"
                subscription.last_error = delivery.error_message
                ok = False
    except Exception as exc:  # noqa: BLE001 - any delivery failure is just a FAILED delivery
        delivery.status = BotWebhookDeliveryStatus.FAILED
        delivery.error_message = str(exc)[:500]
        subscription.last_error = delivery.error_message
        ok = False

    if delivery.status == BotWebhookDeliveryStatus.FAILED:
        if delivery.attempt_count >= subscription.retry_limit:
            delivery.status = BotWebhookDeliveryStatus.DEAD_LETTER
            delivery.next_retry_at = None
            log_activity(
                subscription.bot, BotActivityAction.EVENT_FAILED,
                metadata={"event": delivery.event, "event_id": delivery.event_id, "dead_letter": True},
            )
        else:
            delivery.next_retry_at = timezone.now() + timedelta(
                seconds=subscription.retry_backoff_seconds * (2 ** (delivery.attempt_count - 1)),
            )
    else:
        log_activity(
            subscription.bot, BotActivityAction.EVENT_DELIVERED,
            metadata={"event": delivery.event, "event_id": delivery.event_id},
        )

    subscription.save(update_fields=["last_sent_at", "last_error", "updated_at"])
    delivery.save(
        update_fields=["status", "attempt_count", "response_code", "error_message", "next_retry_at", "updated_at"],
    )
    return ok


def dispatch_bot_events_for_conversation(conversation_id, event: str, payload: dict, *, event_id: str) -> int:
    """Entry point from apps.chat.views.webhook_dispatch (the EXISTING
    Nest -> Django message.created call) - fans out to every active bot
    resource grant on this conversation, in addition to whatever Partner
    webhook dispatch already happens there. Never raises - a bot
    delivery failure must never affect the caller (which is itself on
    the critical message-send path)."""
    delivered = 0
    grants = BotResourceGrant.objects.filter(
        conversation_id=conversation_id, is_active=True,
    ).select_related("bot", "bot__webhook_subscription")
    for grant in grants:
        try:
            if grant_has_scope(grant, BotPermissionScope.READ_MESSAGES):
                if dispatch_bot_event(grant, event, payload, event_id=f"{event_id}:{grant.id}"):
                    delivered += 1
        except Exception:  # noqa: BLE001
            continue
    return delivered


def retry_pending_bot_webhook_deliveries(limit: int = 200) -> int:
    """Celery-beat entry point (see tasks.py) - mirrors apps.partners'
    own retry sweep pattern."""
    due = BotWebhookDelivery.objects.filter(
        status=BotWebhookDeliveryStatus.FAILED, next_retry_at__lte=timezone.now(),
    ).select_related("subscription", "subscription__bot")[:limit]
    retried = 0
    for delivery in due:
        if _attempt_delivery(delivery.subscription, delivery):
            retried += 1
    return retried


# ---------------------------------------------------------------------
# AI provider credential encryption (section 10) — dedicated KEK, see
# settings.BOTS_AI_CREDENTIAL_KEK's docstring.
# ---------------------------------------------------------------------

def _ai_kek_fernet() -> Fernet:
    key = settings.BOTS_AI_CREDENTIAL_KEK
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_ai_api_key(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _ai_kek_fernet().encrypt(plaintext.encode()).decode()


def decrypt_ai_api_key(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return _ai_kek_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        return ""


def connect_ai_provider(
    bot: Bot, user: User, *, provider: str, api_key: str = "", custom_endpoint_url: str = "",
    model: str = "", system_instructions: str = "", daily_request_limit: int | None = None,
) -> BotAIConnection:
    _require_owner(bot, user)
    connection, _created = BotAIConnection.objects.update_or_create(
        bot=bot,
        defaults={
            "provider": provider,
            "api_key_encrypted": encrypt_ai_api_key(api_key) if api_key else "",
            "custom_endpoint_url": custom_endpoint_url[:1024],
            "model": model[:120],
            "system_instructions": system_instructions[:8000],
            "daily_request_limit": daily_request_limit,
            "is_active": True,
        },
    )
    log_activity(bot, BotActivityAction.UPDATED, actor=user, metadata={"ai_provider": provider})
    return connection


def disconnect_ai_provider(bot: Bot, user: User) -> None:
    _require_owner(bot, user)
    BotAIConnection.objects.filter(bot=bot).delete()
    log_activity(bot, BotActivityAction.UPDATED, actor=user, metadata={"ai_provider": None})
