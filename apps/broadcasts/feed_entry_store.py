from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
import uuid

from django.utils import timezone
from django.utils.text import slugify
from rest_framework.exceptions import ValidationError


@dataclass(frozen=True)
class FeedEntryResolution:
    profile: dict[str, Any]
    feeds: list[dict[str, Any]]
    index: int
    entry: dict[str, Any]


def get_feed_entries(profile: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(profile, dict):
        return []
    feeds = profile.get("feeds") or []
    if not isinstance(feeds, list):
        return []
    return [deepcopy(item) for item in feeds if isinstance(item, dict)]


def with_feed_entries(
    profile: dict[str, Any] | None,
    feeds: list[dict[str, Any]],
) -> dict[str, Any]:
    next_profile = dict(profile or {})
    next_profile["feeds"] = [deepcopy(item) for item in feeds if isinstance(item, dict)]
    return next_profile


def resolve_feed_entry(
    profile: dict[str, Any] | None,
    entry_id: str,
) -> FeedEntryResolution:
    feeds = get_feed_entries(profile)
    for index, entry in enumerate(feeds):
        if str(entry.get("id")) == str(entry_id):
            return FeedEntryResolution(
                profile=with_feed_entries(profile, feeds),
                feeds=feeds,
                index=index,
                entry=deepcopy(entry),
            )
    raise ValidationError({"detail": "Feed item not found."})


def append_feed_entry(
    profile: dict[str, Any] | None,
    entry: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    feeds = get_feed_entries(profile)
    feeds.append(deepcopy(entry))
    return with_feed_entries(profile, feeds), feeds


def replace_feed_entry(
    profile: dict[str, Any] | None,
    entry_id: str,
    updater: Callable[[dict[str, Any]], dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    resolved = resolve_feed_entry(profile, entry_id)
    updated_entry = updater(deepcopy(resolved.entry))
    if not isinstance(updated_entry, dict):
        raise ValidationError({"detail": "Feed entry update produced invalid data."})
    feeds = list(resolved.feeds)
    feeds[resolved.index] = deepcopy(updated_entry)
    return with_feed_entries(profile, feeds), feeds, updated_entry


def delete_feed_entry(
    profile: dict[str, Any] | None,
    entry_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    resolved = resolve_feed_entry(profile, entry_id)
    feeds = list(resolved.feeds)
    removed = feeds.pop(resolved.index)
    return with_feed_entries(profile, feeds), feeds, removed


def _as_uuid(value: object | None):
    try:
        return uuid.UUID(str(value or "").strip())
    except (TypeError, ValueError):
        return None


def _entry_text_doc(entry: dict[str, Any]) -> dict[str, Any]:
    for key in ("text_doc", "textDoc", "rich_text", "richText", "styled_text", "styledText"):
        value = entry.get(key)
        if isinstance(value, dict):
            return deepcopy(value)
    return {}


def _entry_text_plain(entry: dict[str, Any]) -> str:
    for key in ("text_plain", "textPlain", "text_preview", "textPreview", "summary", "description"):
        value = str(entry.get(key) or "").strip()
        if value:
            return value
    return ""


def _content_type_from_entry(entry: dict[str, Any]) -> str:
    explicit = str(entry.get("content_type") or entry.get("contentType") or "").strip().lower()
    media_type = str(entry.get("media_type") or entry.get("mediaType") or "").strip().lower()
    if explicit in {
        "video",
        "short_video",
        "image",
        "gallery",
        "text",
        "rich_text",
        "audio",
        "document",
        "link",
        "poll",
        "event",
        "live_stream",
        "replay",
    }:
        return explicit
    if entry.get("poll"):
        return "poll"
    if entry.get("event"):
        return "event"
    if entry.get("link"):
        return "link"
    if _entry_text_doc(entry):
        return "rich_text"
    if media_type in {"video", "short_video", "image", "audio"}:
        return media_type
    if media_type in {"file", "document", "pdf"}:
        return "document"
    attachments = entry.get("attachments") or []
    if isinstance(attachments, list) and len([item for item in attachments if isinstance(item, dict)]) > 1:
        image_count = sum(1 for item in attachments if str(item.get("media_type") or item.get("kind") or "").lower() == "image")
        if image_count == len(attachments):
            return "gallery"
    return "text"


def _asset_payload_from_attachment(attachment: dict[str, Any], index: int) -> dict[str, Any]:
    asset_type = str(
        attachment.get("asset_type")
        or attachment.get("media_type")
        or attachment.get("kind")
        or attachment.get("type")
        or "file"
    ).strip().lower()
    if asset_type == "file":
        asset_type = "document"
    return {
        "asset_type": asset_type[:32] or "document",
        "url": str(attachment.get("url") or attachment.get("uri") or "").strip(),
        "storage_path": str(attachment.get("path") or attachment.get("storage_path") or attachment.get("storagePath") or "").strip(),
        "mime_type": str(attachment.get("mime_type") or attachment.get("mimeType") or attachment.get("type") or "").strip()[:128],
        "size_bytes": _safe_int(attachment.get("size_bytes") or attachment.get("sizeBytes") or attachment.get("size")),
        "width": _safe_int(attachment.get("width")),
        "height": _safe_int(attachment.get("height")),
        "duration_seconds": _safe_int(attachment.get("duration_seconds") or attachment.get("durationSeconds")),
        "thumbnail_url": str(attachment.get("thumbnail_url") or attachment.get("thumbUrl") or attachment.get("thumbnailUrl") or "").strip(),
        "caption": str(attachment.get("caption") or attachment.get("name") or "").strip(),
        "sort_order": index,
        "processing_status": str(attachment.get("processing_status") or attachment.get("processingStatus") or "ready").strip()[:24] or "ready",
        "metadata": deepcopy(attachment),
    }


def _entry_attachments(entry: dict[str, Any]) -> list[dict[str, Any]]:
    attachments = []
    primary = entry.get("attachment")
    if isinstance(primary, dict):
        attachments.append(primary)
    raw = entry.get("attachments") or []
    if isinstance(raw, list):
        attachments.extend(item for item in raw if isinstance(item, dict))
    seen = set()
    deduped = []
    for item in attachments:
        identity = str(item.get("id") or item.get("url") or item.get("path") or item.get("name") or "").strip()
        if identity and identity in seen:
            continue
        if identity:
            seen.add(identity)
        deduped.append(item)
    return deduped


def _safe_int(value):
    try:
        if value in (None, ""):
            return None
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return None


def _safe_channel_handle(user) -> str:
    base = slugify(
        getattr(user, "display_name", None)
        or getattr(user, "username", None)
        or getattr(user, "phone", None)
        or f"user-{getattr(user, 'id', '')}"
    )[:54].strip("-")
    suffix = str(getattr(user, "id", ""))[:8]
    return f"{base or 'kis-user'}-{suffix}".strip("-")[:80]


def _get_or_create_personal_channel(user, profile: dict[str, Any] | None = None):
    from apps.broadcasts.models import BroadcastChannel, BroadcastChannelRole

    channel = BroadcastChannel.objects.filter(
        owner_type=BroadcastChannel.OwnerType.USER,
        owner_id=user.id,
        owner_user=user,
        is_deleted=False,
    ).order_by("created_at").first()
    if channel:
        return channel

    profile = profile if isinstance(profile, dict) else {}
    channel = BroadcastChannel.objects.create(
        owner_type=BroadcastChannel.OwnerType.USER,
        owner_id=user.id,
        owner_user=user,
        handle=_safe_channel_handle(user),
        display_name=str(
            profile.get("profile_name")
            or profile.get("title")
            or getattr(user, "display_name", "")
            or getattr(user, "username", "")
            or getattr(user, "phone", "")
            or "KIS Channel"
        )[:140],
        description=str(profile.get("notes") or profile.get("description") or "")[:5000],
        is_public=True,
    )
    BroadcastChannelRole.objects.get_or_create(
        channel=channel,
        user=user,
        role=BroadcastChannelRole.Role.OWNER,
    )
    return channel


def channel_content_payload_from_feed_entry(channel, entry: dict[str, Any], broadcast_item=None) -> dict[str, Any]:
    attachments = _entry_attachments(entry)
    first_attachment = attachments[0] if attachments else {}
    is_broadcast = bool(entry.get("is_broadcast") or broadcast_item)
    content_type = _content_type_from_entry(entry)
    text_doc = _entry_text_doc(entry)
    text_plain = _entry_text_plain(entry)
    published_at = timezone.now() if is_broadcast else None
    if entry.get("broadcasted_at"):
        try:
            published_at = datetime.fromisoformat(str(entry["broadcasted_at"]).replace("Z", "+00:00"))
        except ValueError:
            published_at = timezone.now()
    return {
        "channel": channel,
        "legacy_broadcast_item": broadcast_item,
        "legacy_feed_entry_id": _as_uuid(entry.get("id")),
        "content_type": content_type,
        "title": str(entry.get("title") or text_plain[:80] or "Untitled")[:220],
        "description": str(entry.get("summary") or entry.get("description") or "")[:10000],
        "text_plain": text_plain,
        "text_doc": text_doc,
        "thumbnail_url": str(
            entry.get("thumbnail_url")
            or entry.get("thumbnailUrl")
            or first_attachment.get("thumbnail_url")
            or first_attachment.get("thumbUrl")
            or first_attachment.get("url")
            or ""
        ).strip(),
        "visibility": str(entry.get("visibility") or "public").strip().lower()[:16] or "public",
        "status": "published" if is_broadcast else "draft",
        "published_at": published_at,
        "scheduled_at": None,
        "duration_seconds": _safe_int(entry.get("duration_seconds") or first_attachment.get("duration_seconds")),
        "metadata": {
            "legacy_feed_entry": deepcopy(entry),
            "legacy_profile_id": str((entry.get("profile_id") or "") or ""),
        },
        "stats": {
            "views": int(entry.get("views") or entry.get("view_count") or 0),
            "shares": int(entry.get("shares") or entry.get("share_count") or 0),
            "comments": int(entry.get("comments") or entry.get("comment_count") or 0),
            "reactions": int(entry.get("reactions") or entry.get("reaction_count") or 0),
        },
        "is_deleted": False,
    }


def sync_channel_content_from_feed_entry(user, profile: dict[str, Any] | None, entry: dict[str, Any], broadcast_item=None):
    if not user or not isinstance(entry, dict):
        return None
    entry_uuid = _as_uuid(entry.get("id"))
    if not entry_uuid:
        return None

    from apps.broadcasts.models import ChannelContent, ChannelContentAsset

    channel = _get_or_create_personal_channel(user, profile)
    payload = channel_content_payload_from_feed_entry(channel, entry, broadcast_item=broadcast_item)
    created_by = user if getattr(user, "is_authenticated", False) else None
    content = ChannelContent.objects.filter(legacy_feed_entry_id=entry_uuid, channel=channel).first()
    if content:
        for key, value in payload.items():
            setattr(content, key, value)
        content.created_by = content.created_by or created_by
        content.save()
    else:
        content = ChannelContent.objects.create(**payload, created_by=created_by)

    ChannelContentAsset.objects.filter(content=content).delete()
    for index, attachment in enumerate(_entry_attachments(entry)):
        asset_payload = _asset_payload_from_attachment(attachment, index)
        ChannelContentAsset.objects.create(content=content, **asset_payload)

    if broadcast_item:
        metadata = dict(getattr(broadcast_item, "metadata", None) or {})
        metadata["channel_content_id"] = str(content.id)
        broadcast_item.metadata = metadata
        broadcast_item.save(update_fields=["metadata", "updated_at"])
    return content


def archive_channel_content_for_feed_entry(user, entry_id: str, *, hard_deleted: bool = False):
    entry_uuid = _as_uuid(entry_id)
    if not entry_uuid or not user:
        return None
    from apps.broadcasts.models import BroadcastChannel, ChannelContent

    channel = BroadcastChannel.objects.filter(
        owner_type=BroadcastChannel.OwnerType.USER,
        owner_id=user.id,
        owner_user=user,
        is_deleted=False,
    ).order_by("created_at").first()
    if not channel:
        return None
    content = ChannelContent.objects.filter(channel=channel, legacy_feed_entry_id=entry_uuid).first()
    if not content:
        return None
    content.status = ChannelContent.Status.ARCHIVED
    content.visibility = ChannelContent.Visibility.PRIVATE
    if hard_deleted:
        content.is_deleted = True
    content.save(update_fields=["status", "visibility", "is_deleted", "updated_at"])
    return content


def broadcast_item_payload_from_channel_content(content) -> dict[str, Any]:
    first_asset = content.assets.order_by("sort_order", "created_at").first()
    payload = {
        "id": str(content.legacy_feed_entry_id or content.id),
        "channel_content_id": str(content.id),
        "title": content.title,
        "summary": content.description,
        "media_type": content.content_type,
        "text_plain": content.text_plain,
        "text_doc": deepcopy(content.text_doc or {}),
        "thumbnail_url": content.thumbnail_url,
        "is_broadcast": content.status == content.Status.PUBLISHED,
        "created_at": content.created_at.isoformat() if content.created_at else "",
        "updated_at": content.updated_at.isoformat() if content.updated_at else "",
    }
    if first_asset:
        payload["attachment"] = {
            "media_type": first_asset.asset_type,
            "url": first_asset.url,
            "path": first_asset.storage_path,
            "mime_type": first_asset.mime_type,
            "thumbnail_url": first_asset.thumbnail_url,
            "caption": first_asset.caption,
        }
    payload["attachments"] = [
        {
            "media_type": asset.asset_type,
            "url": asset.url,
            "path": asset.storage_path,
            "mime_type": asset.mime_type,
            "thumbnail_url": asset.thumbnail_url,
            "caption": asset.caption,
        }
        for asset in content.assets.order_by("sort_order", "created_at")
    ]
    return payload
