# Phase 11 - Advanced Calls Screen Share Call Links

Purpose: add WhatsApp/Telegram-grade call capabilities after the basic call flow is reliable.

## Files To Inspect First

Frontend:

- call overlay/components found by:
  - `rg -n "CallOverlay|incomingCall|startCall|screenShare|callLink|call.offer|call.answer|call.ice" /Users/nigel/dev/KIS/src /Users/nigel/dev/KIS/SocketProvider.tsx -S`
- `/Users/nigel/dev/KIS/src/screens/tabs/MesssagingSubTabs/CallsTab.tsx`

Nest:

- `src/chat/features/calls/`
- `src/realtime/chat.gateway.ts`
- `src/realtime/handlers/`
- `src/notifications/notifications.service.ts`

## Required Features

### 1. Group calls

Add support for:

- participants list;
- join/leave;
- active speaker;
- muted/camera-off state;
- missed group call history.

### 2. Screen sharing

Add:

- UI button;
- permission handling;
- signaling field for screen track;
- fallback if platform does not support.

### 3. Call links

Backend:

- create call link;
- expire/revoke link;
- join by link if authorized.

Frontend:

- create/share link;
- open link into waiting room;
- show participants before join.

### 4. Scheduled calls

Add event-style scheduled call:

- title;
- starts_at;
- participants;
- reminder notification hook.

### 5. Production media readiness

Document required:

- STUN/TURN provider;
- TURN credentials rotation;
- call logging without media contents;
- network fallback behavior.

## Validation

```bash
pnpm tsc --noEmit
npx eslint src/screens/tabs/MesssagingSubTabs/CallsTab.tsx SocketProvider.tsx --quiet
```

Manual QA:

- Direct voice/video.
- Group voice/video with 3 accounts.
- Screen share where supported.
- Create and join call link.

## Best Prompt For Phase 12

```text
Please proceed with Phase 12 of the KIS Messaging Platform Roadmap without using git commands. Focus on Telegram-grade channels, large groups, topics/forum mode, channel admin tools, comments, and channel analytics. Use docs/messaging-platform-roadmap/phase-12-telegram-grade-channels-large-groups-topics.md as the source of truth. Run safe validation or record blockers and update docs/messaging-platform-roadmap/status.md and docs/BUILD_STATE.md.
```

