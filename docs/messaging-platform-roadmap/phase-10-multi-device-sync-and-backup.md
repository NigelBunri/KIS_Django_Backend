# Phase 10 - Multi Device Sync And Backup

Purpose: move toward WhatsApp/Telegram-grade reliability across app restarts, device changes, and multiple active devices.

## Files To Inspect First

Frontend:

- `/Users/nigel/dev/KIS/src/Module/ChatRoom/normalizeConversation.ts`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/hooks/useChatPersistence.ts`
- `/Users/nigel/dev/KIS/src/network/cache.tsx`
- `/Users/nigel/dev/KIS/src/security/e2ee.ts`
- `/Users/nigel/dev/KIS/src/security/customE2EE.ts`
- `/Users/nigel/dev/KIS/SocketProvider.tsx`

Nest:

- `src/chat/features/sync/`
- `src/chat/features/messages/messages.service.ts`
- `src/chat/features/e2ee/`

Django:

- auth/device models and token lifecycle files;
- `apps/chat/views.py`;
- `apps/chat/models.py`.

## Required Features

### 1. Device registry

Each user device should have:

- device id;
- display name;
- platform;
- last seen;
- revoked flag;
- public key bundle reference.

### 2. Linked devices UI

Add screen/sheet:

- list devices;
- revoke device;
- show current device;
- show last seen.

### 3. Message sync

Implement:

- sync after last known sequence;
- gap detection;
- no duplicate messages;
- correct fromMe on every device;
- per-device read receipts where necessary.

### 4. Backup/restore policy

Document and/or implement:

- local encrypted cache backup;
- server history source of truth;
- how encrypted keys are restored;
- what happens when user loses all devices.

Do not claim encrypted backup exists unless actually implemented.

## Validation

```bash
python3 manage.py check
pnpm tsc --noEmit
npx eslint src/Module/ChatRoom/normalizeConversation.ts src/Module/ChatRoom/hooks/useChatPersistence.ts src/network/cache.tsx src/security/e2ee.ts SocketProvider.tsx --quiet
```

Manual QA:

- Same account on two devices.
- Send from device A, receive on device B.
- Restart both.
- Revoke one device and confirm it cannot fetch keys/send.

## Best Prompt For Phase 11

```text
Please proceed with Phase 11 of the KIS Messaging Platform Roadmap without using git commands. Focus on advanced calls: group calls, screen share, call links, scheduled calls, in-call reactions, call quality states, and production WebRTC/TURN readiness. Use docs/messaging-platform-roadmap/phase-11-advanced-calls-screen-share-call-links.md as the source of truth. Run safe validation or record blockers and update docs/messaging-platform-roadmap/status.md and docs/BUILD_STATE.md.
```

