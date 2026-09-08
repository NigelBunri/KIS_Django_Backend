# apps/communities/notifications.py
#
# Persistent (in-app + push) notifications for Community lifecycle events,
# distinct from apps.communities.realtime's live socket nudges: those are
# fire-and-forget UI refresh hints for a client already looking at the
# screen, these are durable Notification rows the recipient sees later too.
# Reuses apps.notifications.services.create_notification exactly like every
# other content-owning app (see apps/broadcasts/views.py's
# _notify_channel_subscribers for the same per-recipient fan-out shape)
# rather than inventing a second notification system.
import logging

logger = logging.getLogger(__name__)

# Fields whose change actually affects whether/how a member can find,
# join, or be seen in the community - the ones worth interrupting members
# for. Toggling e.g. allow_polls or allow_links is a minor capability
# change, not "important" in the sense the notification is meant for.
NOTIFIABLE_SETTINGS_FIELDS = {"visibility", "join_policy"}


def _create(*, user_id, notification_type, title, body, target_id, dedup_key, context=None):
    from apps.notifications.services import create_notification

    try:
        create_notification(
            user_id=user_id,
            type=notification_type,
            title=title[:400],
            body=body[:1000],
            target_type="community",
            target_id=target_id,
            priority="MEDIUM",
            dedup_key=dedup_key,
            context=context or {},
        )
    except Exception:
        logger.exception(
            "Unable to send community notification type=%s target=%s user=%s",
            notification_type, target_id, user_id,
        )


def notify_join_request_created(community, join_request) -> None:
    """Fan out to every admin/mod/owner - mirrors
    apps.broadcasts.views._notify_channel_subscribers's per-recipient loop.
    """
    from apps.communities.realtime import _admin_ids

    requester_name = (
        getattr(join_request.user, "display_name", None)
        or getattr(join_request.user, "username", None)
        or "Someone"
    )
    for admin_id in _admin_ids(community)[:500]:
        _create(
            user_id=admin_id,
            notification_type="community.join_request.created",
            title=f"{requester_name} wants to join {community.name}",
            body="Tap to review this request.",
            target_id=community.id,
            dedup_key=f"community.join_request.created:{join_request.id}:{admin_id}",
            context={"community_id": str(community.id), "join_request_id": str(join_request.id)},
        )


def notify_join_request_decided(community, join_request, *, approved: bool) -> None:
    _create(
        user_id=join_request.user_id,
        notification_type="community.join_request.decided",
        title=f"Your request to join {community.name} was {'approved' if approved else 'declined'}",
        body="Tap to view." if approved else "You can request to join again later.",
        target_id=community.id,
        dedup_key=f"community.join_request.decided:{join_request.id}",
        context={"community_id": str(community.id), "approved": approved},
    )


def notify_role_changed(community, membership, *, previous_role: str) -> None:
    if previous_role == membership.role:
        return
    _create(
        user_id=membership.user_id,
        notification_type="community.role_changed",
        title=f"Your role in {community.name} changed to {membership.get_role_display()}",
        body="Tap to view.",
        target_id=community.id,
        dedup_key=f"community.role_changed:{community.id}:{membership.user_id}:{membership.role}",
        context={"community_id": str(community.id), "role": membership.role},
    )


def notify_member_removed(community, user_id) -> None:
    # Deliberately generic - no reason/moderation detail leaked to the
    # removed user beyond the fact of the removal itself.
    _create(
        user_id=user_id,
        notification_type="community.member_removed",
        title=f"You were removed from {community.name}",
        body="You can rejoin later if the community allows it.",
        target_id=community.id,
        dedup_key=f"community.member_removed:{community.id}:{user_id}",
        context={"community_id": str(community.id)},
    )


def notify_member_banned(community, user_id) -> None:
    _create(
        user_id=user_id,
        notification_type="community.member_banned",
        title=f"You were banned from {community.name}",
        body="Contact the community admins if you believe this is a mistake.",
        target_id=community.id,
        dedup_key=f"community.member_banned:{community.id}:{user_id}",
        context={"community_id": str(community.id)},
    )


def notify_settings_changed(community, *, changed_by, changed_fields) -> None:
    notifiable = set(changed_fields) & NOTIFIABLE_SETTINGS_FIELDS
    if not notifiable:
        return
    from apps.communities.realtime import _active_member_ids

    for user_id in _active_member_ids(community, exclude_user_id=changed_by.id)[:500]:
        _create(
            user_id=user_id,
            notification_type="community.settings_changed",
            title=f"{community.name} updated its settings",
            body="Tap to view what changed.",
            target_id=community.id,
            # Deliberately no field values in the dedup key or body - this
            # is a "something changed, go look" nudge, not an audit log.
            dedup_key=f"community.settings_changed:{community.id}:{user_id}:{'-'.join(sorted(notifiable))}",
            context={"community_id": str(community.id), "changed_fields": sorted(notifiable)},
        )
