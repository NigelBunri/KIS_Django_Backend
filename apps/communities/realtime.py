# apps/communities/realtime.py
#
# Best-effort live-update push for Community events, reusing the exact
# platform pattern already proven for Partners-system events
# (apps.partners.services.notify_nest_of_partner_event ->
# Nest's POST partners/:partnerId/events): Django is the source of truth
# and persists every state change on its own; this is purely a fire-and-
# forget nudge so a client already looking at the screen updates live
# instead of waiting for a manual refresh. If Nest is unreachable or
# unconfigured, every caller here degrades to a no-op - the mutating
# request that triggered it has already succeeded and committed by the
# time any of these run.
import json
import logging
import urllib.request

from django.conf import settings

from apps.chat.internal_signing import sign_internal_request
from apps.communities.models import CommunityMembership, CommunityMembershipStatus

logger = logging.getLogger(__name__)


def notify_nest_of_community_event(*, community_id, event: str, user_ids, data=None) -> None:
    clean_user_ids = sorted({str(uid) for uid in (user_ids or []) if uid})
    if not clean_user_ids:
        return

    base = str(getattr(settings, "NEST_INTERNAL_URL", "")).strip().rstrip("/")
    token = str(getattr(settings, "NEST_INTERNAL_TOKEN", "")).strip()
    if not base or not token:
        return

    url = f"{base}/communities/{community_id}/events"
    body = {"event": event, "userIds": clean_user_ids, "data": data or {}}
    try:
        headers = {
            "Content-Type": "application/json",
            **sign_internal_request("POST", url, body, secret=token),
        }
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            resp.read()
    except Exception as exc:
        logger.debug("Unable to emit community realtime event %s: %s", event, exc)


def _active_member_ids(community, *, exclude_user_id=None) -> list:
    qs = CommunityMembership.objects.active().filter(community=community).values_list("user_id", flat=True)
    ids = [str(uid) for uid in qs]
    if exclude_user_id is not None:
        exclude = str(exclude_user_id)
        ids = [uid for uid in ids if uid != exclude]
    return ids


def _admin_ids(community) -> list:
    from apps.communities.models import CommunityRole

    ids = list(
        CommunityMembership.objects.active()
        .filter(community=community, role__in=(CommunityRole.OWNER, CommunityRole.ADMIN, CommunityRole.MOD))
        .values_list("user_id", flat=True)
    )
    ids = [str(uid) for uid in ids]
    owner_id = str(community.owner_id)
    if owner_id not in ids:
        ids.append(owner_id)
    return ids


def notify_member_joined(community, membership: CommunityMembership) -> None:
    audience = _active_member_ids(community, exclude_user_id=membership.user_id)
    notify_nest_of_community_event(
        community_id=community.id,
        event="community.member_joined",
        user_ids=audience,
        data={"userId": str(membership.user_id), "role": membership.role},
    )


def notify_member_left(community, membership: CommunityMembership, *, reason: str) -> None:
    # Includes the affected user themself for "removed" so their own open
    # client can react (e.g. leave the screen) - not needed for "left"
    # since that was their own action and their client already knows.
    audience = _active_member_ids(community, exclude_user_id=membership.user_id)
    if reason == "removed":
        audience = audience + [str(membership.user_id)]
    notify_nest_of_community_event(
        community_id=community.id,
        event="community.member_left",
        user_ids=audience,
        data={"userId": str(membership.user_id), "reason": reason},
    )


def notify_member_banned(community, user_id, *, banned_by) -> None:
    audience = _active_member_ids(community) + [str(user_id)]
    notify_nest_of_community_event(
        community_id=community.id,
        event="community.member_banned",
        user_ids=audience,
        data={"userId": str(user_id)},
    )


def notify_role_changed(community, membership: CommunityMembership, *, previous_role: str, changed_by) -> None:
    audience = _active_member_ids(community, exclude_user_id=membership.user_id) + [str(membership.user_id)]
    notify_nest_of_community_event(
        community_id=community.id,
        event="community.role_changed",
        user_ids=audience,
        data={"userId": str(membership.user_id), "role": membership.role, "previousRole": previous_role},
    )


def notify_join_request_created(community, join_request) -> None:
    notify_nest_of_community_event(
        community_id=community.id,
        event="community.join_request_created",
        user_ids=_admin_ids(community),
        data={"requestId": join_request.id, "userId": str(join_request.user_id)},
    )


def notify_join_request_decided(community, join_request, *, approved: bool) -> None:
    notify_nest_of_community_event(
        community_id=community.id,
        event="community.join_request_decided",
        user_ids=[str(join_request.user_id)],
        data={"requestId": join_request.id, "approved": approved},
    )


def notify_post_created(post) -> None:
    audience = _active_member_ids(post.community, exclude_user_id=post.author_id)
    notify_nest_of_community_event(
        community_id=post.community_id,
        event="community.post_created",
        user_ids=audience,
        data={"postId": str(post.id), "authorId": str(post.author_id)},
    )


def notify_post_updated(post) -> None:
    audience = _active_member_ids(post.community, exclude_user_id=post.author_id)
    notify_nest_of_community_event(
        community_id=post.community_id,
        event="community.post_updated",
        user_ids=audience,
        data={"postId": str(post.id)},
    )


def notify_post_deleted(post) -> None:
    audience = _active_member_ids(post.community)
    notify_nest_of_community_event(
        community_id=post.community_id,
        event="community.post_deleted",
        user_ids=audience,
        data={"postId": str(post.id)},
    )


def notify_comment_created(comment) -> None:
    post = comment.post
    audience = _active_member_ids(post.community, exclude_user_id=comment.author_id)
    notify_nest_of_community_event(
        community_id=post.community_id,
        event="community.comment_created",
        user_ids=audience,
        data={"postId": str(post.id), "commentId": comment.id, "authorId": str(comment.author_id)},
    )
