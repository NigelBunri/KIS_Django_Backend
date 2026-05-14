# KIS Channels Product Spec

Phase 00 status: completed.

KIS Channels upgrades the existing broadcast feed system into a YouTube-style channel platform that supports more content types than YouTube: videos, short videos, rich text, images, galleries, audio, documents, links, polls, events, live streams, and replays.

The system must preserve all existing broadcast feed behavior while gradually adding normalized channel/content models, channel APIs, creator studio, live streaming, embeds, moderation, analytics, and launch QA.

## Product Principles

- Channels are creator or organization homes for public content.
- The current feed system remains compatible until migration/backfill is complete.
- Public channel/content APIs must not expose private owner data, raw storage paths, private media, or internal metadata.
- New public UX should feel familiar to users who understand YouTube channels.
- KIS-specific content types should feel native, not bolted on.
- Live provider calls, embeds, and monetization-like behavior must be feature-flagged and production-safe.
- Paid/monetized channel features are out of scope until the financial redesign has production sign-off.

## Channel Identity

Each channel should have:

- `id`: stable UUID.
- `handle`: unique public handle, case-insensitive, URL-safe, for example `@kishealth`.
- `display_name`: visible channel name.
- `owner_type`: `user`, `shop`, `health`, `education`, or `partner`.
- `owner_id`: UUID of the owning subject.
- `owner_user`: optional user owner for personal channels.
- `avatar_url`: channel avatar.
- `banner_url`: channel cover/banner.
- `description`: about text.
- `country`: optional country code.
- `language`: preferred language.
- `category`: primary channel category.
- `links`: approved public links.
- `branding`: JSON for theme, accent, featured layout, and banner crop metadata.
- `verification_badges`: public badge list from the verification system.
- `is_verified`: quick public verified flag.
- `is_public`: visibility flag.
- `subscriber_count`: denormalized count.
- `content_count`: denormalized public/published count.

## Channel Ownership And Roles

Roles:

- `owner`: full control, transfer/delete/channel settings.
- `manager`: channel settings, content, live, moderation, analytics.
- `editor`: create/edit/publish content.
- `moderator`: manage comments/reports, cannot edit content.
- `analyst`: view analytics only.

Rules:

- Phase 01 should support personal user channels first.
- Organization channels should be model-ready from Phase 01, but can be progressively wired.
- Every write action must check ownership or channel role.
- Staff can inspect and moderate but should not silently become channel owner.

## Channel Tabs

Public channel home tabs:

- `Home`: featured content, latest uploads, live/scheduled, playlists, about snippet.
- `Videos`: long-form videos.
- `Shorts`: short videos.
- `Posts`: text, rich text, image, poll, link, document-style posts.
- `Live`: live now, scheduled streams, replays.
- `Playlists`: curated collections.
- `About`: description, links, metadata, report action.

KIS optional tab filters:

- `Documents`
- `Audio`
- `Events`
- `Education`
- `Market`
- `Health`
- `Partners`

These can be filters inside the content tabs instead of permanent top-level tabs if the screen becomes crowded.

## Content Types

Supported `content_type` values:

- `video`: long-form video.
- `short_video`: vertical short video.
- `image`: single image.
- `gallery`: multiple images.
- `text`: plain text post.
- `rich_text`: styled text from the existing composer.
- `audio`: audio with cover art.
- `document`: PDF/doc/file attachment.
- `link`: external link preview.
- `poll`: poll with options and results.
- `event`: scheduled event.
- `live_stream`: active or scheduled live stream.
- `replay`: archived live stream/VOD.

Content fields:

- `id`
- `channel`
- `legacy_broadcast_item`
- `legacy_feed_entry_id`
- `content_type`
- `title`
- `description`
- `text_plain`
- `text_doc`
- `thumbnail_url`
- `visibility`: `public`, `unlisted`, `private`
- `status`: `draft`, `scheduled`, `published`, `processing`, `failed`, `archived`
- `published_at`
- `scheduled_at`
- `duration_seconds`
- `metadata`
- `stats`
- `is_deleted`
- `created_by`

