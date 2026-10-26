# apps/broadcasts/realtime.py
#
# Best-effort live-update push for broadcast-channel events, reusing the
# exact platform pattern already proven for Communities/Partners-system
# events (apps.communities.realtime.notify_nest_of_community_event ->
# Nest's POST communities/:communityId/events): Django is the source of
# truth and persists every state change on its own; this is purely a
# fire-and-forget nudge so a client already looking at the screen updates
# live instead of waiting for a manual refresh or the next REST poll. If
# Nest is unreachable or unconfigured, every caller here degrades to a
# no-op - the mutating request that triggered it has already succeeded and
# committed by the time any of these run.
import json
import logging
import urllib.request

from django.conf import settings

from apps.chat.internal_signing import sign_internal_request

logger = logging.getLogger(__name__)


def _post_to_nest(path: str, body: dict) -> None:
    base = str(getattr(settings, "NEST_INTERNAL_URL", "")).strip().rstrip("/")
    # Nest's InternalAuthGuard checks against its own DJANGO_INTERNAL_TOKEN
    # env var, not NEST_INTERNAL_TOKEN (which Nest never reads) - see
    # apps/chat/tasks.py's _post_to_nest for the full explanation.
    token = str(getattr(settings, "DJANGO_INTERNAL_TOKEN", "")).strip()
    if not base or not token:
        return

    # RealtimeInternalController is @Controller('internal') on the Nest
    # side - missing this prefix 404s.
    url = f"{base}/internal/{path}"
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
        logger.debug("Unable to emit broadcast realtime event to %s: %s", path, exc)


def notify_nest_of_channel_event(*, channel_id, event: str, user_ids, data=None) -> None:
    """Per-user-room fan-out, for events whose audience isn't necessarily
    already watching the live stream (a channel going live, ending, new
    content published, a subscription change) - see
    broadcasts/channels/:channelId/events on the Nest side."""
    clean_user_ids = sorted({str(uid) for uid in (user_ids or []) if uid})
    if not clean_user_ids:
        return
    _post_to_nest(
        f"broadcasts/channels/{channel_id}/events",
        {"event": event, "userIds": clean_user_ids, "data": data or {}},
    )


def notify_nest_of_live_stream_broadcast(*, stream_id, event: str, payload=None) -> None:
    """Direct broadcast into the `stream:<streamId>` socket room every
    viewer already joined on `channel.live.join` (see
    realtime/handlers/live.ts) - for events only people already watching
    the stream need, like a chat message. See
    broadcasts/live-streams/:streamId/broadcast on the Nest side."""
    _post_to_nest(
        f"broadcasts/live-streams/{stream_id}/broadcast",
        {"event": event, "payload": payload or {}},
    )


def _subscriber_ids(channel, *, exclude_user_id=None) -> list:
    from apps.broadcasts.models import BroadcastChannelSubscription

    qs = BroadcastChannelSubscription.objects.filter(channel=channel).values_list("user_id", flat=True)
    ids = [str(uid) for uid in qs]
    if exclude_user_id is not None:
        exclude = str(exclude_user_id)
        ids = [uid for uid in ids if uid != exclude]
    return ids


def notify_channel_live_started(channel, live_stream) -> None:
    notify_nest_of_channel_event(
        channel_id=channel.id,
        event="channel.live.started",
        user_ids=_subscriber_ids(channel),
        data={
            "channelId": str(channel.id),
            "streamId": str(live_stream.id),
            "title": live_stream.title or "",
            "channelName": channel.display_name,
        },
    )


def notify_channel_live_ended(channel, live_stream) -> None:
    notify_nest_of_channel_event(
        channel_id=channel.id,
        event="channel.live.ended",
        user_ids=_subscriber_ids(channel),
        data={"channelId": str(channel.id), "streamId": str(live_stream.id)},
    )


def notify_channel_content_published(content) -> None:
    notify_nest_of_channel_event(
        channel_id=content.channel_id,
        event="channel.content.published",
        user_ids=_subscriber_ids(content.channel),
        data={
            "channelId": str(content.channel_id),
            "contentId": str(content.id),
            "title": content.title or "",
        },
    )


def notify_channel_subscribed(channel, user_id, *, subscribed: bool) -> None:
    # Self-only audience - this just keeps the same user's other open
    # sessions/devices in sync with a subscribe/unsubscribe they just did
    # on this one, not a broadcast to anyone else.
    notify_nest_of_channel_event(
        channel_id=channel.id,
        event="channel.subscribed",
        user_ids=[str(user_id)],
        data={"channelId": str(channel.id), "subscribed": subscribed},
    )


def notify_live_stream_status_changed(live) -> None:
    """Pushes a status transition (live/ended) into the stream's own
    `stream:<streamId>` room (C6 of the YouTube-parity batch) - distinct
    from notify_channel_live_started/_ended above, which notify
    *subscribers* (a per-user audience, for push notifications to people
    not currently watching). This reaches people ALREADY on LiveWatchPage,
    already joined via channel.live.join, who were previously only finding
    out about a status change on their next REST poll tick. Callers are
    responsible for the same go-live honesty gating _notify_live_started
    already enforces - this function itself does not re-derive that."""
    notify_nest_of_live_stream_broadcast(
        stream_id=live.id,
        event="channel.live.status_changed",
        payload={"streamId": str(live.id), "status": live.status},
    )


def notify_live_chat_message_deleted(stream_id, *, message_id) -> None:
    notify_nest_of_live_stream_broadcast(
        stream_id=stream_id,
        event="channel.chat.message_deleted",
        payload={"streamId": str(stream_id), "id": str(message_id)},
    )


def notify_live_chat_message_pinned(stream_id, *, message_id, is_pinned: bool) -> None:
    notify_nest_of_live_stream_broadcast(
        stream_id=stream_id,
        event="channel.chat.message_pinned",
        payload={"streamId": str(stream_id), "id": str(message_id), "isPinned": is_pinned},
    )


def notify_live_chat_message(stream_id, *, message, is_member: bool = False) -> None:
    payload = {
        "streamId": str(stream_id),
        "id": str(message.id),
        "userId": str(message.user_id) if message.user_id else "",
        "displayName": message.display_name,
        "text": message.message,
        "messageType": message.message_type,
        "sentAt": message.created_at.isoformat(),
        "isMember": is_member,
    }
    # Only ever one of these applies per message (STICKER xor VOICE_NOTE) -
    # same "omit entirely rather than send null" convention
    # ChannelLiveStreamChatHistoryView.get already uses for the REST
    # history payload, so a live-socket delivery and a page-load fetch of
    # the same message always look identical to the client. Deliberately
    # NEVER a baked sticker image url - see that view's own comment for
    # why (private bucket, real access-control chokepoint, ~1h presigned
    # expiry) - stickerAssetId lets the client fetch a fresh signed URL
    # itself via the existing GET /media/assets/<id>/signed-url/ endpoint.
    if message.message_type == message.MessageType.STICKER and message.sticker_id:
        payload["stickerAssetId"] = str(message.sticker.asset_id)
    elif message.message_type == message.MessageType.VOICE_NOTE and message.asset_url:
        payload["assetUrl"] = message.asset_url
        payload["durationSeconds"] = message.duration_seconds
    notify_nest_of_live_stream_broadcast(
        stream_id=stream_id,
        event="channel.chat.message",
        payload=payload,
    )
