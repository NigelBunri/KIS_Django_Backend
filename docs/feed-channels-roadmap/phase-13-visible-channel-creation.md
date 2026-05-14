# Phase 13 - Visible Channel Creation And Channel-Scoped Feed Composer

Purpose: fix the immediate UX gap where a user cannot clearly create a channel, and make new feed/content creation happen inside a selected channel.

## Required Backend Behavior

- Reuse `POST /api/v1/broadcasts/channels/` for personal user channel creation.
- Keep organization channel creation blocked unless ownership is fully wired.
- Ensure `GET /api/v1/broadcasts/channels/?mine=1` returns newly created channels.
- Preserve old `/api/v1/broadcasts/profiles/feeds/` behavior.
- If a channel-aware composer payload includes `channel_id`, make sure the normalized channel content bridge can receive it safely.

## Required Frontend Behavior

- In Channel Studio, when no channel exists:
  - show a visible **Create Channel** button;
  - open a compact channel creation form/sheet;
  - collect display name, handle, category, description, public/private state;
  - call the channel creation API;
  - refresh owned channels;
  - auto-select the new channel.
- When channels exist:
  - show a **New Channel** button near the channel selector;
  - keep existing selected-channel behavior.
- Feed/content creation:
  - composer opens only with a selected channel in the Studio path;
  - UI says `Create in @handle`;
  - composer payload includes `channel_id`;
  - new channel content should not feel like a global feed detached from a channel.

## Files To Change

Backend:

- `apps/broadcasts/views.py`
- `apps/broadcasts/tests.py`

React Native:

- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/studio/ChannelStudioScreen.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/hooks/useChannelsData.ts`
- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/api/channels.types.ts`
- `/Users/nigel/dev/KIS/src/components/feeds/composer/FeedComposerSheet.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/FeedManagementModal.tsx`

Docs:

- `docs/feed-channels-roadmap/youtube-200-roadmap.md`
- `docs/feed-channels-roadmap/status.md`
- `docs/BUILD_STATE.md`

## Validation

```bash
python3 manage.py check
python3 manage.py test apps.broadcasts.tests.BroadcastChannelApiTests apps.broadcasts.tests.ChannelContentCompatibilityTests --noinput --keepdb
cd /Users/nigel/dev/KIS
npm run typecheck -- --pretty false
npx eslint src/screens/broadcast/channels src/components/feeds/composer src/screens/tabs/profile-screen/FeedManagementModal.tsx --quiet
```

## ChatGPT Prompt

```text
Please implement Phase 13 of the KIS Feed Channels 200% YouTube roadmap without using git commands. Focus on visible channel creation and channel-scoped feed creation. Add a clear Create Channel button/form in the profile/feed Channel Studio when no channel exists and in the channel selector when channels exist. After creating a channel, refresh and select it. Make the Create feed/content button open the composer inside the selected channel, pass channel_id through the composer payload, and show clear “Create in @channel” UI copy. Preserve old feed APIs and old profile feed behavior for compatibility. Add focused backend/frontend validation, update docs/feed-channels-roadmap/youtube-200-roadmap.md, docs/feed-channels-roadmap/status.md, and docs/BUILD_STATE.md.
```
