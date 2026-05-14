# Phase 15 - Moderation Safety Admin Analytics

Purpose: make messaging safe to operate at scale.

## Files To Inspect First

Django:

- `apps/chat/models.py`
- `apps/chat/views.py`
- `apps/partners/models.py`
- `apps/partners/views.py`
- `apps/partners/services.py`
- admin files for chat/partners.

Nest:

- `src/chat/features/moderation/`
- `src/chat/infra/rate-limit/`
- message handlers.

Frontend:

- `/Users/nigel/dev/KIS/src/Module/ChatRoom/ChatRoomHandlers.tsx`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/ChatRoomPage.tsx`
- partner admin screens under `/Users/nigel/dev/KIS/src/components/partners/` and `/Users/nigel/dev/KIS/src/screens/partners/`

## Required Features

### 1. User safety actions

Complete:

- report message;
- report conversation;
- block user;
- unblock user;
- mute conversation;
- hide archived/blocked conversations from normal list.

### 2. Admin moderation queue

Add:

- reported messages;
- reported users;
- reported groups/channels;
- status reports;
- action history.

### 3. Spam controls

Add:

- per-user send rate;
- new-account limits;
- attachment limits;
- duplicate message detection;
- suspicious contact spam detection.

### 4. Audit logs

Persist:

- message delete by admin;
- member ban/unban;
- channel admin changes;
- partner DLP block/warn;
- webhook failures;
- invite link usage.

### 5. Analytics

Admin-visible:

- message volume;
- call volume;
- active chats;
- reports;
- blocked sends;
- delivery latency.

Do not expose private message contents in analytics.

## Validation

```bash
python3 manage.py check
pnpm tsc --noEmit
npx eslint src/Module/ChatRoom src/components/partners src/screens/partners --quiet
```

## Best Prompt For Phase 16

```text
Please proceed with Phase 16 of the KIS Messaging Platform Roadmap without using git commands. Focus on final QA and launch runbook for messaging: direct chats, groups, channels, communities, partner messaging, updates/status, calls, E2EE, privacy, moderation, cache, sync, and performance. Use docs/messaging-platform-roadmap/phase-16-qa-launch-runbook.md as the source of truth. Run safe validation or record blockers and update docs/messaging-platform-roadmap/status.md and docs/BUILD_STATE.md with final launch readiness.
```

