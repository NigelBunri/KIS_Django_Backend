# KIS Feed Channels 200% YouTube Roadmap

Status: Phase 14 completed 2026-05-13. Phase 15 is next.

This roadmap continues after Phase 12. The target is not literally “200% YouTube” in one step; the practical target is a **YouTube-class channel system plus KIS-specific support for richer file types, institutions, partners, commerce, education, health, and embeddable content**.

## Current Gap

The current system has strong backend foundations, but the user-facing creation flow is not yet correct:

- The profile/feed workspace does not show a clear **Create Channel** button.
- The “create broadcast feed” flow can still feel general/global instead of being clearly inside a selected channel.
- Users should create or select a channel first, then create channel content/feed posts inside that channel.
- Users should be able to broadcast a single feed item or broadcast/promote a whole channel.
- Channel Studio needs to feel more like YouTube Studio: content table, upload/create flow, dashboard, channel customization, playlists, analytics, comments, moderation, live, and monetization-ready controls.

## Product Direction

KIS Channels should support:

- YouTube-style channels;
- YouTube Studio-style creator workspace;
- videos, shorts, live streams, replays;
- rich text posts, image posts, galleries, documents, audio, links, polls, events;
- channel broadcast/promotion;
- feed-item broadcast/promotion;
- embeds/oEmbed;
- subscriptions and notification bell;
- comments, reactions, saves, watch history;
- playlists/series;
- analytics;
- moderation;
- verification badges;
- organization channels for shops, health institutions, education institutions, and partners.

## Phase 13 - Visible Channel Creation And Channel-Scoped Feed Composer

Purpose: fix the immediate UX gap.

Status: completed 2026-05-13.

Files likely changed:

- Backend:
  - `apps/broadcasts/views.py`
  - `apps/broadcasts/tests.py`
- React Native:
  - `/Users/nigel/dev/KIS/src/screens/broadcast/channels/studio/ChannelStudioScreen.tsx`
  - `/Users/nigel/dev/KIS/src/screens/broadcast/channels/hooks/useChannelsData.ts`
  - `/Users/nigel/dev/KIS/src/screens/broadcast/channels/api/channels.types.ts`
  - `/Users/nigel/dev/KIS/src/screens/broadcast/channels/api/channels.endpoints.ts`
  - `/Users/nigel/dev/KIS/src/components/feeds/composer/FeedComposerSheet.tsx`
  - `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/FeedManagementModal.tsx`

Required changes:

- Add a visible **Create Channel** button when the user has no channel. Completed.
- Add a channel creation form/sheet with:
  - display name;
  - handle;
  - description;
  - category;
  - avatar/banner placeholders;
  - visibility.
- On successful channel creation, refresh `mine=1` channels and select the new channel. Completed.
- Disable/open composer only after a channel is selected. Completed in Channel Studio.
- Pass `selectedChannel.id` into the composer as `channel_id`. Completed.
- Show “Create in @channel” instead of generic “Create feed”. Completed.
- Ensure saved feed entries include channel context when created from Studio. Completed through the composer payload bridge.
- Keep old profile feed creation working for compatibility, but make the new UI channel-first. Completed.

Validation:

- `../env/bin/python manage.py check` passed.
- `../env/bin/python manage.py test apps.broadcasts.tests.BroadcastChannelApiTests.test_user_can_create_own_channel_and_duplicate_handle_fails --noinput --keepdb` passed.
- `cd /Users/nigel/dev/KIS && npm run typecheck -- --pretty false` passed.
- `cd /Users/nigel/dev/KIS && npx eslint src/screens/broadcast/channels/studio/ChannelStudioScreen.tsx src/screens/broadcast/channels/hooks/useChannelsData.ts src/components/feeds/composer/FeedComposerSheet.tsx src/screens/tabs/profile-screen/FeedManagementModal.tsx src/screens/tabs/ProfileScreen.tsx --quiet` passed.

Remaining risk:

- Phase 13 covers personal creator channel creation. Organization channel creation still needs ownership-specific flows.
- Channel broadcast/promotion is not yet implemented.

## Phase 14 - Channel Broadcast And Feed Broadcast Semantics

Purpose: allow both individual content broadcast and whole-channel promotion.

Status: completed 2026-05-13.

Required changes:

- Add backend support for channel broadcast/promotion as a first-class `BroadcastItem` source type or metadata-backed safe bridge. Completed with `broadcast_channel`.
- Add normalized channel-content promotion through `channel_content` broadcast items. Completed.
- Add `broadcast_channel` / `unbroadcast_channel` action endpoints. Completed as `POST/DELETE /api/v1/broadcasts/channels/<channel_id>/broadcast/`.
- Add `broadcast_content` / `unbroadcast_content` action endpoints. Completed as `POST/DELETE /api/v1/broadcasts/channel-contents/<content_id>/broadcast/`.
- Add UI actions:
  - Broadcast this content;
  - Stop broadcasting this content;
  - Broadcast this channel;
  - Stop broadcasting this channel.
- Make the broadcast state visible in Channel Studio. Completed.
- Preserve old feed item broadcast behavior. Completed.
- Add tests for channel broadcast, content broadcast, unbroadcast, idempotency, and feed list output. Completed.

Validation:

