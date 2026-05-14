# Phase 16 - QA Launch Runbook

Purpose: prove messaging is ready for production after all implementation phases.

## Full Validation Commands

Django:

```bash
python3 manage.py check
python3 manage.py test apps.chat apps.partners --noinput
```

Nest:

```bash
pnpm tsc --noEmit
pnpm test
```

React Native:

```bash
npm run typecheck
npx eslint src/Module/ChatRoom src/Module/AddContacts src/screens/tabs/MessagesScreen.tsx src/screens/tabs/MesssagingSubTabs --quiet
```

If full checks are blocked by unrelated legacy errors, record exact blockers and run focused touched-file checks.

## Manual QA Matrix

### Direct Chat

- User 1 starts chat with User 2 from contacts.
- User 1 sends short text, long text, image, video, document, voice, sticker.
- User 2 receives quickly.
- User 2 replies.
- Restart both apps.
- Verify history decrypts and alignment is correct.
- Verify both chat lists show the conversation.

### Groups

- Create group.
- Add members.
- Send messages.
- Promote admin.
- Remove member.
- Leave group.
- Search group.

### Channels

- Create channel.
- Subscribe.
- Subscriber cannot post.
- Admin can post.
- Mute/unsubscribe.
- Report channel/post.

### Communities

- Create community.
- Attach group/channel.
- Join/leave.
- Verify permissions.

### Updates/Status

- Post text/image/video/audio status.
- Apply each visibility mode.
- View as allowed and excluded users.
- Reply where allowed.
- Mute/report.
- Verify status ring.

### Calls

- Direct voice call.
- Direct video call.
- Missed call.
- Declined call.
- Group call if implemented.
- Call history for both users.

### Partner Messaging

- Partner owner sends in main partner conversation.
- Subscriber/member receives.
- DLP block/warn test.
- Audit log visible.
- Webhook dispatch recorded.

### Privacy/Security

- E2EE send/history.
- Device revoke.
- Disappearing message.
- View-once media.
- Chat lock.
- Block/unblock.

## Launch Go/No-Go

Launch is **NO-GO** if:

- either direction of direct messaging fails;
- chat list is missing conversations for one participant;
- history alignment is wrong after restart;
- production E2EE silently falls back to plaintext;
- cache errors hide fresh server data;
- calls ring but cannot be ended cleanly;
- user can access a conversation they are not a member of;
- partner DLP blocks ordinary personal chats by mistake;
- visible buttons do nothing without clear disabled state.

Launch can be **GO** only when all critical QA items pass or have explicit product-approved deferrals.

## Final Summary Template

```text
Messaging launch readiness:
- Direct chat:
- Groups:
- Channels:
- Communities:
- Updates/status:
- Calls:
- Partner messaging:
- E2EE/security:
- Cache/sync:
- Moderation:

Commands passed:

Commands blocked:

Remaining approved deferrals:

Production go/no-go:
```

