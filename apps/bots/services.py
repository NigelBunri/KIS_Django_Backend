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
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.text import slugify
from rest_framework.exceptions import NotFound, PermissionDenied, Throttled, ValidationError

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
    BotWorkflow,
    BotWorkflowRun,
    BotWorkflowStatus,
    BotWorkflowVersion,
)

SENSITIVE_ACTIONS = {
    "ban_member",
    "remove_member",
    "mute_member",
    "remove_admin",
    "change_ownership",
    "delete_content",
    "financial_action",
    "change_billing",
}

# The permission scope a given sensitive action requires, on top of the
# live grant-validity check every action already goes through. An action
# with no entry here still runs through execute_bot_action, just without
# an extra scope requirement beyond having an active grant at all.
ACTION_REQUIRED_SCOPE = {
    "ban_member": BotPermissionScope.MODERATE,
    "unban_member": BotPermissionScope.MODERATE,
    "remove_member": BotPermissionScope.MODERATE,
    "mute_member": BotPermissionScope.MODERATE,
    "unmute_member": BotPermissionScope.MODERATE,
    "remove_admin": BotPermissionScope.MODERATE,
    "delete_content": BotPermissionScope.MODERATE,
    "change_ownership": BotPermissionScope.MANAGE_RESOURCE,
    "financial_action": BotPermissionScope.MANAGE_RESOURCE,
    "change_billing": BotPermissionScope.MANAGE_RESOURCE,
}

# Abuse-detection thresholds (section 16's "complete abuse detection and
# the necessary security controls"). Deliberately conservative/fixed for
# Phase 1 rather than configurable — see module-level comment on
# _record_abuse_signal for why these are counted in a shared window
# rather than per-action-type.
BOT_AUTH_FAIL_THRESHOLD = 10
BOT_AUTH_FAIL_WINDOW_SECONDS = 15 * 60
BOT_ABUSE_SIGNAL_THRESHOLD = 20
BOT_ABUSE_SIGNAL_WINDOW_SECONDS = 60 * 60


def _cache_incr(key: str, ttl_seconds: int) -> int:
    """Atomic counter with a TTL — works the same way against the real
    Redis cache backend in production and against LocMemCache in tests.
    cache.incr() requires the key to already exist, so cache.add() seeds
    it first (a no-op if another request already seeded it)."""
    cache.add(key, 0, ttl_seconds)
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, ttl_seconds)
        return 1


def _record_abuse_signal(bot: "Bot", reason: str) -> None:
    """Call this on every denial that could indicate abuse (rate-limit
    trips, repeated auth failures, repeated permission denials). Once a
    bot accumulates enough signals in the window, it is auto-suspended —
    no human has to notice and act first."""
    if bot.status in (BotStatus.SUSPENDED, BotStatus.DELETED):
        return
    key = f"bots:abuse:{bot.id}"
    count = _cache_incr(key, BOT_ABUSE_SIGNAL_WINDOW_SECONDS)
    if count >= BOT_ABUSE_SIGNAL_THRESHOLD:
        auto_suspend_for_abuse(bot, f"{count} abuse signals ({reason}) within the last hour")


def auto_suspend_for_abuse(bot: "Bot", reason: str) -> None:
    """System-initiated suspension — distinct from set_status_admin
    (a KIS admin's deliberate action) and set_status (the owner's own
    action): nobody decided this, the platform did, so it always takes
    the SUSPENDED path regardless of VALID_TRANSITIONS, and immediately
    revokes credentials/grants exactly like an owner-initiated delete
    does (section 15's "immediately takes effect")."""
    Bot.objects.filter(id=bot.id).update(status=BotStatus.SUSPENDED, updated_at=timezone.now())
    bot.status = BotStatus.SUSPENDED
    BotCredential.objects.filter(bot=bot, is_active=True).update(
        is_active=False, revoked_at=timezone.now(),
    )
    BotResourceGrant.objects.filter(bot=bot, is_active=True).update(
        is_active=False, revoked_at=timezone.now(),
    )
    log_activity(bot, BotActivityAction.ABUSE_DETECTED, metadata={"reason": reason})


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


def _daily_usage_cache_key(bot_id) -> str:
    return f"bots:usage:{bot_id}:{timezone.now().date().isoformat()}"


def check_and_increment_bot_daily_usage(bot: "Bot") -> None:
    """Enforced at the point of every bot-initiated action (send_message_
    as_bot, execute_bot_action) — not merely a number read off the tier,
    but an actual counter that blocks the Nth+1 action once the owner's
    plan limit is reached for the day. None means unlimited (Partner Pro
    etc.) and is never counted at all."""
    limit = get_bot_daily_event_limit(bot.owner)
    if limit is None:
        return
    count = _cache_incr(_daily_usage_cache_key(bot.id), 26 * 3600)
    if count > limit:
        log_activity(
            bot, BotActivityAction.ACTION_RATE_LIMITED,
            metadata={"limit": limit, "count": count},
        )
        _record_abuse_signal(bot, "daily_rate_limit_exceeded")
        raise Throttled(detail=f"This bot has reached its daily action limit ({limit}/day).")


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


def authenticate_bot_credential(client_id: str, client_secret: str, *, ip: str | None = None) -> BotCredential | None:
    """Also the abuse-detection choke point for the OAuth2 token endpoint:
    a client_id with too many recent wrong-secret attempts is locked out
    (returns None without even checking the password, so a lockout can't
    itself be used to brute-force-probe whether a client_id exists)."""
    lockout_key = f"bots:auth_fail:{client_id}"
    if cache.get(lockout_key, 0) >= BOT_AUTH_FAIL_THRESHOLD:
        return None

    try:
        credential = BotCredential.objects.select_related("bot", "bot__owner").get(
            client_id=client_id, is_active=True,
        )
    except BotCredential.DoesNotExist:
        _cache_incr(lockout_key, BOT_AUTH_FAIL_WINDOW_SECONDS)
        return None
    if credential.bot.status != BotStatus.ACTIVE:
        return None
    if not credential.bot.owner.is_active:
        # Suspending/deactivating the owner's KIS account must take
        # immediate effect on every bot they own — including blocking
        # new token issuance, not just invalidating existing tokens.
        return None
    if not check_password(client_secret, credential.client_secret_hash):
        fail_count = _cache_incr(lockout_key, BOT_AUTH_FAIL_WINDOW_SECONDS)
        log_activity(credential.bot, BotActivityAction.AUTH_FAILED, metadata={"ip": ip})
        if fail_count >= BOT_AUTH_FAIL_THRESHOLD:
            _record_abuse_signal(credential.bot, "auth_failure_lockout")
        return None
    cache.delete(lockout_key)
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


