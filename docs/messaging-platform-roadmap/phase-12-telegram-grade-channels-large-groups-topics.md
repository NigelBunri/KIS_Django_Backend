# Phase 12 - Telegram Grade Channels Large Groups Topics

Purpose: extend current KIS channels/groups/communities toward Telegram-grade scale and tooling.

## Files To Inspect First

Django:

- `apps/core/models.py`
- `apps/core/views.py`
- `apps/core/serializers.py`
- `apps/chat/models.py`
- `apps/chat/views.py`
- `apps/partners/models.py`
- `apps/partners/views.py`

Nest:

- `src/chat/features/messages/`
- `src/chat/features/threads/`
- `src/chat/features/search/`

Frontend:

- `/Users/nigel/dev/KIS/src/screens/tabs/MesssagingSubTabs/UpdatesTab.tsx`
- `/Users/nigel/dev/KIS/src/Module/AddContacts/components/NewChannelForm.tsx`
- `/Users/nigel/dev/KIS/src/Module/AddContacts/components/NewGroupForm.tsx`
- `/Users/nigel/dev/KIS/src/Module/AddContacts/components/NewCommunityForm.tsx`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/`

## Required Features

### 1. Channel admin tools

Add:

- channel info screen;
- admins/editors/moderators;
- subscriber count;
- channel post permissions;
- channel link/handle;
- mute notifications;
- leave/unsubscribe;
- report channel.

### 2. Large group readiness

Add pagination for:

- members;
- messages;
- admin actions;
- search results.

Avoid loading full member lists into the chat room.

### 3. Topics/forum mode

Use existing thread/subroom foundation:

- model `Topic` or map to child conversation;
- topic list;
- create topic;
- close/reopen topic;
- route messages by topic.

### 4. Channel comments

Option A:

- comments are a linked discussion conversation.

Option B:

- comments are message threads.

Pick one and document it. Do not implement both.

### 5. Analytics

Add admin-visible:

- views;
- reactions;
- shares;
- joins/leaves;
- top posts.

## Validation

```bash
python3 manage.py check
pnpm tsc --noEmit
npx eslint src/screens/tabs/MesssagingSubTabs/UpdatesTab.tsx src/Module/AddContacts src/Module/ChatRoom --quiet
```

## Best Prompt For Phase 13

```text
Please proceed with Phase 13 of the KIS Messaging Platform Roadmap without using git commands. Focus on bots/automation hooks, public usernames/handles, chat folders, advanced filters, scheduled messages, silent messages, and slow mode. Use docs/messaging-platform-roadmap/phase-13-bots-automation-public-usernames-folders.md as the source of truth. Run safe validation or record blockers and update docs/messaging-platform-roadmap/status.md and docs/BUILD_STATE.md.
```

