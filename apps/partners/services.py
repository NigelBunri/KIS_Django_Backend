# apps/partners/services.py
from typing import Optional, Set
from datetime import timedelta

from django.utils import timezone

from django.db import DatabaseError, models, transaction
from django.db.models import Count
from django.db.models.functions import TruncDate

from apps.accounts.models import AccountTier, User
from apps.accounts.tiers import get_aggregated_tier_features, get_user_tier, normalize_limit_value
from apps.partners.models import (
    Partner,
    PartnerPolicy,
    PartnerMembership,
    PartnerMembershipStatus,
    PartnerApplication,
    PartnerPost,
    PartnerPostComment,
    PartnerPostReaction,
    PartnerRoleAssignment,
    PartnerRole,
    PartnerAuditEvent,
    PartnerAutomationRule,
    PartnerReportSnapshot,
    PartnerExportJob,
    PartnerExportStatus,
    PartnerFeatureFlag,
    PartnerExportScheduleFrequency,
    PartnerWebhook,
    PartnerWebhookDelivery,
    PartnerWebhookDeliveryStatus,
    PartnerOrganizationApp,
    PartnerOrganizationAppType,
    PartnerOrganizationAppAccessLog,
    PartnerChannelPermissionOverwrite,
    PartnerModerationAction,
    PartnerSubscription,
)
import json
import hmac
import hashlib
import urllib.request
from apps.chat.models import (
    Conversation,
    ConversationType,
    ConversationSettings,
    ConversationMember,
    BaseConversationRole,
)


@transaction.atomic
def create_partner_with_main_conversation(
    *,
    owner: User,
    name: str,
    slug: str,
    description: str = "",
    avatar_url: str = "",
    create_main_conversation: bool = True,
) -> Partner:
    """
    Service layer for creating a Partner and (optionally) its main conversation.
    """
    main_conversation = None

    if create_main_conversation:
        main_conversation = Conversation.objects.create(
            type=ConversationType.POST,
            title=name,
            description=f"Post space for partner {name}",
            created_by=owner,
        )

        ConversationMember.objects.create(
            conversation=main_conversation,
            user=owner,
            base_role=BaseConversationRole.OWNER,
        )

        ConversationSettings.objects.create(conversation=main_conversation)

    partner = Partner.objects.create(
        name=name,
        slug=slug,
        description=description,
        avatar_url=avatar_url,
        owner=owner,
        main_conversation=main_conversation,
    )
    ensure_partner_subscription(partner)

    return partner


def ensure_partner_subscription(partner: Partner) -> PartnerSubscription:
    """
    Bootstraps a new Partner's workspace-level plan at creation time,
    defaulting to whatever the owner's OWN personal tier currently is —
    a reasonable starting point ("your org starts where your plan is"),
    not an ongoing link. From this point on the two are independent (see
    PartnerSubscription's docstring): upgrading/downgrading the owner's
    personal plan later has no effect on this row, and vice versa.
    """
    existing = PartnerSubscription.objects.filter(partner=partner).first()
    if existing:
        return existing
    owner_tier = get_user_tier(partner.owner)
    return PartnerSubscription.objects.create(partner=partner, tier=owner_tier)


def ensure_partner_policy(partner: Partner) -> PartnerPolicy:
    policy, _ = PartnerPolicy.objects.get_or_create(partner=partner)
    return policy


def apply_partner_policy(partner: Partner) -> None:
    policy = ensure_partner_policy(partner)
    retention = (policy.settings or {}).get("retention", {})
    message_retention = retention.get("message_retention_days")
    conversation_ids = set()
    if partner.main_conversation_id:
        conversation_ids.add(str(partner.main_conversation_id))

    try:
        from apps.communities.models import Community
        from apps.groups.models import Group
        from apps.channels.models import Channel
    except ImportError:
        Community = Group = Channel = None

    if Community is not None:
        try:
            community_ids = Community.objects.filter(partner=partner).order_by().values_list(
                "main_conversation_id",
                "posts_conversation_id",
            )
            for main_id, posts_id in community_ids:
                if main_id:
                    conversation_ids.add(str(main_id))
                if posts_id:
                    conversation_ids.add(str(posts_id))
        except DatabaseError:
            pass

    if Group is not None:
        try:
            group_ids = Group.objects.filter(partner=partner).order_by().values_list("conversation_id", flat=True)
            for gid in group_ids:
                if gid:
                    conversation_ids.add(str(gid))
        except DatabaseError:
            pass

    if Channel is not None:
        try:
            channel_ids = Channel.objects.filter(partner=partner).order_by().values_list("conversation_id", flat=True)
            for cid in channel_ids:
                if cid:
                    conversation_ids.add(str(cid))
        except DatabaseError:
            pass

    for conv_id in conversation_ids:
        ConversationSettings.objects.update_or_create(
            conversation_id=conv_id,
            defaults={"message_retention_days": message_retention},
        )


def ensure_default_partner_roles(partner: Partner) -> None:
    defaults = [
        ("Owner", [
            "partner.policy.edit",
            "partner.roles.manage",
            "partner.audit.view",
            "partner.integrations.manage",
            "partner.integrations.view",
            "partner.automation.manage",
            "partner.automation.view",
            "partner.reports.view",
            "partner.exports.manage",
            "partner.exports.view",
            "partner.access.manage",
            "partner.access.view",
            "partner.settings.manage",
            "partner.settings.view",
            "partner.members.kick",
            "partner.members.ban",
            "partner.categories.manage",
        ]),
        ("Admin", [
            "partner.policy.edit",
            "partner.roles.view",
            "partner.audit.view",
            "partner.integrations.manage",
            "partner.integrations.view",
            "partner.automation.manage",
            "partner.automation.view",
            "partner.reports.view",
            "partner.exports.manage",
            "partner.exports.view",
            "partner.access.manage",
            "partner.access.view",
            "partner.settings.manage",
            "partner.settings.view",
            "partner.members.kick",
            "partner.members.ban",
            "partner.categories.manage",
        ]),
        ("Manager", [
            "partner.roles.view",
            "partner.audit.view",
            "partner.integrations.view",
            "partner.automation.view",
            "partner.reports.view",
            "partner.exports.view",
            "partner.access.view",
            "partner.settings.view",
        ]),
        ("Analyst", ["partner.audit.view", "partner.reports.view"]),
        ("Member", []),
    ]
    for name, permissions in defaults:
        PartnerRole.objects.update_or_create(
            partner=partner,
            name=name,
            defaults={
                "description": f"Default {name} role.",
                "permissions": permissions,
                "is_default": name == "Member",
            },
        )