def resolve_resource_display_name(resource_type: str, resource_id: str) -> str:
    """Phase 5 dashboard — turns a (resource_type, resource_id) pair into
    something a human can actually read, instead of a bare UUID. Same
    per-type dispatch as _resolve_conversation_id_for_resource above
    (and the same lazy imports, for the same reason: avoid an app-
    loading-order cycle). Returns a clearly-marked placeholder rather
    than raising if the underlying resource was deleted after the
    grant was created - the grant row itself is the source of truth
    for "this bot was once connected to X", not the resource's current
    existence."""
    try:
        if resource_type == BotResourceType.DIRECT_CONVERSATION:
            from apps.chat.models import Conversation

            conversation = Conversation.objects.filter(id=resource_id).first()
            if conversation and conversation.title:
                return conversation.title
            return "Direct conversation"
        if resource_type == BotResourceType.GROUP:
            from apps.groups.models import Group

            group = Group.objects.filter(id=resource_id).first()
            return group.name if group else "Deleted group"
        if resource_type == BotResourceType.CHANNEL:
            from apps.broadcasts.models import BroadcastChannel

            channel = BroadcastChannel.objects.filter(id=resource_id).first()
            return channel.display_name if channel else "Deleted channel"
        if resource_type == BotResourceType.COMMUNITY:
            from apps.communities.models import Community

            community = Community.objects.filter(id=resource_id).first()
            return community.name if community else "Deleted community"
        if resource_type == BotResourceType.PARTNER:
            from apps.partners.models import Partner

            partner = Partner.objects.filter(id=resource_id).first()
            return partner.name if partner else "Deleted partner"
        if resource_type == BotResourceType.FEED_PROFILE:
            user = User.objects.filter(id=resource_id).first()
            if not user:
                return "Deleted user's feed"
            label = user.display_name or user.username or user.phone or "User"
            return f"{label}'s feed"
    except (ValueError, TypeError):
        pass
    return "Unknown resource"


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


def _grant_is_currently_valid(grant: BotResourceGrant) -> bool:
    """Re-derives permission from scratch on every call — section 11's
    "enforce resource permissions every time a bot attempts an
    operation," not just at grant-creation time. A grant row being
    is_active=True is necessary but not sufficient: if the owner lost
    their admin role on the underlying resource since granting it, or
    the owner's own KIS account is now suspended, or the bot itself is
    no longer ACTIVE, access must be denied right now regardless of what
    the grant row says."""
    bot = grant.bot
    if bot.status != BotStatus.ACTIVE:
        return False
    if not bot.owner.is_active:
        return False
    if not grant.is_active:
        return False
    return _user_may_grant_resource(bot.owner, grant.resource_type, grant.resource_id)


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
    """Deciding APPROVED doesn't just flip a status flag — it actually
    runs the action the bot originally requested, with the deciding
    human as the one performing it (ACTION_EXECUTORS below), via the
    same executor execute_bot_action would have called had the grant
    auto-approved it. This is what makes approval meaningful: before
    this, nothing ever read BotApprovalRequest.status back out again, so
    "approving" a sensitive action did not make it happen — see
    execute_bot_action's docstring for why that is the one gate nothing
    bypasses."""
    if approval.status != BotApprovalStatus.PENDING:
        raise ValidationError({"status": "This approval request has already been decided."})
    if approval.expires_at and approval.expires_at < timezone.now():
        approval.status = BotApprovalStatus.EXPIRED
        approval.save(update_fields=["status"])
        raise ValidationError({"status": "This approval request has expired."})
    if not _user_may_grant_resource(decider, approval.resource_grant.resource_type, approval.resource_grant.resource_id):
        raise PermissionDenied("You are not authorized to decide approvals for this resource.")

    approval.status = BotApprovalStatus.APPROVED if approve else BotApprovalStatus.DECLINED
    approval.decided_by = decider
    approval.decided_at = timezone.now()

    execution_error = ""
    if approve:
        executor = ACTION_EXECUTORS.get(approval.action)
        if not executor:
            execution_error = f"'{approval.action}' has no registered executor."
        elif not _grant_is_currently_valid(approval.resource_grant):
            execution_error = "The underlying resource grant is no longer valid."
        else:
            try:
                result = executor(approval.resource_grant, approval.details or {}, decider)
            except Exception as exc:  # noqa: BLE001 — surfaced via execution_error, not raised
                execution_error = str(exc)[:500]
            else:
                log_activity(
                    approval.bot, BotActivityAction.ACTION_EXECUTED, actor=decider,
                    resource_type=approval.resource_grant.resource_type,
                    resource_id=approval.resource_grant.resource_id,
                    metadata={"action": approval.action, "result": result, "via_approval": str(approval.id)},
                )

    approval.execution_error = execution_error
    approval.save(update_fields=["status", "decided_by", "decided_at", "execution_error"])
    log_activity(
        approval.bot, BotActivityAction.APPROVAL_DECIDED, actor=decider,
        resource_type=approval.resource_grant.resource_type, resource_id=approval.resource_grant.resource_id,
        metadata={"action": approval.action, "approved": approve, "execution_error": execution_error},
    )
    return approval


# ---------------------------------------------------------------------
# Generic sensitive-action execution (section 11/12) — the ONE path any
# bot-initiated action beyond plain messaging must go through. Nothing
# else in this module performs ban_member/remove_admin/etc. directly;
# adding a new executor to ACTION_EXECUTORS is how a new action type
# becomes real, not by calling a resource's model layer from views.py.
# ---------------------------------------------------------------------

