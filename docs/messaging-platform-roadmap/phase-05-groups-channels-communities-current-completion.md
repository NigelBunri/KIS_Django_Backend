# Phase 05 - Groups Channels Communities Current Completion

Purpose: complete the current group/channel/community implementation so every visible creation and management UI is backed by working APIs and consistent chat behavior.

## Files To Inspect First

Frontend:

- `/Users/nigel/dev/KIS/src/Module/AddContacts/AddContactsPage.tsx`
- `/Users/nigel/dev/KIS/src/Module/AddContacts/components/NewGroupForm.tsx`
- `/Users/nigel/dev/KIS/src/Module/AddContacts/components/NewChannelForm.tsx`
- `/Users/nigel/dev/KIS/src/Module/AddContacts/components/NewCommunityForm.tsx`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/ChatRoomPage.tsx`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/ChatRoomHandlers.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/MesssagingSubTabs/UpdatesTab.tsx`

Django:

- `apps/core/models.py`
- `apps/core/views.py`
- `apps/core/serializers.py`
- `apps/chat/models.py`
- `apps/chat/views.py`
- `apps/chat/serializers.py`

Partner:

- `apps/partners/views.py`
- `apps/partners/services.py`
- `apps/partners/serializers.py`

## Required Work

### 1. Creation flows

For group/channel/community creation:

- validate required fields;
- create linked `Conversation`;
- add creator as owner/admin member;
- return `conversation_id`;
- update frontend chat list immediately.

### 2. Channel posting rules

Channel behavior should be:

- subscribers can read;
- owner/admin/editor can post;
- readonly members cannot post;
- UI hides composer if `canPost=false`.

### 3. Group management

Complete UI/API for:

- add member;
- remove member;
- promote/demote admin;
- leave group;
- group avatar/title/description edit;
- mute/archive/block/report.

### 4. Community management

Complete:

- create community;
- attach groups/channels;
- default announcement channel;
- join/leave;
- member count;
- visible channel list.

### 5. Invite links/codes foundation

If full invite links are too large for this phase:

- add backend model or documented plan;
- add disabled UI state with clear copy;
- do not show a fake working invite button.

## Validation

```bash
python3 manage.py check
npx eslint src/Module/AddContacts/AddContactsPage.tsx src/Module/AddContacts/components/NewGroupForm.tsx src/Module/AddContacts/components/NewChannelForm.tsx src/Module/AddContacts/components/NewCommunityForm.tsx src/Module/ChatRoom/ChatRoomPage.tsx --quiet
```

Manual QA:

- Create group, channel, community.
- Enter each conversation.
- Confirm creator can post.
- Confirm subscriber readonly behavior for channel.
- Confirm list refresh after creation.

## Best Prompt For Phase 06

```text
Please proceed with Phase 06 of the KIS Messaging Platform Roadmap without using git commands. Focus on completing Updates/Status: text/image/video/audio statuses, visibility, replies, viewed state, rings, mute/report, and expiry. Use docs/messaging-platform-roadmap/phase-06-updates-status-current-completion.md as the source of truth. Run safe validation or record blockers and update docs/messaging-platform-roadmap/status.md and docs/BUILD_STATE.md.
```

