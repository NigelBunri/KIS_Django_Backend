# Phase 02 - Backend Normalized Channel Content

Purpose: introduce normalized channel content rows while keeping existing `BroadcastItem` and profile JSON feed entries working.

## Files To Change

- `apps/broadcasts/models.py`
- `apps/broadcasts/feed_entry_store.py`
- `apps/broadcasts/serializers.py`
- `apps/broadcasts/views.py`
- `apps/broadcasts/tests.py`
- New migration in `apps/broadcasts/migrations/`
- `docs/feed-channels-roadmap/status.md`
- `docs/BUILD_STATE.md`

## Models To Add

In `apps/broadcasts/models.py`, add:

```python
class ChannelContentType(models.TextChoices):
    VIDEO = "video", "Video"
    SHORT_VIDEO = "short_video", "Short video"
    IMAGE = "image", "Image"
    GALLERY = "gallery", "Gallery"
    TEXT = "text", "Text"
    RICH_TEXT = "rich_text", "Rich text"
    AUDIO = "audio", "Audio"
    DOCUMENT = "document", "Document"
    LINK = "link", "Link"
    POLL = "poll", "Poll"
    EVENT = "event", "Event"
    LIVE_STREAM = "live_stream", "Live stream"
    REPLAY = "replay", "Replay"

class ChannelContent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.ForeignKey(BroadcastChannel, on_delete=models.CASCADE, related_name="contents")
    legacy_broadcast_item = models.OneToOneField("BroadcastItem", null=True, blank=True, on_delete=models.SET_NULL, related_name="channel_content")
    legacy_feed_entry_id = models.UUIDField(null=True, blank=True, db_index=True)
    content_type = models.CharField(max_length=32, choices=ChannelContentType.choices, db_index=True)
    title = models.CharField(max_length=220, blank=True, default="")
    description = models.TextField(blank=True, default="")
    text_plain = models.TextField(blank=True, default="")
    text_doc = models.JSONField(default=dict, blank=True)
    thumbnail_url = models.URLField(blank=True, default="")
    visibility = models.CharField(max_length=16, choices=[("public","Public"),("unlisted","Unlisted"),("private","Private")], default="public", db_index=True)
    status = models.CharField(max_length=24, choices=[("draft","Draft"),("scheduled","Scheduled"),("published","Published"),("processing","Processing"),("failed","Failed"),("archived","Archived")], default="draft", db_index=True)
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    scheduled_at = models.DateTimeField(null=True, blank=True, db_index=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    stats = models.JSONField(default=dict, blank=True)
    is_deleted = models.BooleanField(default=False, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_channel_contents")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["channel", "status", "published_at"]),
            models.Index(fields=["content_type", "status"]),
            models.Index(fields=["visibility", "is_deleted"]),
        ]
```

Add:

```python
class ChannelContentAsset(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    content = models.ForeignKey(ChannelContent, on_delete=models.CASCADE, related_name="assets")
    asset_type = models.CharField(max_length=32)
    url = models.URLField(blank=True, default="")
    storage_path = models.CharField(max_length=512, blank=True, default="")
    mime_type = models.CharField(max_length=128, blank=True, default="")
    size_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    thumbnail_url = models.URLField(blank=True, default="")
    caption = models.TextField(blank=True, default="")
    sort_order = models.PositiveIntegerField(default=0)
    processing_status = models.CharField(max_length=24, default="ready")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
```

## Compatibility Layer

Do not delete `feed_entry_store.py`. Add new functions:

- `channel_content_payload_from_feed_entry(channel, entry, broadcast_item=None)`
- `sync_channel_content_from_feed_entry(user, profile, entry)`
- `broadcast_item_payload_from_channel_content(content)`

Rules:

- Old JSON feed entries remain readable.
- New content rows are created when a feed entry is broadcast or edited.
- `BroadcastItem.metadata` should include `channel_content_id` where available.
- `ChannelContent.legacy_feed_entry_id` stores the old entry id.

## Serializer Changes

Add:

- `ChannelContentAssetSerializer`
- `ChannelContentListSerializer`
- `ChannelContentDetailSerializer`

Public list serializer should include:

- id, channel summary, content_type, title, description preview, text_plain preview, thumbnail_url, first asset, visibility, status, published_at, duration_seconds, stats, engagement counts.

## View Changes

In `apps/broadcasts/views.py`, locate `_sync_broadcast_feed_entry_snapshot`. Extend it so it also calls the new `sync_channel_content_from_feed_entry`.

Do not change existing response shape yet. Add `channel_content_id` to feed entry normalized payload only if available.

## Tests

Add tests:

- creating a feed entry still returns old `feed` payload;
- broadcasting a feed entry creates or updates a `ChannelContent`;
- editing a feed entry updates the matching `ChannelContent`;
- deleting/unbroadcasting does not hard-delete `ChannelContent`; it archives/unpublishes.

## Validation

```bash
python3 manage.py makemigrations broadcasts
python3 manage.py check
python3 manage.py test apps.broadcasts.tests.ChannelContentCompatibilityTests --noinput
```

## ChatGPT Prompt

```text
Please implement Phase 02 of KIS Feed Channels without using git commands. Add normalized ChannelContent and ChannelContentAsset models while preserving existing BroadcastItem and JSON feed entry behavior. Extend feed_entry_store compatibility helpers, serializers, and _sync_broadcast_feed_entry_snapshot so old feed APIs keep working. Add focused compatibility tests and update status docs.
```