def ensure_default_organization_app(
    partner: Partner,
    app_type: str = PartnerOrganizationAppType.KIS,
    defaults: Optional[dict] = None,
) -> PartnerOrganizationApp:
    defaults = defaults or {}
    default_data = {
        "name": defaults.get("name", "KIS app"),
        "slug": defaults.get("slug", app_type),
        "description": defaults.get("description", "Core partner workspace."),
        "link": defaults.get("link", ""),
        "module": defaults.get("module", "partner.kis.workspace"),
        "icon": defaults.get("icon", ""),
        "order": defaults.get("order", 0),
        "badge_label": defaults.get("badge_label", ""),
        "is_active": defaults.get("is_active", True),
        "status": defaults.get("status", "published"),
        "is_promoted_global": defaults.get("is_promoted_global", False),
        "promoted_order": defaults.get("promoted_order", 0),
        "published_at": defaults.get("published_at", timezone.now()),
        "metadata": defaults.get("metadata", {"internal": True}),
        "config": defaults.get("config", {}),
    }
    default_data["type"] = app_type
    app, _ = PartnerOrganizationApp.objects.get_or_create(
        partner=partner,
        type=app_type,
        defaults=default_data,
    )
    update_fields = []
    for key, value in default_data.items():
        if getattr(app, key, None) in ("", None, {}, []) or key in {"name", "slug", "description", "module", "is_active"}:
            if getattr(app, key, None) != value:
                setattr(app, key, value)
                update_fields.append(key)
    if update_fields:
        update_fields.append("updated_at")
        app.save(update_fields=update_fields)
    return app


def log_organization_app_access(
    *,
    user: User,
    app: PartnerOrganizationApp,
    action: str,
    data_scope: Optional[list] = None,
    consent: bool = False,
) -> PartnerOrganizationAppAccessLog:
    return PartnerOrganizationAppAccessLog.objects.create(
        user=user,
        app=app,
        action=action,
        data_scope=data_scope or [],
        consent=consent,
    )


def _get_member_conversation_role(partner: Partner, user: Optional[User]) -> str | None:
    if not user or user.is_anonymous:
        return None
    if not partner.main_conversation_id:
        return None
    member = partner.main_conversation.memberships.filter(
        user=user,
        left_at__isnull=True,
    ).first()
    return member.base_role if member else None


def _get_active_partner_membership(partner: Partner, user: Optional[User]) -> Optional[PartnerMembership]:
    if not user or user.is_anonymous:
        return None
    return PartnerMembership.objects.filter(
        partner=partner,
        user=user,
        status__in=(PartnerMembershipStatus.MEMBER, PartnerMembershipStatus.SUBSCRIBER),
        is_banned=False,
    ).first()


def partner_user_can_access(partner: Partner, user: Optional[User]) -> bool:
    if not user or user.is_anonymous:
        return False
    if partner.owner_id == user.id:
        return True
    if _get_active_partner_membership(partner, user):
        return True
    # Legacy fallback while older partner records are migrated to PartnerMembership.
    return _get_member_conversation_role(partner, user) is not None


def partner_user_can_manage(partner: Partner, user: Optional[User]) -> bool:
    if not user or user.is_anonymous:
        return False
    if partner.owner_id == user.id:
        return True
    base_role = _get_member_conversation_role(partner, user)
    if base_role in (BaseConversationRole.OWNER, BaseConversationRole.ADMIN):
        return True
    membership = _get_active_partner_membership(partner, user)
    if membership and membership.role in ("owner", "admin", "manager"):
        return True
    return False


def partner_ids_user_can_access(user: Optional[User]) -> list:
    """Bulk id-set counterpart to partner_user_can_access, for shaping a
    'my institutions' listing queryset (e.g. filtering Shop/HealthInstitution/
    EducationInstitution by partner_id__in=...) without an N+1 per-row
    permission check. Deliberately does NOT include the legacy
    conversation-role fallback that partner_user_can_access falls back to —
    that path only matters for older partner records pre-dating
    PartnerMembership, and this helper only shapes what shows up in a list,
    not the authoritative permission check (callers still enforce the real
    per-object partner_user_can_access/partner_user_can_manage before
    allowing a mutation)."""
    if not user or getattr(user, "is_anonymous", False):
        return []
    owned = Partner.objects.filter(owner=user).values_list("id", flat=True)
    member = PartnerMembership.objects.filter(
        user=user,
        status__in=(PartnerMembershipStatus.MEMBER, PartnerMembershipStatus.SUBSCRIBER),
        is_banned=False,
    ).values_list("partner_id", flat=True)
    return list(set(owned) | set(member))


def partner_ids_user_can_manage(user: Optional[User]) -> list:
    """Bulk id-set counterpart to partner_user_can_manage — see
    partner_ids_user_can_access's docstring for the same caveat about the
    legacy conversation-role fallback being intentionally excluded here."""
    if not user or getattr(user, "is_anonymous", False):
        return []
    owned = Partner.objects.filter(owner=user).values_list("id", flat=True)
    managed = PartnerMembership.objects.filter(
        user=user,
        status__in=(PartnerMembershipStatus.MEMBER, PartnerMembershipStatus.SUBSCRIBER),
        is_banned=False,
        role__in=("owner", "admin", "manager"),
    ).values_list("partner_id", flat=True)
    return list(set(owned) | set(managed))


