# Phase 04 - Current Message Types Completion

Purpose: finish the message types already present in KIS before adding new WhatsApp/Telegram features.

## Files To Inspect First

Frontend:

- `/Users/nigel/dev/KIS/src/Module/ChatRoom/hooks/useChatMessaging.ts`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/ChatRoomHandlers.tsx`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/ChatRoomPage.tsx`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/messagesUtils.ts`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/componets/`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/uploadFileToBackend.ts`

Nest:

- `src/chat/chat.types.ts`
- `src/chat/features/messages/schemas/message.schema.ts`
- `src/chat/features/messages/messages.service.ts`
- `src/realtime/handlers/messages.ts`
- `src/chat/features/reactions/`
- `src/chat/features/pins/`
- `src/chat/features/stars/`

## Required Message Types

Complete end-to-end support for:

- plain text;
- styled text;
- image;
- video;
- audio file;
- voice note;
- document/file;
- sticker;
- contact card;
- poll;
- event;
- reply;
- forwarded message;
- edited message;
- deleted message;
- reactions;
- pinned message;
- starred/saved message.

## Required Work

### 1. Payload contract

Create or update a shared frontend mapping function:

```ts
function buildChatSendPayload(message: LocalMessage): ChatSendPayload
```

It must produce the exact Nest schema shape:

- `kind`
- `text`
- `styledText`
- `voice`
- `sticker`
- `attachments`
- `contacts`
- `poll`
- `event`
- `replyToId`
- `threadId`
- `clientId`
- `conversationId`

### 2. Renderer contract

Create or update one normalized renderer map:

```ts
const MESSAGE_RENDERERS = {
  text,
  styled_text,
  voice,
  sticker,
  contacts,
  poll,
  event,
  system,
}
```

Avoid special-case rendering scattered across multiple components.

### 3. Attachments

For each attachment:

- show preview;
- show filename/mime/size where useful;
- retry upload if failed;
- avoid sending message until upload gives a safe file reference or URL;
- support media headers if private media requires auth.

### 4. Polls and events

If voting/RSVP is not implemented:

- hide vote/RSVP action or mark it disabled;
- keep sending/displaying poll/event payloads working;
- document voting/RSVP as future work unless implemented in this phase.

### 5. Forwarding

Forward should preserve:

- original kind;
- text/attachment metadata;
- safe preview;
- not raw sender secrets/encryption metadata.

### 6. Reactions, pins, stars

Confirm frontend actions hit Nest endpoints/events and update local state optimistically.

## Validation

```bash
pnpm tsc --noEmit
npx eslint src/Module/ChatRoom/hooks/useChatMessaging.ts src/Module/ChatRoom/ChatRoomHandlers.tsx src/Module/ChatRoom/ChatRoomPage.tsx src/Module/ChatRoom/messagesUtils.ts --quiet
```

Manual QA:

- Send each message type.
- Restart app and confirm history renders each type.
- React/pin/star/edit/delete a message.
- Forward an image and a text message.

## Best Prompt For Phase 05

```text
Please proceed with Phase 05 of the KIS Messaging Platform Roadmap without using git commands. Focus on completing current groups, channels, and communities before adding Telegram-grade expansion. Use docs/messaging-platform-roadmap/phase-05-groups-channels-communities-current-completion.md as the source of truth. Run safe validation or record blockers and update docs/messaging-platform-roadmap/status.md and docs/BUILD_STATE.md.
```

