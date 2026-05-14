# Phase 04 - Broadcast Channels Feed Studio

## Goal

Make Broadcast, Channels, and Feed Studio feel fully royal and consistent.

## Files To Inspect First

- `/Users/nigel/dev/KIS/src/screens/tabs/BroadcastScreen.tsx`
- `/Users/nigel/dev/KIS/src/components/broadcast/BroadcastMainTabs.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/ChannelsDiscoverPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/ChannelHomePage.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/ChannelContentDetailPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/studio`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/FeedManagementModal.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/feeds`

## Required Work

- Replace local hard-coded gold/beige values with Phase 01 tokens.
- Keep channel creation, create-in-channel, and broadcast/unbroadcast behavior.
- Make channel cards, studio cards, feed cards, and detail overlays share one gold + purple language.
- Avoid beige-only screens; use purple depth and gold accents.

## Validation

- `cd /Users/nigel/dev/KIS && npm run typecheck -- --pretty false`
- `cd /Users/nigel/dev/KIS && npx eslint src/screens/broadcast src/components/broadcast src/screens/tabs/profile-screen/FeedManagementModal.tsx src/screens/tabs/feeds --quiet`

## Best Prompt For Phase 05

```text
Please implement Phase 05 of the KIS Royal Gold + Purple theme roadmap without using git commands. Focus on commerce, wallet/billing, education, health, and partner surfaces. Replace orange and mismatched hard-coded colors with centralized royal tokens, keep USD/payment and verification behavior unchanged, preserve health clarity while using gold + purple accents, run focused typecheck/lint, document blockers, and update docs/royal-theme-roadmap/status.md and docs/BUILD_STATE.md.
```