def get_partner_user_roles(partner: Partner, user: Optional[User]) -> Set[str]:
    roles: Set[str] = set()
    if not user or user.is_anonymous:
        return roles
    if partner.owner_id == user.id:
        roles.add("owner")
    base_role = _get_member_conversation_role(partner, user)
    if base_role == BaseConversationRole.ADMIN:
        roles.add("admin")
    elif base_role == BaseConversationRole.OWNER:
        roles.add("owner")
    membership = _get_active_partner_membership(partner, user)
    if membership and membership.role:
        roles.add(membership.role)
    if not roles:
        roles.add("member")
    elif "owner" not in roles and "admin" not in roles and "manager" not in roles:
        roles.add("member")
    return roles


PARTNER_CHANNEL_PERMISSION_VIEW = PartnerChannelPermissionOverwrite.PermissionCode.VIEW_CHANNEL
PARTNER_CHANNEL_PERMISSION_SEND = PartnerChannelPermissionOverwrite.PermissionCode.SEND_MESSAGES
PARTNER_CHANNEL_PERMISSION_MANAGE = PartnerChannelPermissionOverwrite.PermissionCode.MANAGE_CHANNEL
PARTNER_CHANNEL_PERMISSION_MENTION_EVERYONE = PartnerChannelPermissionOverwrite.PermissionCode.MENTION_EVERYONE
PARTNER_CHANNEL_PERMISSION_CODES = {
    PARTNER_CHANNEL_PERMISSION_VIEW,
    PARTNER_CHANNEL_PERMISSION_SEND,
    PARTNER_CHANNEL_PERMISSION_MANAGE,
    PARTNER_CHANNEL_PERMISSION_MENTION_EVERYONE,
}


def _normalized_role_names(partner: Partner, user: Optional[User], channel=None) -> Set[str]:
    role_names = {role_name.strip().lower() for role_name in get_partner_user_roles(partner, user) if role_name}
    if not user or user.is_anonymous:
        return role_names
    assignments = PartnerRoleAssignment.objects.select_related("role").filter(
        partner=partner,
        user=user,
    )
    if channel is not None:
        assignments = assignments.filter(
            models.Q(scope_type=PartnerRoleAssignment.SCOPE_GLOBAL)
            | models.Q(
                scope_type=PartnerRoleAssignment.SCOPE_CHANNEL,
                scope_id=str(channel.id),
            )
        )
    for assignment in assignments:
        if assignment.role_id:
            role_names.add(assignment.role.name.strip().lower())
    return role_names


def get_partner_user_channel_base_permissions(partner: Partner, user: Optional[User], channel=None) -> set[str]:
    permissions: set[str] = set()
    if not user or user.is_anonymous:
        return permissions
    if partner.owner_id == user.id:
        return set(PARTNER_CHANNEL_PERMISSION_CODES)
    if not partner_user_can_access(partner, user):
        return permissions

    permissions.add(PARTNER_CHANNEL_PERMISSION_VIEW)

    membership = _get_active_partner_membership(partner, user)
    if membership and membership.status == PartnerMembershipStatus.MEMBER:
        muted_until = membership.muted_until
        timed_out_until = membership.timed_out_until
        is_temporarily_blocked = bool(
            (muted_until and muted_until > timezone.now())
            or (timed_out_until and timed_out_until > timezone.now())
            or membership.is_muted
        )
        if not is_temporarily_blocked:
            permissions.add(PARTNER_CHANNEL_PERMISSION_SEND)

    if partner_user_can_manage(partner, user):
        permissions.update(PARTNER_CHANNEL_PERMISSION_CODES)

    assignments = PartnerRoleAssignment.objects.select_related("role").filter(
        partner=partner,
        user=user,
    )
    if channel is not None:
        assignments = assignments.filter(
            models.Q(scope_type=PartnerRoleAssignment.SCOPE_GLOBAL)
            | models.Q(
                scope_type=PartnerRoleAssignment.SCOPE_CHANNEL,
                scope_id=str(channel.id),
            )
        )
    for assignment in assignments:
        for permission_codename in _role_permission_set(assignment.role):
            if permission_codename in PARTNER_CHANNEL_PERMISSION_CODES:
                permissions.add(permission_codename)

    if channel is not None and channel.category_id and getattr(channel.category, "is_private", False):
        permissions.discard(PARTNER_CHANNEL_PERMISSION_VIEW)
        permissions.discard(PARTNER_CHANNEL_PERMISSION_SEND)
        permissions.discard(PARTNER_CHANNEL_PERMISSION_MANAGE)

    return permissions


def resolve_partner_channel_permissions(channel, user: Optional[User]) -> set[str]:
    partner = getattr(channel, "partner", None)
    if partner is None:
        return set()
    if not user or user.is_anonymous:
        return set()
    if partner.owner_id == user.id:
        return set(PARTNER_CHANNEL_PERMISSION_CODES)

    permissions = get_partner_user_channel_base_permissions(partner, user, channel=channel)
    role_names = _normalized_role_names(partner, user, channel=channel)

    overwrites = channel.permission_overwrites.select_related("role", "user").all()
    role_allows: set[str] = set()
    role_denies: set[str] = set()
    member_allows: set[str] = set()
    member_denies: set[str] = set()

    for overwrite in overwrites:
        allow_permissions = {
            code for code in (overwrite.allow_permissions or []) if code in PARTNER_CHANNEL_PERMISSION_CODES
        }
        deny_permissions = {
            code for code in (overwrite.deny_permissions or []) if code in PARTNER_CHANNEL_PERMISSION_CODES
        }
        if overwrite.subject_type == PartnerChannelPermissionOverwrite.SubjectType.ROLE:
            role = getattr(overwrite, "role", None)
            if role and role.name.strip().lower() in role_names:
                role_allows.update(allow_permissions)
                role_denies.update(deny_permissions)
        elif overwrite.subject_type == PartnerChannelPermissionOverwrite.SubjectType.MEMBER:
            if overwrite.user_id == user.id:
                member_allows.update(allow_permissions)
                member_denies.update(deny_permissions)

    permissions.update(role_allows)
    permissions.difference_update(role_denies)
    permissions.update(member_allows)
    permissions.difference_update(member_denies)

    if PARTNER_CHANNEL_PERMISSION_VIEW not in permissions:
        permissions.discard(PARTNER_CHANNEL_PERMISSION_SEND)
        permissions.discard(PARTNER_CHANNEL_PERMISSION_MANAGE)
    if PARTNER_CHANNEL_PERMISSION_MANAGE in permissions:
        permissions.update({PARTNER_CHANNEL_PERMISSION_VIEW, PARTNER_CHANNEL_PERMISSION_SEND})

    return permissions


