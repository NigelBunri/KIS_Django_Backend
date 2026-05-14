# Phase 04 - Frontend Channel Discovery

Purpose: add the React Native channel discovery layer while keeping the current feed page working.

## Files To Change

- `/Users/nigel/dev/KIS/src/network/routes/broadcastRoutes.ts`
- `/Users/nigel/dev/KIS/src/screens/broadcast/feeds/api/feeds.types.ts`
- New: `/Users/nigel/dev/KIS/src/screens/broadcast/channels/api/channels.endpoints.ts`
- New: `/Users/nigel/dev/KIS/src/screens/broadcast/channels/api/channels.types.ts`
- New: `/Users/nigel/dev/KIS/src/screens/broadcast/channels/hooks/useChannelsData.ts`
- New: `/Users/nigel/dev/KIS/src/screens/broadcast/channels/ChannelsDiscoverPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/BroadcastScreen.tsx`
- `/Users/nigel/dev/KIS/src/components/broadcast/BroadcastMainTabs.tsx`
- `docs/feed-channels-roadmap/status.md`
- `docs/BUILD_STATE.md`

## API Types

Create `channels.types.ts`:

```ts
export type BroadcastChannelSummary = {
  id: string;
  handle: string;
  display_name: string;
  description?: string;
  avatar_url?: string;
  banner_url?: string;
  category?: string;
  is_verified?: boolean;
  verification_badges?: string[];
  subscriber_count?: number;
  content_count?: number;
  is_subscribed?: boolean;
};

export type BroadcastChannelContent = {
  id: string;
  channel?: BroadcastChannelSummary;
  content_type: string;
  title?: string;
  description?: string;
  text_plain?: string;
  thumbnail_url?: string;
  assets?: any[];
  status?: string;
  visibility?: string;
  published_at?: string;
  duration_seconds?: number;
  stats?: Record<string, any>;
};
```

## Discovery Page Design

`ChannelsDiscoverPage.tsx` should include:

- top search row aligned with existing broadcast search;
- horizontal category pills: All, Video, Shorts, Live, Music/Audio, Documents, Education, Market, Health, Partners;
- featured channels carousel with banner thumbnails;
- continue watching/latest content section;
- live now strip;
- recommended channels list;
- no nested cards inside cards;
- light theme, luxury/professional styling, 8px radius max for cards unless existing system differs;
- skeleton/loading and empty states.

## Integration

In `BroadcastScreen.tsx`, add a `Channels` tab next to feeds. Do not remove existing Feeds/Education/Market/Health tabs.

If `BroadcastMainTabs.tsx` has a hard-coded tab list, add:

```ts
{ key: 'channels', label: 'Channels' }
```

Route `channels` to `ChannelsDiscoverPage`.

## Data Hook

`useChannelsData.ts` should:

- call `GET /api/v1/broadcasts/channels/`;
- support `q`, `category`, `cursor`;
- expose `channels`, `loading`, `refreshing`, `loadMore`, `refresh`;
- never crash if backend endpoint is not deployed; show empty state.

## Validation

```bash
cd /Users/nigel/dev/KIS
npx eslint src/screens/broadcast/channels src/screens/tabs/BroadcastScreen.tsx src/components/broadcast/BroadcastMainTabs.tsx --quiet
npm run typecheck
```

## ChatGPT Prompt

```text
Please implement Phase 04 of KIS Feed Channels in the React Native app without using git commands. Add channel discovery API types/endpoints/hooks and a luxury ChannelsDiscoverPage. Integrate it into the existing Broadcast tabs without breaking Feeds/Education/Market/Health. Keep UI aligned, light-theme professional, and no broad redesign outside the broadcast tab shell. Update roadmap status docs.
```