Asset fields:

- `asset_type`
- `url`
- `storage_path`
- `mime_type`
- `size_bytes`
- `width`
- `height`
- `duration_seconds`
- `thumbnail_url`
- `caption`
- `sort_order`
- `processing_status`
- `metadata`

## Viewer Actions

Viewers should be able to:

- subscribe/unsubscribe;
- choose notification level: none, personalized, all;
- react/like;
- comment;
- share;
- save;
- report content;
- hide one content item;
- mute a channel;
- open channel profile;
- open author/profile preview;
- copy embed code where allowed.

Rules:

- Share count should increment only after the native share flow reports completion where possible.
- Hide affects only the selected content item for that viewer.
- Mute affects all content from that channel for that viewer.
- Report creates admin-visible moderation records.

## Creator Actions

Creators should be able to:

- create content;
- save draft;
- edit content;
- upload/replace assets;
- upload thumbnail;
- schedule publish;
- publish now;
- unpublish;
- archive/delete;
- pin content;
- feature content on channel home;
- create playlists;
- add/remove/reorder playlist items;
- schedule live stream;
- start/end live stream when provider is available;
- view analytics;
- moderate comments;
- manage channel branding;
- manage embed policy.

## Creator Studio

Channel Studio should feel like a practical creator operations tool, not a marketing landing page.

Sections:

- Dashboard
  - recent performance;
  - subscriber count;
  - latest content;
  - live/scheduled status;
  - moderation alerts.
- Content
  - Draft, Scheduled, Published, Archived filters;
  - content type filter;
  - search;
  - quick actions.
- Create
  - existing advanced composer with channel fields.
- Branding
  - avatar, banner, handle, display name, description, links.
- Playlists
  - create/edit/reorder.
- Live
  - schedule stream;
  - stream key/ingest placeholder;
  - stream health;
  - chat/moderation placeholder.
- Analytics
  - views, impressions, watch time, subscribers, shares, saves, embeds, live peak.
- Moderation
  - reported content/comments;
  - hide/remove/keep actions.
- Settings
  - visibility, comments, embeds, allowed domains.

## Live Streaming

Phase 07 should add provider-neutral live structure before real production provider calls.

Required states:

- `scheduled`
- `live`
- `ended`
- `cancelled`
- `failed`

Live features:

- scheduled waiting page;
- title/description/thumbnail;
- stream provider reference;
- ingest URL;
- stream key hash only, never raw key;
- playback URL;
- viewer count;
- peak viewer count;
- live chat placeholder or bridge;
- moderation controls;
- replay/VOD URL after stream ends.

Provider decision:

- Default for implementation: `LIVE_STREAM_PROVIDER=disabled`.
- Staging/prod provider should be selected later from Mux, Cloudflare Stream, Agora, AWS IVS, or another approved provider.
- Do not make live provider calls unless explicit staging flags and secrets are configured.

## Embeds

KIS should support approved public embed players/cards for external websites and apps.

Embed types:

- full player iframe for videos/live/replays;
- content card embed for text/image/document/link/poll/event;
- oEmbed metadata endpoint.

Embed policy:

- public content can be embedded only if channel policy allows embeds.
- unlisted/private embeds require signed short-lived token.
- allowed domains and blocked domains should be supported.
- embed endpoints should rate-limit.
- embed impressions should be tracked as engagement events.

Embed must not expose:

- private owner data;
- raw storage paths;
- raw signed tokens;
- private media URLs without signed access;
- internal metadata.

## Moderation And Safety

Moderation actions:

- report channel;
- report content;
- report comment;
- hide content;
- mute channel;
- block user/creator where applicable;
- staff review;
- creator comment moderation;
- audit log all high-risk actions.

Safety fields:

- sensitive/age restriction metadata;
- comment policy;
- embed policy;
- live chat policy;
- takedown status;
- copyright/report metadata placeholder.

Admin must be able to inspect:

- channels;
- content;
- reports;
- moderation actions;
- embed policy;
- live stream status;
- audit events.