def partner_user_can_view_channel(channel, user: Optional[User]) -> bool:
    if not getattr(channel, "partner_id", None):
        return True
    return PARTNER_CHANNEL_PERMISSION_VIEW in resolve_partner_channel_permissions(channel, user)


def partner_user_can_send_channel(channel, user: Optional[User]) -> bool:
    if not getattr(channel, "partner_id", None):
        return True
    return PARTNER_CHANNEL_PERMISSION_SEND in resolve_partner_channel_permissions(channel, user)


def partner_user_can_mention_everyone(channel, user: Optional[User]) -> bool:
    if not getattr(channel, "partner_id", None):
        return True
    return PARTNER_CHANNEL_PERMISSION_MENTION_EVERYONE in resolve_partner_channel_permissions(channel, user)


def partner_user_can_manage_channel(channel, user: Optional[User]) -> bool:
    if not getattr(channel, "partner_id", None):
        return getattr(channel, "owner_id", None) == getattr(user, "id", None)
    if getattr(channel, "owner_id", None) == getattr(user, "id", None):
        return True
    return PARTNER_CHANNEL_PERMISSION_MANAGE in resolve_partner_channel_permissions(channel, user)


def filter_partner_channels_for_user(channels, user: Optional[User]) -> list:
    visible_channels = []
    for channel in channels:
        if not getattr(channel, "partner_id", None) or partner_user_can_view_channel(channel, user):
            visible_channels.append(channel)
    return visible_channels


def build_partner_discord_summary(partner: Partner, user: Optional[User]) -> dict:
    """
    Fast, compatibility-safe partner workspace summary for Discord-style clients.

    This intentionally reads existing Partner/Channel/Conversation state instead
    of introducing a parallel server model.
    """
    from apps.channels.models import Channel

    roles = sorted(get_partner_user_roles(partner, user))
    can_manage = partner_user_can_manage(partner, user)
    membership = _get_active_partner_membership(partner, user)
    policy = ensure_partner_policy(partner)
    settings = policy.settings or {}
    visible_apps = get_partner_organization_apps_for_user(partner, user)

    all_channels = list(
        Channel.objects.filter(partner=partner, is_archived=False)
        .select_related("conversation", "category", "owner")
        .prefetch_related("permission_overwrites__role", "permission_overwrites__user")
        .order_by("category__order", "category__name", "order", "name")
    )
    visible_channels = filter_partner_channels_for_user(all_channels, user)
    channel_ids = [channel.id for channel in visible_channels]
    conversation_ids = [channel.conversation_id for channel in visible_channels if channel.conversation_id]
    if partner.main_conversation_id:
        conversation_ids.append(partner.main_conversation_id)

    unread_total = 0
    unread_by_conversation: dict[str, int] = {}
    if user and getattr(user, "is_authenticated", False) and conversation_ids:
        members = (
            ConversationMember.objects.filter(
                user=user,
                conversation_id__in=conversation_ids,
                left_at__isnull=True,
                is_hidden=False,
            )
            .select_related("conversation")
        )
        for member in members:
            last_seq = int(getattr(member.conversation, "last_message_seq", 0) or 0)
            read_seq = int(getattr(member, "last_read_seq", 0) or 0)
            unread = max(last_seq - read_seq, 0)
            unread_total += unread
            unread_by_conversation[str(member.conversation_id)] = unread

    active_members = PartnerMembership.objects.filter(
        partner=partner,
        status__in=(PartnerMembershipStatus.MEMBER, PartnerMembershipStatus.SUBSCRIBER),
        is_banned=False,
    ).count()
    pending_members = PartnerMembership.objects.filter(
        partner=partner,
        status__in=(PartnerMembershipStatus.PENDING, PartnerMembershipStatus.APPLICANT),
        is_banned=False,
    ).count()
    open_applications = PartnerApplication.objects.filter(
        partner=partner,
        status="pending",
    ).count()
    open_moderation = PartnerModerationAction.objects.filter(
        partner=partner,
        revoked_at__isnull=True,
    ).count()

    permission_codes: set[str] = set()
    for assignment in PartnerRoleAssignment.objects.filter(partner=partner, user=user).select_related("role"):
        permission_codes.update(_role_permission_set(assignment.role))
    if can_manage:
        permission_codes.update(
            {
                "partner.roles.manage",
                "partner.audit.view",
                "partner.settings.manage",
                "partner.access.manage",
            }
        )

    channel_previews = []
    for channel in visible_channels[:20]:
        permissions = resolve_partner_channel_permissions(channel, user)
        channel_previews.append(
            {
                "id": str(channel.id),
                "name": channel.name,
                "type": channel.channel_type,
                "category_id": str(channel.category_id) if channel.category_id else None,
                "conversation_id": str(channel.conversation_id),
                "can_send": PARTNER_CHANNEL_PERMISSION_SEND in permissions,
                "can_manage": PARTNER_CHANNEL_PERMISSION_MANAGE in permissions,
                "unread_count": unread_by_conversation.get(str(channel.conversation_id), 0),
            }
        )

    return {
        "partner_id": str(partner.id),
        "workspace": {
            "name": partner.name,
            "slug": partner.slug,
            "is_active": partner.is_active,
            "main_conversation_id": str(partner.main_conversation_id) if partner.main_conversation_id else None,
        },
        "membership": {
            "status": getattr(membership, "status", None),
            "role": getattr(membership, "role", None),
            "roles": roles,
            "can_manage": can_manage,
            "permissions": sorted(permission_codes),
            "is_muted": bool(getattr(membership, "is_muted", False)),
            "is_banned": bool(getattr(membership, "is_banned", False)),
            "timed_out_until": membership.timed_out_until.isoformat() if membership and membership.timed_out_until else None,
        },
        "counts": {
            "active_members": active_members,
            "pending_members": pending_members,
            "categories": partner.server_categories.count(),
            "visible_channels": len(visible_channels),
            "total_channels": len(all_channels),
            "roles": partner.roles.count(),
            "apps": len(visible_apps),
            "open_applications": open_applications,
            "open_moderation_actions": open_moderation,
            "unread_messages": unread_total,
        },
        "channels": channel_previews,
        "readiness": {
            "roles_ready": partner.roles.exists(),
            "channels_ready": bool(channel_ids),
            "onboarding_ready": hasattr(partner, "join_config"),
            "audit_ready": True,
            "moderation_ready": True,
            "apps_ready": bool(visible_apps),
            "low_bandwidth_ready": True,
            "family_safe_media": True,
            "legacy_wallet_disabled": True,
        },
        "safety": {
            "media_gate": "enabled",
            "explicit_content_provider_live_calls": False,
            "quarantine_supported": True,
            "policy": {
                "audit_enabled": bool((settings.get("compliance") or {}).get("audit_enabled", True)),
                "dlp_enabled": bool((settings.get("dlp") or {}).get("enabled", False)),
                "message_retention_days": (settings.get("retention") or {}).get("message_retention_days"),
                "uploads_per_hour": (settings.get("rate_limits") or {}).get("uploads_per_hour"),
            },
        },
        "next_actions": [
            {"key": "create_channel", "label": "Create a channel", "enabled": can_manage},
            {"key": "invite_members", "label": "Invite members", "enabled": can_manage},
            {"key": "review_applications", "label": "Review applications", "enabled": can_manage and open_applications > 0},
            {"key": "open_moderation", "label": "Review moderation queue", "enabled": can_manage and open_moderation > 0},
        ],
    }


