# KIS Feed Channels Roadmap

Goal: evolve the current broadcast feed system into a YouTube-style channel system for many content types: video, short video, image, text, rich text, audio, documents, links, polls, events, paid/live content where approved, and embeddable public players/cards.

This roadmap is written for low-usage handoff. Each phase is a standalone document that can be pasted into normal ChatGPT with the exact files and constraints for that page of work.

Do not use git commands for this project unless Nigel explicitly asks.

## Current System Shape

Backend:

- Django app: `apps/broadcasts`
- Main models: `apps/broadcasts/models.py`
- Main API file: `apps/broadcasts/views.py`
- Main serializers: `apps/broadcasts/serializers.py`
- Feed JSON compatibility helper: `apps/broadcasts/feed_entry_store.py`
- Feed URLs: `apps/broadcasts/urls.py`
- Feed media rules/helpers: `apps/broadcasts/media_utils.py` and feed helper functions inside `apps/broadcasts/views.py`
- Existing feed progress: `docs/broadcast-feeds-progress.md`

Frontend:

- Broadcast main tab: `/Users/nigel/dev/KIS/src/screens/tabs/BroadcastScreen.tsx`
- Feed discovery page: `/Users/nigel/dev/KIS/src/screens/broadcast/feeds/FeedsDiscoverPage.tsx`
- Feed data hook: `/Users/nigel/dev/KIS/src/screens/broadcast/feeds/hooks/useFeedsData.ts`
- Feed API types: `/Users/nigel/dev/KIS/src/screens/broadcast/feeds/api/feeds.types.ts`
- Feed endpoints: `/Users/nigel/dev/KIS/src/screens/broadcast/feeds/api/feeds.endpoints.ts`
- Feed cards/components: `/Users/nigel/dev/KIS/src/components/broadcast/*`
- Feed detail experience: `/Users/nigel/dev/KIS/src/screens/tabs/feeds/BroadcastDetailScreen.tsx`
- Existing composer: `/Users/nigel/dev/KIS/src/components/feeds/composer/FeedComposerSheet.tsx`
- Profile feed manager: `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/FeedManagementModal.tsx`

## Target Product

KIS Channels should feel familiar to a YouTube user, but support more file types:

- Channel homepage with banner, avatar, handle, verification badge, subscribe button, notification bell, tabs, featured content, playlists/collections, community posts, live/scheduled streams, about page, links, contact, and policy/safety state.
- Channel Studio for creators: dashboard, content manager, upload/composer, live control room, analytics, comments, playlists, monetization/readiness, embeds, settings, moderation, branding.
- Content item pages: full-screen immersive viewer for feeds; video/audio/document/image/text support; comments; share; save; embed; report; channel attribution.
- Live streaming: scheduled live events, RTMP/WebRTC provider integration path, live chat, replay/VOD, moderation, viewer counts, stream health.
- Embeds: public read-only embed pages and API tokens that allow approved content to appear in external websites/apps without exposing private data or unsafe JS.
- Security and compliance: ownership checks, private media separation, moderation, audit logs, signed embeds/live callbacks, rate limiting.

## Phase Order

1. `phase-00-analysis-and-product-spec.md`
2. `phase-01-backend-channel-models.md`
3. `phase-02-backend-normalized-content.md`
4. `phase-03-backend-channel-apis.md`
5. `phase-04-frontend-channel-discovery.md`
6. `phase-05-frontend-channel-home-and-detail.md`
7. `phase-06-creator-studio-and-composer.md`
8. `phase-07-live-streaming-foundation.md`
9. `phase-08-embeds-public-player.md`
10. `phase-09-engagement-comments-playlists.md`
11. `phase-10-moderation-analytics-notifications.md`
12. `phase-11-migration-backfill-compatibility.md`
13. `phase-12-qa-launch-runbook.md`

Each phase should update:

- `docs/feed-channels-roadmap/status.md`
- `docs/BUILD_STATE.md`

## Golden Rules For ChatGPT Sessions

- Paste only the phase file for the phase you are doing.
- If ChatGPT asks for code, first paste the specific file section named in the phase. Do not paste the whole project.
- Ask ChatGPT to return a patch or exact replacement blocks, not vague instructions.
- After applying code, run the validation commands in the phase.
- If a check is blocked, record the command and blocker in `status.md`, then move on.
- Do not destructively rename existing JSON/feed fields until Phase 11 migration confirms backward compatibility.