def _execute_ban_member(grant: BotResourceGrant, details: dict, actor: User) -> dict:
    """Mirrors apps.groups.views.GroupViewSet.ban's exact logic (same
    three tables updated the same way) rather than calling that view —
    this runs on behalf of a bot/approving-admin, not an HTTP request,
    and must not depend on DRF request plumbing."""
    if grant.resource_type != BotResourceType.GROUP:
        raise ValidationError({"action": "ban_member is only supported for Group resources."})
    from apps.chat.models import ConversationMember
    from apps.groups.models import Group, GroupBan, GroupMembership

    user_id = details.get("user_id")
    if not user_id:
        raise ValidationError({"user_id": "user_id is required."})
    group = Group.objects.filter(id=grant.resource_id).select_related("conversation").first()
    if not group:
        raise ValidationError({"resource": "This group no longer exists."})

    ban, _created = GroupBan.objects.update_or_create(
        group=group, user_id=user_id,
        defaults={
            "reason": str(details.get("reason") or "")[:500],
            "banned_by": actor,
            "expires_at": details.get("expires_at"),
        },
    )
    now = timezone.now()
    GroupMembership.objects.filter(group=group, user_id=user_id).update(is_banned=True, left_at=now)
    ConversationMember.objects.filter(conversation=group.conversation, user_id=user_id).update(left_at=now)
    return {"ban_id": ban.id, "user_id": str(user_id)}


def _execute_unban_member(grant: BotResourceGrant, details: dict, actor: User) -> dict:
    """Corrective (restores access, doesn't restrict it) - not in
    SENSITIVE_ACTIONS, so this never queues an approval. Supports Group
    and Community, mirroring each one's own unban() view exactly."""
    user_id = details.get("user_id")
    if not user_id:
        raise ValidationError({"user_id": "user_id is required."})

    if grant.resource_type == BotResourceType.GROUP:
        from apps.groups.models import Group, GroupBan, GroupMembership

        group = Group.objects.filter(id=grant.resource_id).first()
        if not group:
            raise ValidationError({"resource": "This group no longer exists."})
        GroupBan.objects.filter(group=group, user_id=user_id).delete()
        GroupMembership.objects.filter(group=group, user_id=user_id).update(
            is_banned=False, left_at=None, joined_at=timezone.now(),
        )
        return {"user_id": str(user_id)}

    if grant.resource_type == BotResourceType.COMMUNITY:
        from apps.communities.models import Community, CommunityBan, CommunityMembership, CommunityMembershipStatus

        community = Community.objects.filter(id=grant.resource_id).first()
        if not community:
            raise ValidationError({"resource": "This community no longer exists."})
        CommunityBan.objects.filter(community=community, user_id=user_id).delete()
        membership = CommunityMembership.objects.filter(
            community=community, user_id=user_id, status=CommunityMembershipStatus.BANNED,
        ).first()
        if membership:
            membership.status = CommunityMembershipStatus.LEFT
            membership.is_banned = False
            membership.save(update_fields=["status", "is_banned"])
        return {"user_id": str(user_id)}

    raise ValidationError({"action": "unban_member is only supported for Group and Community resources."})


def _execute_remove_member(grant: BotResourceGrant, details: dict, actor: User) -> dict:
    """Non-permanent removal - the member can rejoin later, unlike
    ban_member. Supports Group and Community, mirroring each one's own
    remove_member() view exactly (same fields, same ConversationMember
    sync)."""
    user_id = details.get("user_id")
    if not user_id:
        raise ValidationError({"user_id": "user_id is required."})
    now = timezone.now()

    if grant.resource_type == BotResourceType.GROUP:
        from apps.chat.models import ConversationMember
        from apps.groups.models import Group, GroupMembership, GroupRole

        group = Group.objects.filter(id=grant.resource_id).select_related("conversation").first()
        if not group:
            raise ValidationError({"resource": "This group no longer exists."})
        membership = GroupMembership.objects.filter(group=group, user_id=user_id, left_at__isnull=True).first()
        if not membership:
            raise ValidationError({"resource": "Member not found."})
        if membership.role == GroupRole.OWNER:
            raise ValidationError({"resource": "The owner cannot be removed."})
        membership.left_at = now
        membership.save(update_fields=["left_at"])
        ConversationMember.objects.filter(
            conversation=group.conversation, user_id=user_id, left_at__isnull=True,
        ).update(left_at=now)
        return {"user_id": str(user_id)}

    if grant.resource_type == BotResourceType.COMMUNITY:
        from apps.communities.models import Community, CommunityMembership, CommunityRole

        community = Community.objects.filter(id=grant.resource_id).first()
        if not community:
            raise ValidationError({"resource": "This community no longer exists."})
        membership = CommunityMembership.objects.active().filter(community=community, user_id=user_id).first()
        if not membership:
            raise ValidationError({"resource": "Member not found."})
        if membership.role == CommunityRole.OWNER:
            raise ValidationError({"resource": "The owner cannot be removed."})
        membership.mark_removed()
        from apps.chat.models import ConversationMember

        conversation_ids = [cid for cid in (community.main_conversation_id, community.posts_conversation_id) if cid]
        if conversation_ids:
            ConversationMember.objects.filter(
                conversation_id__in=conversation_ids, user_id=user_id, left_at__isnull=True,
            ).update(left_at=now)
        return {"user_id": str(user_id)}

    raise ValidationError({"action": "remove_member is only supported for Group and Community resources."})


def _execute_mute_member(grant: BotResourceGrant, details: dict, actor: User) -> dict:
    """Partner-only - Group/Community have no mute concept today (do not
    invent one). Mirrors moderate_member's own MUTE branch."""
    if grant.resource_type != BotResourceType.PARTNER:
        raise ValidationError({"action": "mute_member is only supported for Partner resources."})
    user_id = details.get("user_id")
    if not user_id:
        raise ValidationError({"user_id": "user_id is required."})
    from apps.partners.models import PartnerMembership

    membership = PartnerMembership.objects.filter(partner_id=grant.resource_id, user_id=user_id).first()
    if not membership:
        raise ValidationError({"resource": "Partner membership not found."})
    membership.is_muted = True
    membership.muted_until = details.get("expires_at")
    membership.save(update_fields=["is_muted", "muted_until"])
    return {"user_id": str(user_id)}


def _execute_unmute_member(grant: BotResourceGrant, details: dict, actor: User) -> dict:
    if grant.resource_type != BotResourceType.PARTNER:
        raise ValidationError({"action": "unmute_member is only supported for Partner resources."})
    user_id = details.get("user_id")
    if not user_id:
        raise ValidationError({"user_id": "user_id is required."})
    from apps.partners.models import PartnerMembership

    membership = PartnerMembership.objects.filter(partner_id=grant.resource_id, user_id=user_id).first()
    if not membership:
        raise ValidationError({"resource": "Partner membership not found."})
    membership.is_muted = False
    membership.muted_until = None
    membership.save(update_fields=["is_muted", "muted_until"])
    return {"user_id": str(user_id)}