def user_can_manage_organization_apps(partner: Partner, user: Optional[User]) -> bool:
    return partner_user_can_manage(partner, user)


def get_partner_organization_apps_for_user(partner: Partner, user: Optional[User]) -> list[PartnerOrganizationApp]:
    roles = get_partner_user_roles(partner, user)
    can_manage = user_can_manage_organization_apps(partner, user)
    apps_qs = partner.organization_apps.order_by("group", "order", "name")
    visible_apps: list[PartnerOrganizationApp] = []
    for app in apps_qs:
        visible_roles = set(app.visible_to or [])
        if can_manage or visible_roles.intersection(roles):
            visible_apps.append(app)
    return visible_apps


DEACTIVATION_GRACE_DAYS = 60


def _grace_deadline(days: int = DEACTIVATION_GRACE_DAYS):
    return timezone.now() + timedelta(days=days)


def deactivate_partner_profile(
    partner: Partner,
    by_user: Optional[User],
    *,
    source: str = Partner.DeactivationSource.SYSTEM,
    grace_days: int = DEACTIVATION_GRACE_DAYS,
) -> Partner:
    """Mark the partner as deactivated and record who/why, with a grace window."""
    now = timezone.now()
    partner.is_active = False
    partner.deactivated_at = now
    partner.deactivated_by = by_user
    partner.deactivation_source = source
    partner.grace_expires_at = _grace_deadline(grace_days) if grace_days else None
    partner.save(
        update_fields=[
            "is_active",
            "deactivated_at",
            "deactivated_by",
            "deactivation_source",
            "grace_expires_at",
            "updated_at",
        ]
    )
    return partner


def reactivate_partner_profile(partner: Partner) -> Partner:
    """Reactivate partners that were deactivated by the user or the system (via lifecycle hooks)."""
    partner.is_active = True
    partner.deactivated_at = None
    partner.deactivated_by = None
    partner.deactivation_source = None
    partner.grace_expires_at = None
    partner.save(
        update_fields=[
            "is_active",
            "deactivated_at",
            "deactivated_by",
            "deactivation_source",
            "grace_expires_at",
            "updated_at",
        ]
    )
    return partner


def cleanup_expired_system_partner_profiles(user: User) -> None:
    expired = Partner.objects.filter(
        owner=user,
        is_active=False,
        deactivation_source=Partner.DeactivationSource.SYSTEM,
        grace_expires_at__lt=timezone.now(),
    )
    if expired.exists():
        expired.delete()


