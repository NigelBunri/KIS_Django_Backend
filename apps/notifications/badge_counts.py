from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from django.db.models import Q
from django.utils import timezone

from apps.chat.models import ConversationMember, ConversationNotificationLevel, ConversationType
from apps.notifications.models import Notification


BIBLE_NOTIFICATION_TOKENS = ("bible", "reading", "meditation", "devotional")
BROADCAST_NOTIFICATION_TOKENS = (
    "broadcast",
    "channel",
    "course",
    "lesson",
    "product",
    "market",
    "shop",
    "event",
    "education",
    "health",
    "institution",
)
PARTNER_NOTIFICATION_TOKENS = ("partner", "community", "partner_group", "group_message")


@dataclass(frozen=True)
class MainTabBadgeCounts:
    messages: int = 0
    bible: int = 0
    broadcast: int = 0
    partners: int = 0
    profile: int = 0

    @property
    def total(self) -> int:
        return self.messages + self.bible + self.broadcast + self.partners + self.profile

    def as_dict(self) -> dict:
        counts = {
            "Messages": self.messages,
            "Bible": self.bible,
            "Broadcast": self.broadcast,
            "Partners": self.partners,
            "Profile": self.profile,
        }
        return {
            "counts": counts,
            "sources": {
                "messages": "chat_conversation_member.last_read_seq",
                "bible": "notifications + bible_reading_events",
                "broadcast": "channel subscriptions/watch history + notifications",
                "partners": "partner conversations",
                "profile": "notifications",
            },
            "total": self.total,
        }


def _clamp(value: int | float | None) -> int:
    try:
        num = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(num, 999))


def _unread_notification_queryset(user):
    return Notification.objects.filter(user_id=user.id, is_deleted=False, is_read=False)


def _notification_text_filter(tokens: Iterable[str]) -> Q:
    query = Q()
    for token in tokens:
        query |= (
            Q(type__icontains=token)
            | Q(title__icontains=token)
            | Q(body__icontains=token)
            | Q(target_type__icontains=token)
            | Q(context_data__source__iexact=token)
            | Q(context_data__badge_source__iexact=token)
        )
    return query


def _conversation_unread_counts(user) -> tuple[int, int]:
    partner_conversation_ids = set()
    try:
        from apps.partners.models import Partner

        partner_conversation_ids.update(
            str(value)
            for value in Partner.objects.filter(main_conversation__memberships__user=user, main_conversation__memberships__left_at__isnull=True)
            .exclude(main_conversation_id__isnull=True)
            .values_list("main_conversation_id", flat=True)
        )
    except Exception:
        partner_conversation_ids = set()

    try:
        from apps.channels.models import Channel

        partner_conversation_ids.update(
            str(value)
            for value in Channel.objects.filter(
                partner__isnull=False,
                conversation__memberships__user=user,
                conversation__memberships__left_at__isnull=True,
            ).values_list("conversation_id", flat=True)
        )
    except Exception:
        pass

    message_unread = 0
    partner_unread = 0
    memberships = (
        ConversationMember.objects.select_related("conversation")
        .filter(user=user, left_at__isnull=True, is_hidden=False)
        .exclude(notification_level=ConversationNotificationLevel.NONE)
        .exclude(conversation__type=ConversationType.POST)
        .exclude(conversation__type=ConversationType.THREAD)
        .only("last_read_seq", "conversation_id", "conversation__last_message_seq")
    )
    for member in memberships.iterator():
        unread = max(int(member.conversation.last_message_seq or 0) - int(member.last_read_seq or 0), 0)
        if unread <= 0:
            continue
        if str(member.conversation_id) in partner_conversation_ids:
            partner_unread += unread
        else:
            message_unread += unread
    return _clamp(message_unread), _clamp(partner_unread)


def _bible_unread_count(user) -> int:
    unread_notifications = _unread_notification_queryset(user).filter(_notification_text_filter(BIBLE_NOTIFICATION_TOKENS)).count()
    missed_events = 0
    try:
        from apps.bible.models import BibleReadingPlanEvent

        missed_events = BibleReadingPlanEvent.objects.filter(user=user).filter(
            Q(status="missed") | Q(status="scheduled", start_at__lt=timezone.now())
        ).count()
    except Exception:
        missed_events = 0
    return _clamp(unread_notifications + missed_events)