- `../env/bin/python manage.py test apps.broadcasts.tests.BroadcastChannelApiTests --noinput --keepdb` passed.
- `../env/bin/python manage.py test apps.broadcasts.tests.ChannelContentCompatibilityTests --noinput --keepdb` passed.
- `../env/bin/python manage.py check` passed.
- `../env/bin/python manage.py makemigrations --check --dry-run broadcasts` passed.
- `cd /Users/nigel/dev/KIS && npm run typecheck -- --pretty false` passed.
- `cd /Users/nigel/dev/KIS && npx eslint src/screens/broadcast/channels/studio/ChannelStudioScreen.tsx src/screens/broadcast/channels/studio/ChannelContentManager.tsx src/screens/broadcast/channels/hooks/useChannelsData.ts src/screens/broadcast/channels/api/channels.endpoints.ts src/screens/broadcast/channels/api/channels.types.ts src/network/routes/broadcastRoutes.ts --quiet` passed.

Remaining risk:

- Channel/content broadcast is now a promotion layer over public broadcast feed distribution. Full YouTube Studio table filtering/editing/bulk status control remains Phase 15.
- Real device visual QA was not run in this implementation session.

## Phase 15 - YouTube Studio-Style Content Manager

Purpose: replace placeholder creator content lists with a real operational table/list.

Required changes:

- Content filters: Draft, Scheduled, Published, Archived, Live, Shorts, Posts, Documents.
- Search by title/text.
- Bulk actions where safe:
  - publish;
  - unpublish;
  - archive;
  - add to playlist.
- Per-item status chips, thumbnail, visibility, date, views, comments.
- Clear empty states and loading states.
- Edit existing channel content from the content manager.

## Phase 16 - Upload And Media Processing Pipeline

Purpose: move from metadata-only assets toward production media behavior.

Required changes:

- Direct uploads into private media first.
- Malware scan/quarantine hook.
- Video processing status:
  - pending;
  - processing;
  - ready;
  - failed.
- Thumbnail extraction/generation hooks.
- Video duration/width/height metadata.
- File size and MIME validation per content type.
- Frontend upload queue with retry and progress.

## Phase 17 - Real Video Player, Shorts, And Watch Experience

Purpose: make the viewer experience feel closer to YouTube.

Required changes:

- Dedicated watch page for videos and replays.
- Vertical swipe Shorts viewer.
- Continue watching/watch history resume.
- Recommended next content rail.
- Playlist autoplay.
- Fullscreen controls.
- Loading/error/fallback states.
- View count rules and anti-spam guard.

## Phase 18 - Real Live Streaming Provider Integration

Purpose: move live from placeholder to provider-backed staging.

Required changes:

- Choose provider: Mux, Cloudflare Stream, AWS IVS, Agora, or equivalent.
- Staging-only provider enablement flag.
- Create live stream session.
- Return masked ingest details.
- Webhook validation.
- Live playback URL.
- Live status updates.
- Replay/VOD connection.
- Live chat/moderation bridge.

## Phase 19 - Playlists, Series, Sections, And Channel Home Customization

Purpose: make channel home feel like a real creator homepage.

Required changes:

- Create/edit/delete playlists.
- Add/remove/reorder content.
- Channel home sections:
  - featured video;
  - latest uploads;
  - popular uploads;
  - shorts;
  - live/replays;
  - custom playlists.
- Branding editor:
  - banner crop;
  - avatar;
  - accent color;
  - links.

## Phase 20 - Discovery, Search, Recommendations, And Ranking

Purpose: improve the public feed beyond simple listing.

Required changes:

- Search channels and content.
- Ranking inputs:
  - freshness;
  - engagement;
  - watch history;
  - subscriptions;
  - muted/hidden content;
  - country/language/category.
- Trending channels/content.
- Recommended channels.
- “Because you watched” rails.
- Avoid showing hidden/muted content.

## Phase 21 - Comments, Community Safety, And Creator Moderation

Purpose: make comments and moderation YouTube-class.

Required changes:

- Threaded replies.
- Pin creator comment.
- Heart/like comments.
- Hold potentially unsafe comments for review.
- Block words list.
- Channel moderator roles.
- Report queue.
- User mute/block at channel level.
- Appeal/review notes.

## Phase 22 - Analytics And Creator Insights

Purpose: make Studio analytics useful.

Required changes:

- Views over time.
- Watch time.
- Average view duration.
- Subscribers gained/lost.
- Traffic source:
  - home;
  - search;
  - embed;
  - channel page;
  - notifications.
- Top content.
- Audience geography/language where safe.
- Live peak viewers.
- Export CSV.

## Phase 23 - Monetization-Ready But Legally Safe Controls

Purpose: prepare without accidentally creating risky financial behavior.

Required changes:

- No coin-as-money behavior.
- USD-only paid features through approved provider.
- Optional paid promotions only after financial/legal sign-off.
- Creator upgrade plans through Flutterwave/direct USD.
- Clear receipts, refunds, tax/legal notes.

## Phase 24 - Production Launch Hardening

Purpose: final launch evidence.

Required changes:

- Staging migrations.
- Staging backfill dry-run and approved apply.
- iOS manual QA.
- Android manual QA.
- Production flags:
  - embeds;
  - live provider;
  - notifications;
  - media processing.
- Load/performance checks.
- Rollback drill.
- Monitoring and alerting.

## Immediate Best Next Prompt

```text
Please implement Phase 15 of the KIS Feed Channels 200% YouTube roadmap without using git commands. Focus on a YouTube Studio-style content manager. Replace the simple placeholder list with a real operational Channel Studio content table/list: filters for Draft, Scheduled, Published, Archived, Live, Shorts, Posts, Documents; search by title/text; status chips, thumbnail, visibility, date, views, comments, broadcast state, and per-item actions for edit, publish/unpublish, broadcast/unbroadcast, archive, and add to playlist where safe. Preserve legacy feed compatibility and existing APIs, add focused backend/frontend validation, and update docs/feed-channels-roadmap/youtube-200-roadmap.md, docs/feed-channels-roadmap/status.md, and docs/BUILD_STATE.md.
```