def ensure_partner_profiles_for_user(user: User, tier_name: Optional[str]) -> None:
    """
    Runs the partner-profile policy when a user changes tiers.
    Uses the tier's `partner_accounts` feature limit:
      - 0/disabled => deactivate all active partner profiles
      - finite int => keep up to that many active profiles
      - unlimited => reactivate all system-deactivated profiles
    """
    if not user:
        return
    tier_obj = AccountTier.objects.filter(name__iexact=(tier_name or "").strip()).first()
    features = get_aggregated_tier_features(tier_obj) if tier_obj else {}
    partner_limit = normalize_limit_value(features.get("partner_accounts"), default=0)
    cleanup_expired_system_partner_profiles(user)
    partners = Partner.objects.filter(owner=user).order_by("created_at", "id")
    if not partners.exists():
        return

    if partner_limit is None:
        system_deactivated = partners.filter(
            is_active=False,
            deactivation_source=Partner.DeactivationSource.SYSTEM,
        )
        for partner in system_deactivated:
            reactivate_partner_profile(partner)
        return

    if partner_limit <= 0:
        for partner in partners.filter(is_active=True):
            deactivate_partner_profile(partner, by_user=None, source=Partner.DeactivationSource.SYSTEM)
        return

    active_partners = list(partners.filter(is_active=True).order_by("created_at", "id"))
    if len(active_partners) > partner_limit:
        for partner in active_partners[partner_limit:]:
            deactivate_partner_profile(partner, by_user=None, source=Partner.DeactivationSource.SYSTEM)
        active_partners = active_partners[:partner_limit]

    if len(active_partners) >= partner_limit:
        return

    slots_left = partner_limit - len(active_partners)
    system_deactivated = list(
        partners.filter(
            is_active=False,
            deactivation_source=Partner.DeactivationSource.SYSTEM,
        ).order_by("created_at", "id")[:slots_left]
    )
    for partner in system_deactivated:
        reactivate_partner_profile(partner)


def _match_patterns(text: str, patterns: list[str]) -> list[str]:
    hits = []
    lowered = text.lower()
    for pattern in patterns:
        pat = str(pattern).strip()
        if not pat:
            continue
        if pat.startswith("/") and pat.endswith("/") and len(pat) > 2:
            try:
                import re

                if re.search(pat[1:-1], text, flags=re.IGNORECASE):
                    hits.append(pat)
            except re.error:
                continue
        elif pat.lower() in lowered:
            hits.append(pat)
    return hits


def evaluate_partner_dlp(partner: Partner, text: str) -> dict:
    policy = ensure_partner_policy(partner)
    dlp = (policy.settings or {}).get("dlp", {})
    if not dlp.get("enabled"):
        return {"blocked": [], "warn": []}
    block_hits = _match_patterns(text or "", dlp.get("block_patterns") or [])
    warn_hits = _match_patterns(text or "", dlp.get("warn_patterns") or [])
    return {"blocked": block_hits, "warn": warn_hits}


def dispatch_partner_webhooks(
    *,
    partner: Partner,
    event: str,
    payload: dict,
) -> int:
    hooks = PartnerWebhook.objects.filter(partner=partner, is_active=True)
    delivered = 0
    for hook in hooks:
        delivery = PartnerWebhookDelivery.objects.create(
            webhook=hook,
            event=event,
            payload=payload or {},
            status=PartnerWebhookDeliveryStatus.PENDING,
        )
        events = hook.events or []
        if events and event not in events and "all" not in events and "*" not in events:
            delivery.status = PartnerWebhookDeliveryStatus.FAILED
            delivery.error_message = "Event not subscribed."
            delivery.save(update_fields=["status", "error_message", "updated_at"])
            continue
        body = {
            "event": event,
            "partner_id": str(partner.id),
            "payload": payload or {},
        }
        body_bytes = json.dumps(body).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-KIS-Event": event,
            "X-KIS-Partner-Id": str(partner.id),
        }
        if hook.secret:
            signature = hmac.new(
                hook.secret.encode("utf-8"),
                body_bytes,
                hashlib.sha256,
            ).hexdigest()
            headers["X-KIS-Signature"] = signature
        request = urllib.request.Request(
            hook.url,
            data=body_bytes,
            headers=headers,
            method="POST",
        )
        try:
            delivery.attempt_count += 1
            with urllib.request.urlopen(request, timeout=6) as resp:
                if 200 <= resp.status < 300:
                    delivered += 1
                    hook.last_sent_at = timezone.now()
                    hook.last_error = ""
                    delivery.status = PartnerWebhookDeliveryStatus.DELIVERED
                    delivery.response_code = resp.status
                else:
                    hook.last_error = f"HTTP {resp.status}"
                    delivery.status = PartnerWebhookDeliveryStatus.FAILED
                    delivery.response_code = resp.status
                    delivery.error_message = hook.last_error
        except Exception as exc:
            hook.last_error = str(exc)[:500]
            delivery.status = PartnerWebhookDeliveryStatus.FAILED
            delivery.error_message = hook.last_error
        if delivery.status == PartnerWebhookDeliveryStatus.FAILED and delivery.attempt_count <= hook.retry_limit:
            delivery.next_retry_at = timezone.now() + timedelta(
                seconds=hook.retry_backoff_seconds,
            )
        hook.save(update_fields=["last_sent_at", "last_error", "updated_at"])
        delivery.save(
            update_fields=[
                "status",
                "attempt_count",
                "next_retry_at",
                "response_code",
                "error_message",
                "updated_at",
            ],
        )
    return delivered


def retry_partner_webhook_delivery(delivery: PartnerWebhookDelivery) -> bool:
    hook = delivery.webhook
    if not hook.is_active:
        delivery.status = PartnerWebhookDeliveryStatus.FAILED
        delivery.error_message = "Webhook inactive."
        delivery.save(update_fields=["status", "error_message", "updated_at"])
        return False
    body = {
        "event": delivery.event,
        "partner_id": str(hook.partner_id),
        "payload": delivery.payload or {},
    }
    body_bytes = json.dumps(body).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-KIS-Event": delivery.event,
        "X-KIS-Partner-Id": str(hook.partner_id),
    }
    if hook.secret:
        signature = hmac.new(
            hook.secret.encode("utf-8"),
            body_bytes,
            hashlib.sha256,
        ).hexdigest()
        headers["X-KIS-Signature"] = signature
    request = urllib.request.Request(
        hook.url,
        data=body_bytes,
        headers=headers,
        method="POST",
    )
    delivery.attempt_count += 1
    try:
        with urllib.request.urlopen(request, timeout=6) as resp:
            if 200 <= resp.status < 300:
                delivery.status = PartnerWebhookDeliveryStatus.DELIVERED
                delivery.response_code = resp.status
                delivery.error_message = ""
                hook.last_sent_at = timezone.now()
                hook.last_error = ""
            else:
                delivery.status = PartnerWebhookDeliveryStatus.FAILED
                delivery.response_code = resp.status
                delivery.error_message = f"HTTP {resp.status}"
                hook.last_error = delivery.error_message
    except Exception as exc:
        delivery.status = PartnerWebhookDeliveryStatus.FAILED
        delivery.error_message = str(exc)[:500]
        hook.last_error = delivery.error_message

    if delivery.status == PartnerWebhookDeliveryStatus.FAILED and delivery.attempt_count <= hook.retry_limit:
        delivery.next_retry_at = timezone.now() + timedelta(
            seconds=hook.retry_backoff_seconds,
        )
    else:
        delivery.next_retry_at = None
    hook.save(update_fields=["last_sent_at", "last_error", "updated_at"])
    delivery.save(
        update_fields=[
            "status",
            "attempt_count",
            "next_retry_at",
            "response_code",
            "error_message",
            "updated_at",
        ],
    )
    return delivery.status == PartnerWebhookDeliveryStatus.DELIVERED


