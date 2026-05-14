# Phase 16 - Media Processing And Upload Pipeline

Purpose: move from metadata-ready channel assets to a production upload and processing pipeline.

## Required Behavior

- Direct upload into private media.
- File type and size policy per content type.
- Malware scan/quarantine hook.
- Processing states:
  - queued;
  - processing;
  - ready;
  - failed.
- Video metadata:
  - duration;
  - width;
  - height;
  - thumbnail.
- Frontend upload queue:
  - progress;
  - retry;
  - cancel;
  - failure messaging.
- Preserve existing composer queue behavior.

## Files To Change

- `apps/media/*`
- `apps/broadcasts/models.py`
- `apps/broadcasts/views.py`
- `apps/broadcasts/tests.py`
- `/Users/nigel/dev/KIS/src/components/feeds/composer/*`
- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/studio/*`

## ChatGPT Prompt

```text
Please implement Phase 16 of the KIS Feed Channels 200% YouTube roadmap without using git commands. Add production-safe media upload and processing foundations for channel content: private upload references, MIME/size validation by content type, malware/quarantine hook points, processing states, metadata extraction placeholders, frontend upload progress/retry/cancel UI, focused tests, validation, and docs updates.
```