ACTION_EXECUTORS = {
    "ban_member": _execute_ban_member,
    "unban_member": _execute_unban_member,
    "remove_member": _execute_remove_member,
    "mute_member": _execute_mute_member,
    "unmute_member": _execute_unmute_member,
}


def execute_bot_action(bot: Bot, grant: BotResourceGrant, action: str, details: dict | None = None) -> dict:
    """The single entry point for every bot-initiated action beyond
    plain messaging (see ExternalBotExecuteActionView). On every call,
    in order: re-verify the grant is CURRENTLY valid (not just was valid
    at grant time), check the action's required scope, enforce the
    owner's daily rate limit, then either queue a BotApprovalRequest
    (never executing — see requires_approval) or run the action
    immediately and log it. There is no second path anywhere in this
    codebase that performs a sensitive action without going through
    this function or decide_approval's execution branch above, which is
    what makes "sensitive operations cannot bypass pending approval" an
    actual guarantee rather than a convention."""
    action = (action or "").strip()
    if not action:
        raise ValidationError({"action": "An action is required."})
    if grant.bot_id != bot.id:
        raise PermissionDenied("This bot has no active grant for that resource.")
    if not _grant_is_currently_valid(grant):
        log_activity(
            bot, BotActivityAction.ACTION_DENIED, resource_type=grant.resource_type,
            resource_id=grant.resource_id, metadata={"action": action, "reason": "grant_invalid"},
        )
        raise PermissionDenied("This bot no longer has valid access to that resource.")

    required_scope = ACTION_REQUIRED_SCOPE.get(action)
    if required_scope and not grant_has_scope(grant, required_scope):
        log_activity(
            bot, BotActivityAction.ACTION_DENIED, resource_type=grant.resource_type,
            resource_id=grant.resource_id,
            metadata={"action": action, "reason": "missing_scope", "required_scope": required_scope},
        )
        _record_abuse_signal(bot, "missing_scope_attempt")
        raise PermissionDenied(f"This bot does not have the '{required_scope}' permission on this resource.")

    check_and_increment_bot_daily_usage(bot)

    if requires_approval(grant, action):
        approval = request_approval(bot, grant, action, details or {})
        log_activity(
            bot, BotActivityAction.ACTION_PENDING_APPROVAL, resource_type=grant.resource_type,
            resource_id=grant.resource_id, metadata={"action": action, "approval_id": str(approval.id)},
        )
        return {"status": "pending_approval", "approval_id": str(approval.id)}

    executor = ACTION_EXECUTORS.get(action)
    if not executor:
        raise ValidationError({"action": f"'{action}' is not yet a supported executable action."})
    result = executor(grant, details or {}, bot.owner)
    log_activity(
        bot, BotActivityAction.ACTION_EXECUTED, resource_type=grant.resource_type,
        resource_id=grant.resource_id, metadata={"action": action, "result": result},
    )
    return {"status": "executed", "result": result}


# ---------------------------------------------------------------------
# Sending a message as a bot — reuses the EXACT existing system-message
# pipeline (apps.chat.tasks.send_system_message_as_user -> Nest's
# internal/messages/send-as-user), not a new send path.
# ---------------------------------------------------------------------

