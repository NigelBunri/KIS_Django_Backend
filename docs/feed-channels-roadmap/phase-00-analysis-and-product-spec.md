# Phase 00 - Analysis And Product Spec

Purpose: create the final product specification before writing code. This prevents random UI/backend changes and keeps later ChatGPT sessions aligned.

## Files To Read

Backend:

- `apps/broadcasts/models.py`
- `apps/broadcasts/views.py`
- `apps/broadcasts/serializers.py`
- `apps/broadcasts/urls.py`
- `apps/broadcasts/feed_entry_store.py`
- `docs/broadcast-feeds-progress.md`

Frontend:

- `/Users/nigel/dev/KIS/src/screens/broadcast/feeds/FeedsDiscoverPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/feeds/hooks/useFeedsData.ts`
- `/Users/nigel/dev/KIS/src/screens/broadcast/feeds/api/feeds.types.ts`
- `/Users/nigel/dev/KIS/src/screens/tabs/feeds/BroadcastDetailScreen.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/FeedManagementModal.tsx`
- `/Users/nigel/dev/KIS/src/components/feeds/composer/FeedComposerSheet.tsx`

## Product Specification To Write

Create `docs/feed-channels-roadmap/product-spec.md` with these sections:

1. Channel identity
   - channel id, handle, display name, avatar, banner, owner type, verification badge, category, country, language, links, about text.
2. Channel tabs
   - Home, Videos, Shorts, Posts, Live, Playlists, About.
   - KIS-specific additions: Documents, Audio, Polls, Events, Marketplace/Education/Health links only if source supports them.
3. Content types
   - `video`, `short_video`, `image`, `gallery`, `text`, `rich_text`, `audio`, `document`, `link`, `poll`, `event`, `live_stream`, `replay`.
4. Viewer actions
   - subscribe, notification bell, like/reaction, comment, share, save, report, hide post, mute channel.
5. Creator actions
   - create, edit, publish, schedule, unpublish, delete, playlist add/remove, pin, feature on home, upload thumbnail, captions/transcripts, live schedule/start/end.
6. Embed actions
   - public embed player/card, copy embed code, allowed domains, signed content token for private/unlisted content.
7. Live streaming
   - scheduled live, waiting room, stream key, ingest URL, viewer count, live chat, moderation, replay/VOD.
8. Safety
   - channel ownership, staff roles, moderation, private media, copyright/reporting hooks, age/sensitive flags.
9. Analytics
   - views, impressions, watch time, average view duration, subscribers, shares, external embed impressions, live peak viewers.

## Decisions Needed Before Phase 01

Record these in `docs/feed-channels-roadmap/product-spec.md`:

- Live provider: use Mux, Cloudflare Stream, Agora, AWS IVS, or custom RTMP later.
- Public channel handles: allow one handle per channel, case-insensitive unique.
- Channel ownership: user channels first, then organization channels later; or all subject types from day one.
- Embed policy: public only at first; private/unlisted signed embeds later.
- Monetization: out of scope until financial compliance is finished.

## ChatGPT Prompt For This Phase

```text
I am building KIS Channels from an existing Django + React Native broadcast feed system. Please help me create `docs/feed-channels-roadmap/product-spec.md` only. Do not write application code. Use the phase document below as the source of truth. I need a YouTube-style channel product spec that supports video, shorts, image, rich text, audio, documents, polls, events, live streaming, playlists, embeds, moderation, analytics, and creator studio. Keep backward compatibility with existing broadcast feeds.
```

