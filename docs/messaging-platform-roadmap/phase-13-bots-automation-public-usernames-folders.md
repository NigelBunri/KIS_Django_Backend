# Phase 13 - Bots Automation Public Usernames Folders

Purpose: add Telegram-style power-user and platform features after core channels/groups are stable.

## Files To Inspect First

Django:

- user/profile models and serializers for handles;
- `apps/chat/models.py`;
- `apps/chat/views.py`;
- `apps/partners/services.py` for automation/webhooks.

Nest:

- chat message handlers;
- webhook/integration modules;
- rate limit service.

Frontend:

- `/Users/nigel/dev/KIS/src/screens/tabs/MessagesScreen.tsx`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/`
- `/Users/nigel/dev/KIS/src/Module/AddContacts/`

## Required Features

### 1. Public usernames/handles

Add:

- unique user handle;
- unique channel/community handle;
- search by handle;
- open chat/channel by handle.

### 2. Chat folders

Add user-defined folders:

- name;
- included conversation ids;
- excluded conversation ids;
- rules such as unread/groups/channels/partners.

Frontend:

- folder chips or tabs on Messages screen.

### 3. Scheduled messages

Message schema:

- `scheduledAt`;
- `status: scheduled/sent/cancelled`.

Backend job:

- deliver when due.

Frontend:

- schedule picker;
- scheduled messages list;
- cancel/edit scheduled message.

### 4. Silent messages

Message payload:

- `silent: true`.

Notification service:

- store message without push sound.

### 5. Slow mode

Conversation setting:

- seconds between sends per member.

Nest:

- enforce via rate limit.

Frontend:

- show cooldown timer.

### 6. Bot/automation foundation

Start provider-neutral:

- bot account type;
- bot token stored hashed;
- incoming webhook endpoint;
- outgoing event subscriptions;
- permission scopes.

Do not expose arbitrary code execution.

## Validation

```bash
python3 manage.py check
pnpm tsc --noEmit
npx eslint src/screens/tabs/MessagesScreen.tsx src/Module/ChatRoom src/Module/AddContacts --quiet
```

## Best Prompt For Phase 14

```text
Please proceed with Phase 14 of the KIS Messaging Platform Roadmap without using git commands. Focus on global search, media/files browser, saved messages, message translation/transcription placeholders, and chat storage cleanup. Use docs/messaging-platform-roadmap/phase-14-search-media-files-saved-messages.md as the source of truth. Run safe validation or record blockers and update docs/messaging-platform-roadmap/status.md and docs/BUILD_STATE.md.
```