## Analytics

Content analytics:

- views;
- unique viewers;
- impressions;
- click-through rate;
- watch time;
- average view duration;
- reaction count;
- comment count;
- share count;
- save count;
- embed impressions;
- external referrers;
- live peak viewers.

Channel analytics:

- subscribers gained/lost;
- total views;
- total watch time;
- top content;
- traffic source;
- embed usage;
- live performance;
- audience geography/language where safely available.

Implementation approach:

- keep durable raw engagement events;
- add daily rollups later;
- avoid expensive real-time analytics in early phases;
- expose conservative stats in public APIs.

## Notifications

Notification events:

- subscribed channel publishes content;
- scheduled live starting soon;
- live stream started;
- creator receives comment/report/moderation notice;
- user receives reply or comment mention if comments support it.

Notification preferences:

- no notifications;
- personalized;
- all.

All push/in-app notification work should use the centralized notifications system where available.

## API Compatibility

Existing endpoints must keep working:

- `/api/v1/broadcasts/`
- `/api/v1/broadcasts/profiles/feeds/`
- `/api/v1/broadcasts/profiles/feeds/<entry_id>/`
- `/api/v1/broadcasts/profiles/feeds/<entry_id>/broadcast/`
- `/api/v1/broadcasts/profiles/feeds/<entry_id>/unbroadcast/`
- `/api/v1/broadcasts/<broadcast_id>/share/`
- `/api/v1/broadcasts/<broadcast_id>/view/`
- existing hide/report/reaction paths.

New APIs should add channel capability without changing old response shapes until Phase 11 migration/backfill is complete.

## Migration Strategy

1. Add channel models.
2. Add normalized content models.
3. Sync new content rows when old feed entries are created/edited/broadcast.
4. Add channel APIs.
5. Add frontend channel views.
6. Add studio/live/embed/analytics features.
7. Backfill old JSON feed entries into channels.
8. Keep old APIs as compatibility wrappers.
9. Only after successful QA, gradually make channel content the primary source of truth.

## Design Direction

Discovery page:

- professional light-theme layout;
- search row and tabs aligned;
- featured channels/content rails;
- live-now strip;
- no nested cards;
- stable dimensions;
- compact but premium spacing.

Channel home:

- banner first;
- avatar overlaps banner;
- handle/verified/subscriber count visible;
- subscribe and bell actions close to identity;
- tabs below identity;
- content rails with see-all behavior.

Content detail:

- video/shorts get immersive full-screen treatment;
- text/rich text preserves composer style;
- document/audio/image each has a native-feeling layout;
- actions should be easy to reach;
- comments should not cover primary content unintentionally.

Creator Studio:

- operational dashboard;
- dense but organized;
- no marketing hero;
- clear content table/list;
- strong empty states;
- safe-area-aware create button.

## Phase Decisions

Decisions made for implementation:

- Start with personal user channels, but model `owner_type` for shops, health, education, and partners from the beginning.
- Handles are unique, slug-like, and case-insensitive.
- Embeds are public-only first; signed private/unlisted embeds come behind flags.
- Live streaming is provider-neutral first and disabled by default.
- Monetization is out of scope for this roadmap.
- Existing feed JSON compatibility remains until Phase 11.
- Channel comments can initially bridge to the existing comment/chat system if direct comment tables are not safe yet.

Open decisions for later:

- final live streaming provider;
- exact public channel URL format;
- whether every user automatically gets a channel or creates one manually;
- embed allowed-domain policy defaults;
- whether organization channels require verification before public publishing;
- comment storage source of truth if Nest remains involved.

## Phase 00 Completion Criteria

Completed:

- Product scope defined.
- Channel identity defined.
- Content types defined.
- Viewer and creator actions defined.
- Live streaming scope defined.
- Embed scope defined.
- Moderation and analytics scope defined.
- Compatibility strategy defined.
- Phase decisions recorded.

Next phase:

- `docs/feed-channels-roadmap/phase-01-backend-channel-models.md`

