# Phase 07 - Live Streaming Foundation

Purpose: add live-streaming structure and UI without committing to a final provider too early.

## Files To Change

Backend:

- `apps/broadcasts/models.py`
- `apps/broadcasts/serializers.py`
- `apps/broadcasts/views.py`
- `apps/broadcasts/urls.py`
- `apps/broadcasts/tests.py`
- `.env.example`

Frontend:

- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/studio/ChannelStudioScreen.tsx`
- New: `/Users/nigel/dev/KIS/src/screens/broadcast/channels/studio/LiveControlRoom.tsx`
- New: `/Users/nigel/dev/KIS/src/screens/broadcast/channels/LiveWatchPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/api/channels.types.ts`

Docs:

- `docs/feed-channels-roadmap/status.md`

## Backend Models

Add:

```python
class ChannelLiveStream(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.ForeignKey(BroadcastChannel, on_delete=models.CASCADE, related_name="live_streams")
    content = models.OneToOneField(ChannelContent, null=True, blank=True, on_delete=models.SET_NULL, related_name="live_stream")
    title = models.CharField(max_length=220)
    description = models.TextField(blank=True, default="")
    status = models.CharField(max_length=24, choices=[("scheduled","Scheduled"),("live","Live"),("ended","Ended"),("cancelled","Cancelled"),("failed","Failed")], default="scheduled", db_index=True)
    scheduled_start_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    provider = models.CharField(max_length=32, blank=True, default="")
    provider_stream_id = models.CharField(max_length=160, blank=True, default="")
    ingest_url = models.CharField(max_length=512, blank=True, default="")
    stream_key_hash = models.CharField(max_length=128, blank=True, default="")
    playback_url = models.URLField(blank=True, default="")
    replay_url = models.URLField(blank=True, default="")
    thumbnail_url = models.URLField(blank=True, default="")
    viewer_count = models.PositiveIntegerField(default=0)
    peak_viewer_count = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

Do not store raw stream key. Store only hash or provider reference.

## Env Examples

Add to `.env.example`:

```text
LIVE_STREAM_PROVIDER=disabled
LIVE_STREAM_PROVIDER_SANDBOX_ENABLED=False
LIVE_STREAM_WEBHOOK_SECRET=replace-with-live-provider-webhook-secret
LIVE_STREAM_DEFAULT_LATENCY=standard
```

## API Endpoints

- `POST /broadcasts/channels/<channel_id>/live-streams/` schedule
- `GET /broadcasts/channels/<channel_id>/live-streams/`
- `GET /broadcasts/live-streams/<id>/`
- `POST /broadcasts/live-streams/<id>/start/` dev/provider-placeholder only
- `POST /broadcasts/live-streams/<id>/end/`
- `POST /broadcasts/live-streams/webhook/<provider>/`

Provider calls should be disabled by default. Return safe placeholder ingest/playback info in local dev only.

## Frontend

`LiveControlRoom.tsx`:

- schedule live stream form;
- stream key area masked by default;
- copy ingest URL button;
- stream health placeholder;
- start/end buttons for dev only;
- chat/moderation placeholder.

`LiveWatchPage.tsx`:

- scheduled waiting state;
- live player placeholder/playback URL;
- viewer count;
- live chat placeholder;
- replay state.

## Validation

```bash
python3 manage.py makemigrations broadcasts
python3 manage.py check
cd /Users/nigel/dev/KIS
npx eslint src/screens/broadcast/channels --quiet
npm run typecheck
```

## ChatGPT Prompt

```text
Please implement Phase 07 of KIS Feed Channels without using git commands. Add provider-neutral live streaming models, env flags, APIs, serializers, and React Native LiveControlRoom/LiveWatchPage placeholders. Do not make live provider calls by default and do not store raw stream keys. Preserve existing feed/channel behavior. Update status docs.
```

