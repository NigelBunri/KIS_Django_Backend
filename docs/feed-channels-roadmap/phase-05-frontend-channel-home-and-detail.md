# Phase 05 - Frontend Channel Home And Content Detail

Purpose: build the YouTube-style channel home page and channel-aware content detail viewer.

## Files To Change

- New: `/Users/nigel/dev/KIS/src/screens/broadcast/channels/ChannelHomePage.tsx`
- New: `/Users/nigel/dev/KIS/src/screens/broadcast/channels/ChannelContentDetailPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/api/channels.types.ts`
- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/hooks/useChannelsData.ts`
- `/Users/nigel/dev/KIS/src/screens/tabs/feeds/BroadcastDetailScreen.tsx`
- `/Users/nigel/dev/KIS/src/components/broadcast/BroadcastFeedCard.tsx`
- `/Users/nigel/dev/KIS/src/components/broadcast/FeedItemCard.tsx`
- `/Users/nigel/dev/KIS/src/components/feeds/RichTextRenderer.tsx`
- `docs/feed-channels-roadmap/status.md`

## Channel Home Layout

Build a full page:

1. Banner
   - full-width image with stable aspect ratio.
   - fallback premium color band if no banner.
2. Identity row
   - avatar overlapping banner bottom edge;
   - display name, handle, verification badge;
   - subscriber/content count;
   - subscribe button and bell.
3. Tabs
   - Home, Videos, Shorts, Posts, Live, Playlists, About.
4. Home content
   - Featured item hero;
   - Latest uploads horizontal row;
   - Shorts vertical cards row;
   - Live/scheduled row;
   - Playlists row;
   - About snippet.

## Detail Viewer

`ChannelContentDetailPage.tsx` should support:

- video/short video: full-bleed player area, title, channel attribution, actions, comments preview;
- image/gallery: large swipeable media;
- text/rich_text: use `RichTextRenderer`, preserve composer styling;
- audio: album-style cover, player controls placeholder;
- document: preview card and open/download action;
- live_stream: waiting/live/replay state.

Do not break existing `BroadcastDetailScreen.tsx`. Add a compatibility wrapper:

- If item has `channel_content_id`, navigate to `ChannelContentDetailPage`.
- Else keep current broadcast detail behavior.

## Actions

Add visible buttons:

- Like/reaction
- Comment
- Share
- Save
- Embed
- Report

`Embed` can show a disabled/coming-soon state until Phase 08.

## Validation

```bash
cd /Users/nigel/dev/KIS
npx eslint src/screens/broadcast/channels src/screens/tabs/feeds/BroadcastDetailScreen.tsx src/components/broadcast/BroadcastFeedCard.tsx src/components/broadcast/FeedItemCard.tsx --quiet
npm run typecheck
```

## ChatGPT Prompt

```text
Please implement Phase 05 of KIS Feed Channels in React Native without using git commands. Add ChannelHomePage and ChannelContentDetailPage with YouTube-style channel layout and multi-file-type detail rendering. Preserve existing BroadcastDetailScreen behavior for legacy feed items. Add subscribe/bell/action UI placeholders where backend actions are not ready. Update status docs.
```

