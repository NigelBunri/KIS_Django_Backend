# Phase 09 - Privacy Disappearing View Once Chat Lock

Purpose: add WhatsApp-grade privacy controls after the current core implementation is stable.

## Files To Inspect First

Django:

- `apps/chat/models.py`
- `apps/chat/views.py`
- `apps/chat/serializers.py`

Nest:

- `src/chat/features/messages/schemas/message.schema.ts`
- `src/chat/features/messages/messages.service.ts`
- `src/realtime/handlers/messages.ts`

Frontend:

- `/Users/nigel/dev/KIS/src/Module/ChatRoom/ChatRoomPage.tsx`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/ChatRoomHandlers.tsx`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/hooks/useChatMessaging.ts`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/componets/`
- `/Users/nigel/dev/KIS/src/screens/tabs/MessagesScreen.tsx`

## Required Features

### 1. Disappearing messages

Add conversation setting:

- off;
- 24 hours;
- 7 days;
- 90 days;
- custom seconds for dev/test only.

Message schema needs:

- `expiresAt`;
- `deleteForEveryoneAt`;
- `isExpired`.

Backend cleanup can be:

- scheduled job later;
- query-time filtering first.

### 2. View-once media

Add attachment fields:

- `viewOnce: boolean`;
- `viewedBy: user ids or count`;
- `openedAt`.

Frontend:

- after open, hide preview;
- show "Opened";
- prevent easy replay from UI.

### 3. Chat lock

Add local app-level lock:

- locked chat list entry hidden or blurred;
- opening requires device biometric/passcode wrapper where available;
- fallback PIN if biometric unavailable.

Do not store PIN plaintext.

### 4. Privacy settings UI

Add `PrivacySettingsSheet`:

- disappearing timer;
- read receipts if supported;
- typing/presence preference if supported;
- block/report;
- media save preference.

## Validation

```bash
python3 manage.py check
pnpm tsc --noEmit
npx eslint src/Module/ChatRoom/ChatRoomPage.tsx src/Module/ChatRoom/ChatRoomHandlers.tsx src/Module/ChatRoom/hooks/useChatMessaging.ts src/screens/tabs/MessagesScreen.tsx --quiet
```

Manual QA:

- Enable disappearing messages in a direct chat.
- Send message and confirm expiry filtering.
- Send view-once image/video and open once.
- Lock chat and verify open requires unlock.

## Best Prompt For Phase 10

```text
Please proceed with Phase 10 of the KIS Messaging Platform Roadmap without using git commands. Focus on multi-device sync, linked devices, reliable backup/restore planning, and encrypted local/server sync behavior. Use docs/messaging-platform-roadmap/phase-10-multi-device-sync-and-backup.md as the source of truth. Run safe validation or record blockers and update docs/messaging-platform-roadmap/status.md and docs/BUILD_STATE.md.
```