def send_message_as_bot(bot: Bot, grant: BotResourceGrant, text: str, *, client_id: str | None = None) -> dict:
    if grant.bot_id != bot.id:
        raise PermissionDenied("This bot has no active grant for that resource.")
    if not _grant_is_currently_valid(grant):
        log_activity(
            bot, BotActivityAction.ACTION_DENIED, resource_type=grant.resource_type,
            resource_id=grant.resource_id, metadata={"action": "send_message", "reason": "grant_invalid"},
        )
        raise PermissionDenied("This bot no longer has valid access to that resource.")
    if not grant_has_scope(grant, BotPermissionScope.SEND_MESSAGES):
        log_activity(
            bot, BotActivityAction.ACTION_DENIED, resource_type=grant.resource_type,
            resource_id=grant.resource_id, metadata={"action": "send_message", "reason": "missing_scope"},
        )
        raise PermissionDenied("This bot does not have send_messages permission on this resource.")
    if not grant.conversation_id:
        raise ValidationError({"resource": "This resource has no linked conversation to send into."})

    check_and_increment_bot_daily_usage(bot)

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
    # Replay protection (section 9/16): sign over "{timestamp}.{body}",
    # the same scheme Stripe's webhooks use, and send the timestamp as
    # its own header. A receiver that (a) rejects a timestamp outside a
    # short tolerance window (e.g. 5 minutes) and (b) recomputes the
    # signature over "{header_timestamp}.{raw_body}" cannot be replayed
    # with a captured request after that window closes — signing the
    # body ALONE (the previous scheme) let a captured request be resent
    # indefinitely since the signature never changed. event_id's
    # UniqueConstraint on BotWebhookDelivery is KIS's own replay defense
    # for redelivery from this side; this header is for the receiver's.
    timestamp = str(int(timezone.now().timestamp()))
    signed_payload = f"{timestamp}.".encode("utf-8") + body_bytes
    signature = hmac.new(subscription.secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-KIS-Bot-Event": delivery.event,
        "X-KIS-Bot-Id": str(subscription.bot_id),
        "X-KIS-Timestamp": timestamp,
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


def dispatch_bot_events_for_conversation(
    conversation_id, event: str, payload: dict, *, event_id: str,
    required_scope: str = BotPermissionScope.READ_MESSAGES,
) -> int:
    """Entry point from apps.chat.views.webhook_dispatch (message events,
    the EXISTING Nest -> Django call) and from apps.groups' member.
    joined/member.left publishers (Phase 2) - fans out to every active
    bot resource grant on this conversation that holds `required_scope`,
    both as a webhook delivery AND as a workflow trigger (a workflow is
    a separate concept from a webhook subscription: one per resource
    grant, not per bot). required_scope defaults to READ_MESSAGES for
    the message-event call sites; member events pass READ_MEMBERS - a
    workflow or webhook must never react to an event its grant's scopes
    don't actually cover. Never raises - a bot delivery failure must
    never affect the caller (which is itself on the critical message-
    send path for message events)."""
    delivered = 0
    grants = BotResourceGrant.objects.filter(
        conversation_id=conversation_id, is_active=True,
    ).select_related("bot", "bot__webhook_subscription")
    resource_keys_dispatched = set()
    for grant in grants:
        try:
            if grant_has_scope(grant, required_scope):
                if dispatch_bot_event(grant, event, payload, event_id=f"{event_id}:{grant.id}"):
                    delivered += 1
                # dispatch_event_to_workflows does its own per-workflow
                # scope check, so one call per distinct (resource_type,
                # resource_id) pair on this conversation is enough - a
                # second grant on the same resource would just redo an
                # already-idempotent dispatch.
                key = (grant.resource_type, grant.resource_id)
                if key not in resource_keys_dispatched:
                    resource_keys_dispatched.add(key)
                    from .workflow_engine import dispatch_event_to_workflows

                    dispatch_event_to_workflows(
                        resource_type=grant.resource_type, resource_id=grant.resource_id,
                        event=event, payload=payload, event_id=event_id, required_scope=required_scope,
                    )
        except Exception:  # noqa: BLE001
            continue
    return delivered


def dispatch_resource_event(
    resource_type: str, resource_id, event: str, payload: dict, *, event_id: str, required_scope: str,
) -> int:
    """The non-conversation counterpart to dispatch_bot_events_for_
    conversation — for events that have no backing chat Conversation at
    all (a Channel post, a Community comment, My Feed entry, Channel/
    Community/Partner membership). Matches grants directly by
    (resource_type, resource_id) instead of conversation_id, then does
    the exact same webhook-delivery + workflow-trigger fan-out. Never
    raises - a publisher failure must never break the real user action
    (posting, joining, commenting) that triggered it."""
    delivered = 0
    grants = BotResourceGrant.objects.filter(
        resource_type=resource_type, resource_id=str(resource_id), is_active=True,
    ).select_related("bot", "bot__webhook_subscription")
    for grant in grants:
        try:
            if grant_has_scope(grant, required_scope):
                if dispatch_bot_event(grant, event, payload, event_id=f"{event_id}:{grant.id}"):
                    delivered += 1
        except Exception:  # noqa: BLE001
            continue

    try:
        from .workflow_engine import dispatch_event_to_workflows

        dispatch_event_to_workflows(
            resource_type=resource_type, resource_id=str(resource_id),
            event=event, payload=payload, event_id=event_id, required_scope=required_scope,
        )
    except Exception:  # noqa: BLE001
        pass
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


def list_webhook_deliveries(bot: Bot, user: User):
    """Phase 5 dashboard — owner-facing delivery history. Deliberately
    excludes `payload` at the serializer layer (not here) - this is
    for inspecting delivery STATUS, not for re-reading event content
    the owner already has through its original source."""
    _require_owner(bot, user)
    subscription = getattr(bot, "webhook_subscription", None)
    if not subscription:
        return BotWebhookDelivery.objects.none()
    return subscription.deliveries.order_by("-created_at")


SECURITY_RELEVANT_ACTIONS = [
    BotActivityAction.SUSPENDED_BY_ADMIN,
    BotActivityAction.ABUSE_DETECTED,
    BotActivityAction.AUTH_FAILED,
    BotActivityAction.ACTION_DENIED,
    BotActivityAction.ACTION_RATE_LIMITED,
    BotActivityAction.CREDENTIAL_REVOKED,
    BotActivityAction.RESOURCE_REVOKED,
]


def get_owner_dashboard_overview(user: User) -> dict:
    """Phase 5 — single aggregation call backing the owner's dashboard
    Overview. Every count/list here is scoped to `user`'s OWN bots via
    the `bot__owner=user` (or `bot_id__in=owner_bot_ids`) filter on
    each query - there is no code path here that can return another
    owner's data. All numbers come from real persisted rows; nothing
    here is a placeholder."""
    from django.db.models import Count, Q

    from .models import (
        BotAIUsageLog, BotAIUsageLogStatus, BotKnowledgeSource,
        BotKnowledgeSourceStatus, BotMemoryConfig, BotMemoryEntry, BotWorkflowRunStatus,
    )

    bots_qs = Bot.objects.filter(owner=user).exclude(status=BotStatus.DELETED)
    bot_ids = list(bots_qs.values_list("id", flat=True))
    status_counts = dict(bots_qs.values_list("status").annotate(count=Count("id")))

    since_7d = timezone.now() - timedelta(days=7)

    runs_qs = BotWorkflowRun.objects.filter(workflow__bot_id__in=bot_ids)
    recent_runs = list(
        runs_qs.select_related("workflow", "workflow__bot").order_by("-started_at")[:10]
    )

    pending_approvals = list(
        BotApprovalRequest.objects.filter(bot_id__in=bot_ids, status=BotApprovalStatus.PENDING)
        .select_related("bot").order_by("-created_at")[:20]
    )

    recent_security_events = list(
        BotActivityLog.objects.filter(bot_id__in=bot_ids, action__in=SECURITY_RELEVANT_ACTIONS)
        .select_related("bot").order_by("-created_at")[:20]
    )

    webhook_deliveries = BotWebhookDelivery.objects.filter(subscription__bot_id__in=bot_ids)
    webhook_status = {
        "bots_with_webhook": BotWebhookSubscription.objects.filter(bot_id__in=bot_ids, is_active=True).count(),
        "pending": webhook_deliveries.filter(status=BotWebhookDeliveryStatus.PENDING).count(),
        "failed": webhook_deliveries.filter(status=BotWebhookDeliveryStatus.FAILED).count(),
        "dead_letter": webhook_deliveries.filter(status=BotWebhookDeliveryStatus.DEAD_LETTER).count(),
    }

    ai_usage_qs = BotAIUsageLog.objects.filter(bot_id__in=bot_ids)
    ai_usage_summary = {
        "bots_with_ai_connected": BotAIConnection.objects.filter(bot_id__in=bot_ids, is_active=True).count(),
        "calls_7d": ai_usage_qs.filter(created_at__gte=since_7d).count(),
        "errors_7d": ai_usage_qs.filter(created_at__gte=since_7d, status=BotAIUsageLogStatus.ERROR).count(),
    }

    knowledge_qs = BotKnowledgeSource.objects.filter(bot_id__in=bot_ids)
    knowledge_summary = dict(knowledge_qs.values_list("status").annotate(count=Count("id")))

    memory_summary = {
        "bots_with_memory_enabled": BotMemoryConfig.objects.filter(bot_id__in=bot_ids, enabled=True).count(),
        "total_entries": BotMemoryEntry.objects.filter(bot_id__in=bot_ids).count(),
    }

    return {
        "bots": {
            "total": len(bot_ids),
            "active": status_counts.get(BotStatus.ACTIVE, 0),
            "paused": status_counts.get(BotStatus.PAUSED, 0),
            "suspended": status_counts.get(BotStatus.SUSPENDED, 0),
            "disconnected": status_counts.get(BotStatus.DISCONNECTED, 0),
            "draft": status_counts.get(BotStatus.DRAFT, 0),
        },
        "executions": {
            "succeeded_7d": runs_qs.filter(started_at__gte=since_7d, status=BotWorkflowRunStatus.SUCCEEDED).count(),
            "failed_7d": runs_qs.filter(started_at__gte=since_7d, status=BotWorkflowRunStatus.FAILED).count(),
            "recent": [
                {
                    "id": str(r.id), "bot_id": str(r.workflow.bot_id), "bot_name": r.workflow.bot.name,
                    "workflow_id": str(r.workflow_id), "workflow_name": r.workflow.name,
                    "trigger_event": r.trigger_event, "status": r.status, "started_at": r.started_at.isoformat(),
                }
                for r in recent_runs
            ],
        },
        "pending_approvals": [
            {
                "id": str(a.id), "bot_id": str(a.bot_id), "bot_name": a.bot.name,
                "action": a.action, "created_at": a.created_at.isoformat(),
            }
            for a in pending_approvals
        ],
        "recent_security_events": [
            {
                "id": str(e.id), "bot_id": str(e.bot_id), "bot_name": e.bot.name,
                "action": e.action, "created_at": e.created_at.isoformat(),
            }
            for e in recent_security_events
        ],
        "webhooks": webhook_status,
        "ai_usage": ai_usage_summary,
        "knowledge": {
            "total_sources": sum(knowledge_summary.values()),
            "ready": knowledge_summary.get(BotKnowledgeSourceStatus.READY, 0),
            "processing": knowledge_summary.get(BotKnowledgeSourceStatus.PROCESSING, 0) + knowledge_summary.get(BotKnowledgeSourceStatus.PENDING, 0),
            "error": knowledge_summary.get(BotKnowledgeSourceStatus.ERROR, 0),
        },
        "memory": memory_summary,
    }


def retry_webhook_delivery(bot: Bot, user: User, delivery_id) -> BotWebhookDelivery:
    """Manual retry, owner-triggered. Only ever re-attempts a delivery
    that is FAILED or DEAD_LETTER - a DELIVERED one is never re-sent
    from here, since _attempt_delivery re-POSTs the exact same payload
    to the owner's own endpoint and re-sending something that already
    succeeded is exactly the duplicate-business-action risk this must
    not create. The idempotency guarantee itself (the same event_id
    never producing two delivery ROWS) is the DB UniqueConstraint on
    BotWebhookDelivery - unaffected either way."""
    _require_owner(bot, user)
    subscription = getattr(bot, "webhook_subscription", None)
    if not subscription:
        raise ValidationError({"detail": "This bot has no webhook configured."})
    delivery = subscription.deliveries.filter(id=delivery_id).first()
    if not delivery:
        raise ValidationError({"detail": "Delivery not found."})
    if delivery.status not in (BotWebhookDeliveryStatus.FAILED, BotWebhookDeliveryStatus.DEAD_LETTER):
        raise ValidationError({"detail": f"Only failed or dead-lettered deliveries can be retried (this one is {delivery.status})."})
    _attempt_delivery(subscription, delivery)
    delivery.refresh_from_db()
    return delivery


# ---------------------------------------------------------------------
# Phase 5 — owner-facing analytics, with the same filters the CSV
# export below applies: date range, bot, workflow, event type, run
# status. Every query here is scoped to `user`'s own bots - the same
# bot_id__in=owner_bot_ids boundary as get_owner_dashboard_overview.
# ---------------------------------------------------------------------

def _owner_bot_ids(user: User, bot_id: str | None = None) -> list:
    qs = Bot.objects.filter(owner=user).exclude(status=BotStatus.DELETED)
    if bot_id:
        qs = qs.filter(id=bot_id)
    return list(qs.values_list("id", flat=True))


def _filtered_workflow_runs(user: User, filters: dict):
    bot_ids = _owner_bot_ids(user, filters.get("bot_id"))
    qs = BotWorkflowRun.objects.filter(workflow__bot_id__in=bot_ids).select_related("workflow", "workflow__bot")
    if filters.get("workflow_id"):
        qs = qs.filter(workflow_id=filters["workflow_id"])
    if filters.get("event_type"):
        qs = qs.filter(trigger_event=filters["event_type"])
    if filters.get("run_status"):
        qs = qs.filter(status=filters["run_status"])
    if filters.get("date_from"):
        qs = qs.filter(started_at__gte=filters["date_from"])
    if filters.get("date_to"):
        qs = qs.filter(started_at__lte=filters["date_to"])
    return qs


def get_owner_analytics(user: User, filters: dict) -> dict:
    """Phase 5 — GET /api/v1/bots/analytics/. Same filter set as the
    CSV export (get_owner_analytics_rows) so the numbers shown on
    screen and the rows in the download always describe the same
    underlying query."""
    from django.db.models import Avg, DurationField, ExpressionWrapper, F, Sum

    from .models import (
        BotApprovalRequest, BotApprovalStatus, BotWebhookDelivery,
        BotWebhookDeliveryStatus, BotWorkflowRunStatus,
    )

    bot_ids = _owner_bot_ids(user, filters.get("bot_id"))
    runs = _filtered_workflow_runs(user, filters)

    duration_expr = ExpressionWrapper(F("finished_at") - F("started_at"), output_field=DurationField())
    finished_runs = runs.filter(finished_at__isnull=False)
    avg_duration = finished_runs.annotate(duration=duration_expr).aggregate(avg=Avg("duration"))["avg"]

    action_counts: dict[str, int] = {}
    for run in finished_runs[:5000]:
        for step in (run.steps_log or []):
            action = step.get("action")
            if action:
                action_counts[action] = action_counts.get(action, 0) + 1

    webhook_qs = BotWebhookDelivery.objects.filter(subscription__bot_id__in=bot_ids)
    approvals_qs = BotApprovalRequest.objects.filter(bot_id__in=bot_ids)

    return {
        "events_received": runs.count(),
        "executions": {
            "total": runs.count(),
            "succeeded": runs.filter(status=BotWorkflowRunStatus.SUCCEEDED).count(),
            "failed": runs.filter(status=BotWorkflowRunStatus.FAILED).count(),
            "denied": runs.filter(status=BotWorkflowRunStatus.DENIED).count(),
            "skipped": runs.filter(status=BotWorkflowRunStatus.SKIPPED).count(),
            "avg_duration_seconds": avg_duration.total_seconds() if avg_duration else None,
        },
        "action_counts": action_counts,
        "webhooks": {
            "deliveries": webhook_qs.count(),
            "delivered": webhook_qs.filter(status=BotWebhookDeliveryStatus.DELIVERED).count(),
            "failed": webhook_qs.filter(status=BotWebhookDeliveryStatus.FAILED).count(),
            "dead_letter": webhook_qs.filter(status=BotWebhookDeliveryStatus.DEAD_LETTER).count(),
            "total_retry_attempts": webhook_qs.aggregate(s=Sum("attempt_count"))["s"] or 0,
        },
        "approvals": {
            "total": approvals_qs.count(),
            "pending": approvals_qs.filter(status=BotApprovalStatus.PENDING).count(),
            "approved": approvals_qs.filter(status=BotApprovalStatus.APPROVED).count(),
            "declined": approvals_qs.filter(status=BotApprovalStatus.DECLINED).count(),
            "expired": approvals_qs.filter(status=BotApprovalStatus.EXPIRED).count(),
        },
        "suspensions": BotActivityLog.objects.filter(
            bot_id__in=bot_ids, action=BotActivityAction.SUSPENDED_BY_ADMIN,
        ).count(),
        "rate_limit_events": BotActivityLog.objects.filter(
            bot_id__in=bot_ids, action=BotActivityAction.ACTION_RATE_LIMITED,
        ).count(),
        "ai": _owner_ai_analytics(bot_ids),
        "knowledge": _owner_knowledge_analytics(bot_ids),
        "memory": _owner_memory_analytics(bot_ids),
    }


def _owner_ai_analytics(bot_ids: list) -> dict:
    from django.db.models import Count, Sum

    from .models import BotAIUsageLog, BotAIUsageLogStatus

    qs = BotAIUsageLog.objects.filter(bot_id__in=bot_ids)
    by_provider = list(qs.values("provider", "model").annotate(requests=Count("id")).order_by("-requests")[:10])
    totals = qs.aggregate(
        prompt_tokens=Sum("prompt_tokens"), completion_tokens=Sum("completion_tokens"),
    )
    return {
        "total_requests": qs.count(),
        "error_requests": qs.filter(status=BotAIUsageLogStatus.ERROR).count(),
        "by_provider_model": by_provider,
        "total_prompt_tokens": totals["prompt_tokens"] or 0,
        "total_completion_tokens": totals["completion_tokens"] or 0,
        # Deliberately no dollar figure - provider pricing varies by
        # model and changes over time; billing is between the bot
        # owner and their own chosen provider, never KIS.
        "billing_note": "Usage quantities only. Billing is determined by the bot owner's own AI provider, not KIS.",
    }


def _owner_knowledge_analytics(bot_ids: list) -> dict:
    from django.db.models import Count

    from .models import BotKnowledgeSource

    qs = BotKnowledgeSource.objects.filter(bot_id__in=bot_ids)
    by_status = dict(qs.values_list("status").annotate(c=Count("id")))
    return {
        "total_sources": qs.count(),
        "by_status": by_status,
        # Per-retrieval-call analytics (requests/results/sources
        # returned) are not separately instrumented beyond this:
        # every AI decision call already attempts exactly one
        # retrieval, so `ai.total_requests` above is that count: a
        # second counter logging the identical event on every single
        # AI decision would be pure write-amplification for no new
        # information. The "test retrieval" tool is explicitly a
        # one-off manual check, not a production metric.
    }


def _owner_memory_analytics(bot_ids: list) -> dict:
    from .models import BotMemoryConfig, BotMemoryEntry

    return {
        "bots_with_memory_enabled": BotMemoryConfig.objects.filter(bot_id__in=bot_ids, enabled=True).count(),
        "total_entries": BotMemoryEntry.objects.filter(bot_id__in=bot_ids).count(),
    }


def get_owner_analytics_rows(user: User, filters: dict):
    """Row-level data backing the CSV export - one row per workflow
    run, same filters as get_owner_analytics above."""
    return _filtered_workflow_runs(user, filters).order_by("-started_at")[:10000]


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


# ---------------------------------------------------------------------
# Phase 2 — no-code workflow lifecycle (create/edit/publish/pause/
# resume/delete, version snapshots). Every function here re-checks
# ownership and re-derives the resource_grant's live validity - a
# workflow is configuration on top of ONE existing grant, never a
# broader authority than that grant already carries (section 4's "a
# workflow must never gain permissions simply because its owner can
# access a resource").
# ---------------------------------------------------------------------

def _require_own_grant(bot: Bot, user: User, resource_grant_id) -> BotResourceGrant:
    _require_owner(bot, user)
    grant = BotResourceGrant.objects.filter(id=resource_grant_id, bot=bot, is_active=True).first()
    if not grant:
        raise ValidationError({"resource_grant_id": "This bot has no active grant for that resource."})
    return grant


def create_workflow(
    bot: Bot, user: User, *, resource_grant_id, name: str, description: str = "",
    trigger_type: str, trigger_config: dict | None = None, definition: dict | None = None,
) -> BotWorkflow:
    from .workflow_engine import validate_definition, validate_trigger_config

    grant = _require_own_grant(bot, user, resource_grant_id)
    name = (name or "").strip()
    if not name:
        raise ValidationError({"name": "A workflow name is required."})
    from .models import BotWorkflowTriggerType

    if trigger_type not in BotWorkflowTriggerType.values:
        raise ValidationError({"trigger_type": f"Unsupported trigger type '{trigger_type}'."})
    clean_trigger_config = validate_trigger_config(trigger_type, trigger_config or {})
    clean_definition = validate_definition(definition or {})

    workflow = BotWorkflow.objects.create(
        bot=bot, resource_grant=grant, name=name[:160], description=description[:4000],
        trigger_type=trigger_type, trigger_config=clean_trigger_config, definition=clean_definition,
        status=BotWorkflowStatus.DRAFT,
    )
    log_activity(
        bot, BotActivityAction.WORKFLOW_CREATED, actor=user,
        resource_type=grant.resource_type, resource_id=grant.resource_id,
        metadata={"workflow_id": str(workflow.id), "trigger_type": trigger_type},
    )
    return workflow


def _require_own_workflow(bot: Bot, user: User, workflow_id) -> BotWorkflow:
    _require_owner(bot, user)
    workflow = BotWorkflow.objects.filter(
        id=workflow_id, bot=bot,
    ).exclude(status=BotWorkflowStatus.DELETED).select_related("resource_grant").first()
    if not workflow:
        raise NotFound("Workflow not found.")
    return workflow


def update_workflow(workflow: BotWorkflow, user: User, **fields) -> BotWorkflow:
    """Only a DRAFT (or a PAUSED workflow being edited before republish)
    may have its definition/trigger edited directly - an ACTIVE
    workflow's live definition is immutable; pause it first. This is
    what makes "safe updates" (section 1) actual: there is no path to
    silently change what a currently-running automation does."""
    from .workflow_engine import validate_definition, validate_trigger_config

    _require_owner(workflow.bot, user)
    if workflow.status == BotWorkflowStatus.ACTIVE:
        raise ValidationError({"status": "Pause this workflow before editing it."})

    changed = []
    if "name" in fields and fields["name"] is not None:
        name = str(fields["name"]).strip()
        if not name:
            raise ValidationError({"name": "A workflow name is required."})
        workflow.name = name[:160]
        changed.append("name")
    if "description" in fields and fields["description"] is not None:
        workflow.description = str(fields["description"])[:4000]
        changed.append("description")
    if "trigger_type" in fields and fields["trigger_type"] is not None:
        workflow.trigger_type = fields["trigger_type"]
        changed.append("trigger_type")
    if "trigger_config" in fields and fields["trigger_config"] is not None:
        workflow.trigger_config = validate_trigger_config(
            fields.get("trigger_type", workflow.trigger_type), fields["trigger_config"],
        )
        changed.append("trigger_config")
    if "definition" in fields and fields["definition"] is not None:
        workflow.definition = validate_definition(fields["definition"])
        changed.append("definition")

    if changed:
        workflow.save(update_fields=[*changed, "updated_at"])
        log_activity(
            workflow.bot, BotActivityAction.UPDATED, actor=user,
            metadata={"workflow_id": str(workflow.id), "fields": changed},
        )
    return workflow


def test_workflow(workflow: BotWorkflow, user: User, sample_payload: dict | None = None) -> dict:
    """A pure, side-effect-free dry run (section 1's "test before
    publishing" / section 6's "test workflows before publishing") - the
    SAME condition evaluator the real engine uses, but actions are only
    described, never executed, and nothing is persisted. Allowed in any
    status (including DRAFT) so an owner can iterate before ever
    activating anything."""
    from .workflow_engine import build_event_context, evaluate_conditions

    _require_owner(workflow.bot, user)
    context = build_event_context(workflow.trigger_type, sample_payload or {})
    condition_result = evaluate_conditions(workflow.definition.get("conditions") or {}, context)
    branch = workflow.definition.get("then_actions" if condition_result else "else_actions") or []
    return {
        "event_context": context,
        "condition_result": condition_result,
        "would_run_actions": [
            {"type": a.get("type"), "params": a.get("params", {}), "delay_seconds": a.get("delay_seconds", 0)}
            for a in branch
        ],
    }


def publish_workflow(workflow: BotWorkflow, user: User) -> BotWorkflow:
    _require_owner(workflow.bot, user)
    if workflow.status == BotWorkflowStatus.DELETED:
        raise ValidationError({"status": "A deleted workflow cannot be published."})
    if not _grant_is_currently_valid(workflow.resource_grant):
        raise PermissionDenied("This bot no longer has valid access to the resource this workflow runs against.")

    from .workflow_engine import validate_definition, validate_trigger_config

    validate_definition(workflow.definition or {})
    validate_trigger_config(workflow.trigger_type, workflow.trigger_config or {})

    is_republish = workflow.published_at is not None
    with transaction.atomic():
        if is_republish:
            workflow.version += 1
        workflow.status = BotWorkflowStatus.ACTIVE
        workflow.published_at = timezone.now()
        workflow.save(update_fields=["status", "version", "published_at", "updated_at"])
        BotWorkflowVersion.objects.create(
            workflow=workflow, version=workflow.version, trigger_type=workflow.trigger_type,
            trigger_config=workflow.trigger_config, definition=workflow.definition, created_by=user,
        )
    log_activity(
        workflow.bot, BotActivityAction.WORKFLOW_PUBLISHED, actor=user,
        metadata={"workflow_id": str(workflow.id), "version": workflow.version},
    )
    return workflow


def pause_workflow(workflow: BotWorkflow, user: User) -> BotWorkflow:
    _require_owner(workflow.bot, user)
    if workflow.status != BotWorkflowStatus.ACTIVE:
        raise ValidationError({"status": "Only an active workflow can be paused."})
    workflow.status = BotWorkflowStatus.PAUSED
    workflow.save(update_fields=["status", "updated_at"])
    log_activity(workflow.bot, BotActivityAction.WORKFLOW_PAUSED, actor=user, metadata={"workflow_id": str(workflow.id)})
    return workflow


def resume_workflow(workflow: BotWorkflow, user: User) -> BotWorkflow:
    _require_owner(workflow.bot, user)
    if workflow.status != BotWorkflowStatus.PAUSED:
        raise ValidationError({"status": "Only a paused workflow can be resumed."})
    if not _grant_is_currently_valid(workflow.resource_grant):
        raise PermissionDenied("This bot no longer has valid access to the resource this workflow runs against.")
    workflow.status = BotWorkflowStatus.ACTIVE
    workflow.save(update_fields=["status", "updated_at"])
    log_activity(workflow.bot, BotActivityAction.WORKFLOW_RESUMED, actor=user, metadata={"workflow_id": str(workflow.id)})
    return workflow


def delete_workflow(workflow: BotWorkflow, user: User) -> None:
    _require_owner(workflow.bot, user)
    workflow.status = BotWorkflowStatus.DELETED
    workflow.deleted_at = timezone.now()
    workflow.save(update_fields=["status", "deleted_at", "updated_at"])
    log_activity(workflow.bot, BotActivityAction.WORKFLOW_DELETED, actor=user, metadata={"workflow_id": str(workflow.id)})
