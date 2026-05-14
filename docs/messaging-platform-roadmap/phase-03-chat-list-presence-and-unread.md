# Phase 03 - Chat List Presence And Unread

Purpose: complete the main chat list so it behaves like a mature messaging app: accurate unread counts, status rings, presence, typing previews, filters, pinned/archive/blocked views, and reliable search.

## Files To Inspect First

- `/Users/nigel/dev/KIS/src/screens/tabs/MessagesScreen.tsx`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/componets/MessageTabs.tsx`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/normalizeConversation.ts`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/messagesUtils.ts`
- `/Users/nigel/dev/KIS/SocketProvider.tsx`
- `apps/chat/views.py`
- `apps/chat/serializers.py`
- Nest presence feature: `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/Nestjs/CC_Node_Backend/src/chat/features/presence/`
- Nest receipts/sync features: `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/Nestjs/CC_Node_Backend/src/chat/features/receipts/` and `src/chat/features/sync/`

## Required Work

### 1. Status ring data

In `MessagesScreen.tsx`:

- Replace `_setStatusByUserId` placeholder with a real load function.
- Fetch current status summary from `ROUTES.statuses.list` or add a small summary endpoint if needed.
- Populate:
  - `hasStatus`
  - `hasUnseen`
  - `latestStatusAt`

In `MessageTabs.tsx`:

- Ensure avatar rings render only for direct chat peers with status.
- Tapping ring should emit `status.open` and open Updates viewer.

### 2. Presence and typing

In `SocketProvider.tsx` and `MessagesScreen.tsx`:

- Subscribe to presence updates.
- Store online/last seen per user.
- Show typing preview per conversation when active.
- Clear typing preview after timeout.

### 3. Unread counts

In Django/Nest:

- Confirm `update-read-state` writes read state.
- Confirm incoming messages increment local unread unless conversation is open.
- Confirm read receipts clear unread on conversation open.

In frontend:

- Chat rows should show unread badge and bold preview.
- Global tab badge should count unread chats.

### 4. Filters

Complete chips already visible:

- All
- Unread
- Groups
- Community
- Mentions
- Archived
- Blocked

If a filter has no backend support, implement frontend filtering using current conversation metadata first.

### 5. Search

Search should cover:

- conversation title/contact name;
- phone;
- last message preview;
- channel/community name.

Do not perform heavy message-content search here; that belongs to Phase 14.

## Validation

```bash
python3 manage.py check
npx eslint src/screens/tabs/MessagesScreen.tsx src/Module/ChatRoom/componets/MessageTabs.tsx src/Module/ChatRoom/normalizeConversation.ts SocketProvider.tsx --quiet
```

Manual QA:

- Send/read messages both directions.
- Confirm unread badge appears/disappears.
- Confirm typing preview appears and clears.
- Confirm status ring opens exact user's status.
- Confirm filters do not hide conversations incorrectly.

## Best Prompt For Phase 04

```text
Please proceed with Phase 04 of the KIS Messaging Platform Roadmap without using git commands. Focus on completing existing message types: attachments, voice notes, stickers, contacts, polls, events, replies, forwards, edit/delete, reactions, pins, and stars. Use docs/messaging-platform-roadmap/phase-04-current-message-types-completion.md as the source of truth. Run safe validation or record blockers and update docs/messaging-platform-roadmap/status.md and docs/BUILD_STATE.md.
```

