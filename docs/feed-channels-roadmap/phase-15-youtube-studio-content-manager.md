# Phase 15 - YouTube Studio-Style Content Manager

Purpose: make Channel Studio content management operational and polished.

## Required Behavior

- Real content manager with filters:
  - Draft;
  - Scheduled;
  - Published;
  - Archived;
  - Videos;
  - Shorts;
  - Posts;
  - Documents;
  - Live/Replays.
- Search by title/body.
- Per-content rows/cards with thumbnail, type, status, visibility, views, comments, updated date.
- Quick actions:
  - edit;
  - publish;
  - schedule;
  - unpublish;
  - archive/delete;
  - broadcast/unbroadcast;
  - add to playlist.
- Empty, loading, failure, and refresh states.

## Files To Change

- `apps/broadcasts/views.py`
- `apps/broadcasts/tests.py`
- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/studio/ChannelContentManager.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/studio/ChannelStudioScreen.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/hooks/useChannelsData.ts`

## ChatGPT Prompt

```text
Please implement Phase 15 of the KIS Feed Channels 200% YouTube roadmap without using git commands. Upgrade Channel Studio content manager into a YouTube Studio-style content table/list with status/type/search filters, thumbnails, visibility, metrics, and quick actions for edit, publish, schedule, unpublish, archive/delete, broadcast/unbroadcast, and add to playlist. Preserve existing APIs and validate backend/frontend.
```
