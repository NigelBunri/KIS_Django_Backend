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