def _broadcast_unread_count(user) -> int:
    notification_count = _unread_notification_queryset(user).filter(_notification_text_filter(BROADCAST_NOTIFICATION_TOKENS)).count()
    channel_content_count = 0
    try:
        from apps.broadcasts.models import BroadcastChannelSubscription, ChannelContent, ChannelWatchHistory

        subscriptions = BroadcastChannelSubscription.objects.filter(user=user).exclude(notifications="none")
        channel_ids = list(subscriptions.values_list("channel_id", flat=True))
        if channel_ids:
            seen_content_ids = ChannelWatchHistory.objects.filter(user=user).values_list("content_id", flat=True)
            channel_content_count = (
                ChannelContent.objects.filter(
                    channel_id__in=channel_ids,
                    status="published",
                    visibility="public",
                    is_deleted=False,
                    published_at__isnull=False,
                )
                .exclude(id__in=seen_content_ids)
                .count()
            )
    except Exception:
        channel_content_count = 0
    return _clamp(notification_count + channel_content_count)


def _comment_room_broadcast_unread_count(user) -> int:
    """
    Unread messages in POST-type comment rooms routed to the Broadcast badge.

    Eligible only when the user is subscribed to the BroadcastChannel that
    published the post, OR has previously engaged with the comment room
    (last_read_seq > 0 — proxy for having opened it before).
    """
    try:
        from apps.broadcasts.models import (
            BroadcastItem,
            BroadcastChannelSubscription,
            ChannelContent,
        )

        subscribed_channel_ids = frozenset(
            BroadcastChannelSubscription.objects
            .filter(user=user)
            .values_list("channel_id", flat=True)
        )

        memberships = list(
            ConversationMember.objects
            .filter(user=user, left_at__isnull=True, conversation__type=ConversationType.POST)
            .select_related("conversation")
            .only("last_read_seq", "conversation_id", "conversation__last_message_seq")
        )
        if not memberships:
            return 0

        conv_data: dict[str, tuple[int, bool]] = {}
        for m in memberships:
            unread = max(int(m.conversation.last_message_seq or 0) - int(m.last_read_seq or 0), 0)
            engaged = int(m.last_read_seq or 0) > 0
            conv_data[str(m.conversation_id)] = (unread, engaged)

        post_conv_ids = [cid for cid, (unread, _) in conv_data.items() if unread > 0]
        if not post_conv_ids:
            return 0

        items = list(
            BroadcastItem.objects
            .filter(comment_conversation_id__in=post_conv_ids, is_deleted=False)
            .values("comment_conversation_id", "source_type", "source_id")
        )

        content_ids = [bi["source_id"] for bi in items if bi["source_type"] == "channel_content"]
        content_channel_map: dict[str, object] = {}
        if content_ids:
            for row in ChannelContent.objects.filter(id__in=content_ids).values("id", "channel_id"):
                content_channel_map[str(row["id"])] = row["channel_id"]

        total = 0
        for bi in items:
            conv_id = str(bi["comment_conversation_id"])
            unread, engaged = conv_data.get(conv_id, (0, False))
            if unread <= 0:
                continue

            is_subscribed = False
            if subscribed_channel_ids:
                source_type = bi["source_type"]
                source_id = str(bi["source_id"])
                if source_type == "broadcast_channel":
                    try:
                        import uuid as _uuid
                        is_subscribed = _uuid.UUID(source_id) in subscribed_channel_ids
                    except Exception:
                        pass
                elif source_type == "channel_content":
                    channel_id = content_channel_map.get(source_id)
                    if channel_id is not None:
                        is_subscribed = channel_id in subscribed_channel_ids

            if is_subscribed or engaged:
                total += unread

        return _clamp(total)
    except Exception:
        return 0


def get_main_tab_badge_counts(user) -> MainTabBadgeCounts:
    if not user or not getattr(user, "is_authenticated", False):
        return MainTabBadgeCounts()

    profile_unread = _unread_notification_queryset(user).count()
    messages_unread, partners_unread = _conversation_unread_counts(user)
    partner_notification_unread = _unread_notification_queryset(user).filter(_notification_text_filter(PARTNER_NOTIFICATION_TOKENS)).count()
    comment_room_broadcast = _comment_room_broadcast_unread_count(user)
    return MainTabBadgeCounts(
        messages=messages_unread,
        bible=_bible_unread_count(user),
        broadcast=_clamp(_broadcast_unread_count(user) + comment_room_broadcast),
        partners=_clamp(partners_unread + partner_notification_unread),
        profile=_clamp(profile_unread),
    )
