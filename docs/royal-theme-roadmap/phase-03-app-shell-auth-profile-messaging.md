# Phase 03 - App Shell Auth Profile Messaging

## Goal

Apply the royal theme to the highest-frequency user surfaces.

## Files To Inspect First

- `/Users/nigel/dev/KIS/App.tsx`
- `/Users/nigel/dev/KIS/src/screens/LoginScreen.tsx`
- `/Users/nigel/dev/KIS/src/screens/RegisterScreen.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/ProfileScreen.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/profile.styles.ts`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/profile.constants.ts`
- `/Users/nigel/dev/KIS/src/screens/MessagesScreen.tsx`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom`
- `/Users/nigel/dev/KIS/src/components/messages`

## Required Work

- Remove orange accents from shell/auth/profile/messaging.
- Ensure light theme panels are not dark or confusing.
- Update chat bubbles through theme tokens:
  - outgoing = gold-tinted;
  - incoming = purple-tinted;
  - read/presence = purple/gold hierarchy.
- Keep message reliability and E2EE behavior untouched.

## Validation

- `cd /Users/nigel/dev/KIS && npm run typecheck -- --pretty false`
- Focused lint for touched files.
- Manual QA: login, register, profile dashboard, message list, direct chat room.

## Best Prompt For Phase 04

```text
Please implement Phase 04 of the KIS Royal Gold + Purple theme roadmap without using git commands. Focus on Broadcast, Channels, Feed Studio, and feed detail surfaces. Replace local hard-coded gold/beige/orange values with centralized royal tokens, harmonize channel discovery, channel home, channel content detail, Broadcast tabs, FeedManagementModal, and Channel Studio into one luxurious gold + purple system, preserve all Phase 13/14 channel behavior, run focused typecheck/lint, document blockers, and update docs/royal-theme-roadmap/status.md and docs/BUILD_STATE.md.
```
