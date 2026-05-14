# Phase 06 - Creator Studio And Composer

Purpose: upgrade the feed workspace into a YouTube Studio-style Channel Studio while preserving the existing feed manager.

## Files To Change

- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/FeedManagementModal.tsx`
- `/Users/nigel/dev/KIS/src/components/feeds/composer/FeedComposerSheet.tsx`
- `/Users/nigel/dev/KIS/src/components/feeds/composer/pages/TextComposerPage.tsx`
- `/Users/nigel/dev/KIS/src/components/feeds/composer/pages/MediaComposerPage.tsx`
- `/Users/nigel/dev/KIS/src/components/feeds/composer/pages/LinkComposerPage.tsx`
- `/Users/nigel/dev/KIS/src/components/feeds/composer/pages/PollComposerPage.tsx`
- `/Users/nigel/dev/KIS/src/components/feeds/composer/pages/EventComposerPage.tsx`
- New: `/Users/nigel/dev/KIS/src/screens/broadcast/channels/studio/ChannelStudioScreen.tsx`
- New: `/Users/nigel/dev/KIS/src/screens/broadcast/channels/studio/ChannelContentManager.tsx`
- New: `/Users/nigel/dev/KIS/src/screens/broadcast/channels/studio/ChannelBrandingEditor.tsx`
- New: `/Users/nigel/dev/KIS/src/screens/broadcast/channels/studio/ChannelAnalyticsPanel.tsx`
- `apps/broadcasts/views.py`
- `docs/feed-channels-roadmap/status.md`

## Studio Sections

Build:

- Dashboard: latest performance, subscriber count, recent content.
- Content: table/list of Draft, Scheduled, Published, Archived.
- Create: opens existing composer with new channel fields.
- Branding: avatar, banner, handle, description, links.
- Playlists: create/edit/reorder.
- Live: scheduled streams placeholder until Phase 07.
- Analytics: cards for views, impressions, watch time placeholders.
- Settings: channel visibility, comments, embed allowlist.

## Composer Payload

Extend composer submit payload to include:

```ts
channel_id
content_type
visibility
scheduled_at
playlist_ids
thumbnail
captions
embed_allowed
```

Keep old feed payload fields:

- `media_type`
- `text_doc`
- `text_plain`
- `attachments`
- `media_options`

## Backend

In `apps/broadcasts/views.py`, ensure `POST /channels/<id>/contents/` accepts the extended composer payload and stores it in `ChannelContent` plus `ChannelContentAsset`.

## Design Requirements

- Make the workspace feel premium and professional.
- Use dense, operational UI like YouTube Studio, not a marketing landing page.
- No oversized decorative hero.
- Avoid nested cards.
- Ensure Create button is not covered by bottom safe area.

## Validation

```bash
cd /Users/nigel/dev/KIS
npx eslint src/screens/broadcast/channels/studio src/screens/tabs/profile-screen/FeedManagementModal.tsx src/components/feeds/composer --quiet
npm run typecheck
python3 manage.py check
```

## ChatGPT Prompt

```text
Please implement Phase 06 of KIS Feed Channels without using git commands. Upgrade the existing feed workspace into a Channel Studio with dashboard, content manager, composer integration, branding editor, analytics placeholders, playlists, live placeholder, and settings. Preserve the existing FeedManagementModal behavior and old feed payloads while adding channel_id/content_type/visibility/scheduled_at/thumbnail/embed fields. Update status docs.
```

