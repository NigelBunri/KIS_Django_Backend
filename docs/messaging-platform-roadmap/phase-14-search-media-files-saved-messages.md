# Phase 14 - Search Media Files Saved Messages

Purpose: add mature message discovery and personal organization tools.

## Files To Inspect First

Nest:

- `src/chat/features/search/`
- `src/chat/features/messages/messages.service.ts`
- `src/chat/features/messages/schemas/message.schema.ts`

Frontend:

- `/Users/nigel/dev/KIS/src/screens/tabs/MessagesScreen.tsx`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/ChatRoomPage.tsx`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/componets/`

Django:

- `apps/chat/views.py`
- user saved/starred APIs if any.

## Required Features

### 1. Global message search

Search should support:

- text;
- sender;
- date range;
- conversation;
- media type;
- files.

Respect membership permissions.

### 2. In-chat search

Inside chat room:

- search bar;
- next/previous result;
- highlight matched message;
- jump to message.

### 3. Media/files browser

Per conversation:

- Media;
- Files;
- Links;
- Voice;
- Polls/Events.

### 4. Saved messages

Add "Saved Messages" as personal system conversation or dedicated saved table.

Users can:

- save/star any message;
- view all saved messages;
- remove saved message;
- forward saved message.

### 5. Translation/transcription placeholders

If full translation/transcription is not ready:

- add provider-neutral interfaces;
- hide buttons unless enabled;
- document env keys needed later.

## Validation

```bash
pnpm tsc --noEmit
npx eslint src/screens/tabs/MessagesScreen.tsx src/Module/ChatRoom --quiet
```

Manual QA:

- Search in one chat.
- Search globally.
- Open media browser.
- Save and unsave messages.

## Best Prompt For Phase 15

```text
Please proceed with Phase 15 of the KIS Messaging Platform Roadmap without using git commands. Focus on moderation, safety, admin analytics, abuse visibility, spam controls, reports, blocks, channel/group audit logs, and partner-safe compliance views. Use docs/messaging-platform-roadmap/phase-15-moderation-safety-admin-analytics.md as the source of truth. Run safe validation or record blockers and update docs/messaging-platform-roadmap/status.md and docs/BUILD_STATE.md.
```