def _automation_condition_matches(conditions: dict, payload: dict) -> bool:
    if not conditions:
        return True
    if "all" in conditions:
        return all(_evaluate_condition(cond, payload) for cond in (conditions.get("all") or []))
    if "any" in conditions:
        return any(_evaluate_condition(cond, payload) for cond in (conditions.get("any") or []))
    for key, value in conditions.items():
        if payload.get(key) != value:
            return False
    return True


def _evaluate_condition(condition: dict, payload: dict) -> bool:
    field = condition.get("field")
    if not field:
        return True
    value = payload.get(field)
    op = (condition.get("op") or "eq").lower()
    expected = condition.get("value")
    if op == "eq":
        return value == expected
    if op == "in":
        return value in (expected or [])
    if op == "contains":
        return str(expected or "").lower() in str(value or "").lower()
    return False


def run_partner_automation_rules(
    *,
    partner: Partner,
    event: str,
    payload: dict,
    actor: Optional[User] = None,
) -> int:
    rules = PartnerAutomationRule.objects.filter(
        partner=partner,
        is_active=True,
        trigger=event,
    )
    ran = 0
    for rule in rules:
        if not _automation_condition_matches(rule.conditions or {}, payload or {}):
            continue
        ran += 1
        last_status = "success"
        last_message = ""
        try:
            _apply_automation_actions(rule, partner, payload or {}, actor)
        except Exception as exc:
            last_status = "failed"
            last_message = str(exc)[:300]
        rule.last_run_at = timezone.now()
        rule.last_run_status = last_status
        rule.last_run_message = last_message
        rule.save(update_fields=["last_run_at", "last_run_status", "last_run_message", "updated_at"])
    return ran


def _apply_automation_actions(
    rule: PartnerAutomationRule,
    partner: Partner,
    payload: dict,
    actor: Optional[User],
) -> None:
    actions = rule.actions or []
    for action in actions:
        action_type = (action.get("type") or "").strip()
        params = action.get("params") or {}
        if action_type == "assign_role":
            role_id = params.get("role_id")
            user_id = params.get("user_id") or payload.get("user_id")
            if role_id and user_id:
                PartnerRoleAssignment.objects.update_or_create(
                    partner=partner,
                    user_id=user_id,
                    role_id=role_id,
                )
                log_partner_audit(
                    partner=partner,
                    actor=actor,
                    action="partner.automation.assign_role",
                    target_type="partner_role",
                    target_id=str(role_id),
                    metadata={"user_id": str(user_id), "rule_id": str(rule.id)},
                )
        elif action_type == "remove_role":
            role_id = params.get("role_id")
            user_id = params.get("user_id") or payload.get("user_id")
            if role_id and user_id:
                PartnerRoleAssignment.objects.filter(
                    partner=partner,
                    user_id=user_id,
                    role_id=role_id,
                ).delete()
                log_partner_audit(
                    partner=partner,
                    actor=actor,
                    action="partner.automation.remove_role",
                    target_type="partner_role",
                    target_id=str(role_id),
                    metadata={"user_id": str(user_id), "rule_id": str(rule.id)},
                )
        elif action_type == "set_feature_flag":
            key = params.get("key")
            enabled = bool(params.get("enabled", True))
            if key:
                PartnerFeatureFlag.objects.update_or_create(
                    partner=partner,
                    key=key,
                    defaults={"is_enabled": enabled},
                )
                log_partner_audit(
                    partner=partner,
                    actor=actor,
                    action="partner.automation.feature_flag",
                    target_type="partner_feature_flag",
                    target_id=str(key),
                    metadata={"enabled": enabled, "rule_id": str(rule.id)},
                )
        elif action_type == "dispatch_webhook":
            event = params.get("event") or "automation.triggered"
            dispatch_partner_webhooks(
                partner=partner,
                event=event,
                payload={**payload, "rule_id": str(rule.id)},
            )
        elif action_type == "log_audit":
            log_partner_audit(
                partner=partner,
                actor=actor,
                action=params.get("action") or "partner.automation.run",
                target_type=params.get("target_type") or "partner_automation_rule",
                target_id=str(rule.id),
                metadata={"rule_id": str(rule.id), **(params.get("metadata") or {})},
            )


