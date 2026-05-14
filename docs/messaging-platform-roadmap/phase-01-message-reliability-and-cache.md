# Phase 01 - Message Reliability And Cache

Purpose: finish the most urgent existing messaging behavior before adding new features: both users must send/receive quickly, both users must see the conversation in the chat list, cached data must remain useful, and message alignment must stay correct after app restart.

## Files To Inspect First

Ask ChatGPT to request these exact files or sections:

Frontend:

- `/Users/nigel/dev/KIS/src/Module/ChatRoom/hooks/useChatMessaging.ts`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/hooks/useChatPersistence.ts`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/ChatRoomPage.tsx`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/normalizeConversation.ts`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/chatStorage.ts`
- `/Users/nigel/dev/KIS/src/network/cache.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/MessagesScreen.tsx`
- `/Users/nigel/dev/KIS/SocketProvider.tsx`

Django:

- `apps/chat/views.py`
- `apps/chat/services.py`
- `apps/chat/serializers.py`

Nest:

- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/Nestjs/CC_Node_Backend/src/realtime/handlers/messages.ts`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/Nestjs/CC_Node_Backend/src/chat/features/messages/messages.service.ts`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/Nestjs/CC_Node_Backend/src/chat/integrations/django/django-conversation.client.ts`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/Nestjs/CC_Node_Backend/src/chat/integrations/django/django-seq.client.ts`

## Required Fixes

### 1. Conversation list must update for both sender and recipient

In React Native:

- In `MessagesScreen.tsx`, locate the socket listeners for incoming messages.
- Ensure every `chat.message` event calls a shared `upsertConversationFromMessage` helper.
- The helper must:
  - derive `conversationId`;
  - update `lastMessagePreview`, `lastMessageAt`, unread count, and participant data;
  - create a minimal row if the server list has not refreshed yet;
  - never depend only on local cache.

In Django:

- In `apps/chat/views.py`, verify `update_last_message` updates `last_message_at`, `last_message_preview`, and `last_message_seq`.
- Ensure both participants remain active `ConversationMember` rows after direct conversation creation.

### 2. Cache must write safely and not hide fresh server data

In `/Users/nigel/dev/KIS/src/network/cache.tsx`:

- Keep the dedicated chat-cache folder.
- If file write fails, log once per key in development but still return fresh server data to the UI.
- Do not let failed cache write cause `fetchConversationsForCurrentUser` to return `[]`.

In `normalizeConversation.ts`:

- Always prefer fresh API response when available.
- Use per-user cache keys.
- If current user is unknown, delay writing user-specific cache rather than writing under `anon` and overwriting later.

### 3. History alignment after restart

In `useChatPersistence.ts` and `ChatRoomPage.tsx`:

- Do not normalize `fromMe` when `currentUserId` is missing.
- If `currentUserId` is not available at first render, wait for socket/auth fallback before saving normalized history.
- When loading messages, compute `fromMe` from `senderId === currentUserId`.
- Preserve stored `fromMe` only as fallback, not as truth when senderId is present.

### 4. Retry should be silent until final failure

In `useChatMessaging.ts`:

- Keep optimistic messages visible.
- Do not show `tap to retry` during background retry.
- Add separate internal state such as `status: "retrying"` or `retryHidden: true`.
- Only show user-facing failed state after retry limit or permanent error.

### 5. Delivery speed

In Nest `messages.ts`:

- Allocate sequence once.
- Save and emit immediately.
- Update Django last message asynchronously when safe.
- Do not block user-visible delivery on non-critical policy/webhook/audit operations unless policy says blocked.

## Tests / Validation

Run what is safe:

```bash
python3 manage.py check
```

In React Native:

```bash
npx eslint src/Module/ChatRoom/hooks/useChatMessaging.ts src/Module/ChatRoom/hooks/useChatPersistence.ts src/Module/ChatRoom/ChatRoomPage.tsx src/Module/ChatRoom/normalizeConversation.ts src/network/cache.tsx src/screens/tabs/MessagesScreen.tsx --quiet
```

In Nest:

```bash
pnpm tsc --noEmit
```

Manual QA:

- User 1 sends short and long message to User 2.
- User 2 sends short and long message to User 1.
- Restart both apps.
- Confirm sent messages are right-aligned for sender and left-aligned for receiver.
- Confirm both users see the conversation in the chat list.
- Confirm messages do not show `encrypted message` after fresh send.
- Confirm background retry does not reload other messages visibly.

## Status Updates

Update:

- `docs/messaging-platform-roadmap/status.md`
- `docs/BUILD_STATE.md`

## Best Prompt For Phase 02

```text
Please proceed with Phase 02 of the KIS Messaging Platform Roadmap without using git commands. Focus on E2EE, device trust, and encrypted history. Use docs/messaging-platform-roadmap/phase-02-e2ee-device-trust-and-history.md as the source of truth. Do not silently downgrade encryption in production, keep local development working, run safe validation or record blockers, and update docs/messaging-platform-roadmap/status.md and docs/BUILD_STATE.md.
```

