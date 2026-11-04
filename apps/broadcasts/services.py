from django.utils import timezone

from apps.broadcasts.models import BroadcastChannel, BroadcastChannelRole, BroadcastItem


def resolve_channel_notification_recipients(channel: BroadcastChannel) -> list:
    """Who should be notified about engagement (comments/reactions) on this
    channel's content.

    Individual-owned channels (owner_type=USER) have a real owner_user_id -
    notify them directly. Organization-owned channels (shop/health/
    education/partner) have owner_user=None by design (see BroadcastChannel.
    owner_type), so there is no single user to fall back to; instead this
    notifies every BroadcastChannelRole holder with an OWNER or MANAGER
    role on the channel - the roles responsible for its day-to-day content.
    EDITOR/MODERATOR/ANALYST are deliberately excluded: they manage the
    channel operationally but aren't necessarily who wants a push
    notification for every comment or reaction.

    Returns a list of user ids (possibly empty, e.g. an org channel with no
    role holders yet - callers already no-op safely on an empty/None
    recipient per-call via notify_engagement).
    """
    if channel.owner_user_id:
        return [channel.owner_user_id]
    return list(
        BroadcastChannelRole.objects
        .filter(
            channel_id=channel.id,
            role__in=[BroadcastChannelRole.Role.OWNER, BroadcastChannelRole.Role.MANAGER],
        )
        .values_list("user_id", flat=True)
        .distinct()
    )


def cleanup_expired_broadcast_items() -> int:
    """
    Remove broadcast items whose expiration date has passed.
    """
    now = timezone.now()
    expired_qs = BroadcastItem.objects.filter(expires_at__lte=now)
    expired_count = expired_qs.count()
    if expired_count:
        expired_qs.delete()
    return expired_count


# ---------------------------------------------------------------------
# KIS Bot Platform (Phase 2 gap-closing) — event publishers for
# Channels (post.created, member.joined on free-tier activation) and
# My Feed (post.created). Plain Django calls, never touching Nest.
# Never raise: a publisher failure must never break the real post/join
# action that triggered it.
# ---------------------------------------------------------------------

def dispatch_channel_post_created(content) -> None:
    try:
        from apps.bots.models import BotPermissionScope, BotResourceType
        from apps.bots.services import dispatch_resource_event
        from django.utils import timezone as _tz

        dispatch_resource_event(
            BotResourceType.CHANNEL, content.channel_id, "post.created",
            {
                "post_id": str(content.id), "author_id": str(content.created_by_id or ""),
                "title": getattr(content, "title", "") or "", "text": getattr(content, "text_plain", "") or "",
            },
            event_id=f"post.created:{content.id}:{_tz.now().timestamp()}",
            required_scope=BotPermissionScope.READ_MESSAGES,
        )
    except Exception:  # noqa: BLE001
        pass


def dispatch_channel_member_event(channel_id, user_id, event: str) -> None:
    try:
        from apps.bots.models import BotPermissionScope, BotResourceType
        from apps.bots.services import dispatch_resource_event
        from django.utils import timezone as _tz

        dispatch_resource_event(
            BotResourceType.CHANNEL, channel_id, event, {"user_id": str(user_id)},
            event_id=f"{event}:{channel_id}:{user_id}:{_tz.now().timestamp()}",
            required_scope=BotPermissionScope.READ_MEMBERS,
        )
    except Exception:  # noqa: BLE001
        pass


def dispatch_feed_post_created(user_id, feed_entry: dict) -> None:
    try:
        from apps.bots.models import BotPermissionScope, BotResourceType
        from apps.bots.services import dispatch_resource_event
        from django.utils import timezone as _tz

        dispatch_resource_event(
            BotResourceType.FEED_PROFILE, user_id, "post.created",
            {
                "post_id": str(feed_entry.get("id") or ""), "author_id": str(user_id),
                "title": feed_entry.get("title") or "", "text": feed_entry.get("summary") or "",
            },
            event_id=f"post.created:{feed_entry.get('id')}:{_tz.now().timestamp()}",
            required_scope=BotPermissionScope.READ_MESSAGES,
        )
    except Exception:  # noqa: BLE001
        pass