def build_partner_summary(partner: Partner) -> dict:
    def _series_counts(queryset, date_field: str, start_date):
        items = (
            queryset.filter(**{f"{date_field}__date__gte": start_date})
            .annotate(day=TruncDate(date_field))
            .values("day")
            .annotate(total=Count("id"))
            .order_by("day")
        )
        return {item["day"]: item["total"] for item in items}

    def _build_series(days: int):
        today = timezone.now().date()
        start_date = today - timedelta(days=days - 1)
        posts = _series_counts(
            PartnerPost.objects.filter(partner=partner, is_deleted=False),
            "created_at",
            start_date,
        )
        comments = _series_counts(
            PartnerPostComment.objects.filter(post__partner=partner, is_deleted=False),
            "created_at",
            start_date,
        )
        reactions = _series_counts(
            PartnerPostReaction.objects.filter(post__partner=partner),
            "created_at",
            start_date,
        )
        applications = _series_counts(
            PartnerApplication.objects.filter(partner=partner),
            "created_at",
            start_date,
        )
        new_members = _series_counts(
            PartnerMembership.objects.filter(partner=partner),
            "created_at",
            start_date,
        )
        audit_events = _series_counts(
            PartnerAuditEvent.objects.filter(partner=partner),
            "created_at",
            start_date,
        )
        series = []
        for idx in range(days):
            day = start_date + timedelta(days=idx)
            series.append(
                {
                    "date": day.isoformat(),
                    "posts": posts.get(day, 0),
                    "comments": comments.get(day, 0),
                    "reactions": reactions.get(day, 0),
                    "applications": applications.get(day, 0),
                    "new_members": new_members.get(day, 0),
                    "audit_events": audit_events.get(day, 0),
                }
            )
        return series

    activity_series = _build_series(days=18)
    last_7 = activity_series[-7:] if activity_series else []
    last_30 = _build_series(days=30)
    last_7_posts = sum(item["posts"] for item in last_7)
    last_7_engagement = sum(item["comments"] + item["reactions"] for item in last_7)
    engagement_rate = (
        round(last_7_engagement / max(last_7_posts, 1), 2) if last_7 else 0
    )
    summary = {
        "partner_id": str(partner.id),
        "members_total": PartnerMembership.objects.filter(partner=partner).count(),
        "members_by_status": {
            status: PartnerMembership.objects.filter(partner=partner, status=status).count()
            for status in PartnerMembershipStatus.values
        },
        "roles": PartnerRole.objects.filter(partner=partner).count(),
        "role_assignments": PartnerRoleAssignment.objects.filter(partner=partner).count(),
        "automation_rules": PartnerAutomationRule.objects.filter(partner=partner).count(),
        "audit_events": PartnerAuditEvent.objects.filter(partner=partner).count(),
        "job_posts": partner.job_posts.count(),
        "applications": PartnerApplication.objects.filter(partner=partner).count(),
        "posts": PartnerPost.objects.filter(partner=partner, is_deleted=False).count(),
        "post_comments": PartnerPostComment.objects.filter(post__partner=partner, is_deleted=False).count(),
        "post_reactions": PartnerPostReaction.objects.filter(post__partner=partner).count(),
        "integrations": partner.integrations.count(),
        "webhooks": partner.webhooks.count(),
        "activity_series": activity_series,
        "engagement_rate": engagement_rate,
        "activity_summary": {
            "last_7_days": {
                "posts": last_7_posts,
                "comments": sum(item["comments"] for item in last_7),
                "reactions": sum(item["reactions"] for item in last_7),
                "applications": sum(item["applications"] for item in last_7),
                "new_members": sum(item["new_members"] for item in last_7),
                "audit_events": sum(item["audit_events"] for item in last_7),
            },
            "last_30_days": {
                "posts": sum(item["posts"] for item in last_30),
                "comments": sum(item["comments"] for item in last_30),
                "reactions": sum(item["reactions"] for item in last_30),
                "applications": sum(item["applications"] for item in last_30),
                "new_members": sum(item["new_members"] for item in last_30),
                "audit_events": sum(item["audit_events"] for item in last_30),
            },
        },
    }
    try:
        from apps.communities.models import Community
        summary["communities"] = Community.objects.filter(partner=partner).count()
    except Exception:
        summary["communities"] = 0
    try:
        from apps.groups.models import Group
        summary["groups"] = Group.objects.filter(partner=partner).count()
    except Exception:
        summary["groups"] = 0
    try:
        from apps.channels.models import Channel
        summary["channels"] = Channel.objects.filter(partner=partner).count()
    except Exception:
        summary["channels"] = 0
    return summary


def next_export_run_at(frequency: str) -> timezone.datetime:
    now = timezone.now()
    if frequency == PartnerExportScheduleFrequency.DAILY:
        return now + timedelta(days=1)
    if frequency == PartnerExportScheduleFrequency.MONTHLY:
        return now + timedelta(days=30)
    return now + timedelta(days=7)


def _role_permission_set(role: PartnerRole) -> set[str]:
    perms = set(role.permissions or [])
    visited = set()
    node = role
    while node and node.id not in visited:
        visited.add(node.id)
        if node.permissions:
            perms.update(node.permissions)
        node = node.parent_role
    return perms


def user_has_partner_permission(partner: Partner, user: User, permission_codename: str) -> bool:
    if partner.owner_id == user.id:
        return True
    if partner.main_conversation_id:
        base_role = partner.main_conversation.memberships.filter(
            user=user,
            left_at__isnull=True,
        ).values_list("base_role", flat=True).first()
        if base_role in (BaseConversationRole.OWNER, BaseConversationRole.ADMIN):
            return True

    assignments = PartnerRoleAssignment.objects.select_related("role").filter(
        partner=partner,
        user=user,
    )
    for assignment in assignments:
        if permission_codename in _role_permission_set(assignment.role):
            return True
    return False


def log_partner_audit(
    *,
    partner: Partner,
    actor: User | None,
    action: str,
    target_type: str = "",
    target_id: str = "",
    metadata: Optional[dict] = None,
    request=None,
) -> PartnerAuditEvent:
    ip_address = None
    user_agent = ""
    if request:
        ip_address = getattr(request, "META", {}).get("REMOTE_ADDR")
        user_agent = getattr(request, "META", {}).get("HTTP_USER_AGENT", "")
    return PartnerAuditEvent.objects.create(
        partner=partner,
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        ip_address=ip_address,
        user_agent=user_agent[:255],
        metadata=metadata or {},
    )
