# Phase 07 - Calls Current Completion

Purpose: complete the existing voice/video call feature before adding screen share, call links, or large group calls.

## Files To Inspect First

Frontend:

- `/Users/nigel/dev/KIS/src/screens/tabs/MesssagingSubTabs/CallsTab.tsx`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/ChatRoomPage.tsx`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/ChatRoomHandlers.tsx`
- `/Users/nigel/dev/KIS/SocketProvider.tsx`
- Search:
  - `rg -n "call.offer|call.answer|call.ice|call.end|CallOverlay|startCall|incomingCall" /Users/nigel/dev/KIS/src /Users/nigel/dev/KIS/SocketProvider.tsx -S`

Nest:

- `src/chat/features/calls/`
- `src/chat/chat.types.ts`
- `src/realtime/chat.gateway.ts`
- `src/realtime/handlers/`
- `src/notifications/notifications.service.ts`

## Required Work

### 1. Call states

Support:

- ringing;
- accepted;
- active;
- declined;
- missed;
- ended;
- failed.

Frontend and backend state names must match.

### 2. Direct call flow

From direct chat:

- tap voice/video;
- recipient gets incoming call UI;
- accept/decline;
- call connects;
- end call;
- both call histories update.

### 3. Call history UX

In `CallsTab.tsx`:

- add redial action;
- show missed call clearly;
- show incoming/outgoing direction;
- allow filter by voice/video/missed.

### 4. Notifications

Verify incoming call push/in-app notification path:

- `notifyIncomingCall`;
- data includes `conversationId` and `callId`;
- no secret values in payload.

### 5. WebRTC/media proof

If media engine is incomplete:

- document exact missing file/library/provider;
- hide broken in-call controls;
- keep signaling/call history intact.

## Validation

```bash
pnpm tsc --noEmit
npx eslint src/screens/tabs/MesssagingSubTabs/CallsTab.tsx src/Module/ChatRoom/ChatRoomPage.tsx src/Module/ChatRoom/ChatRoomHandlers.tsx SocketProvider.tsx --quiet
```

Manual QA:

- Voice call User 1 to User 2.
- Video call User 2 to User 1.
- Decline a call.
- Miss a call.
- Confirm call history for both users.

## Best Prompt For Phase 08

```text
Please proceed with Phase 08 of the KIS Messaging Platform Roadmap without using git commands. Focus on completing partner/company messaging, including partner main conversations, partner channels/groups, DLP, audit logs, webhooks, legal hold/export hooks, and partner messaging UI. Use docs/messaging-platform-roadmap/phase-08-partner-messaging-completion.md as the source of truth. Run safe validation or record blockers and update docs/messaging-platform-roadmap/status.md and docs/BUILD_STATE.md.
```

