# Phase 00 - Messaging Analysis And Product Spec

Purpose: create a complete handoff roadmap for upgrading KIS Messaging. This phase is documentation-only and is already complete.

## Evidence From Current Code

Django:

- `apps/chat/models.py` includes direct, group, channel, post, thread, and system conversation types.
- `apps/chat/views.py` includes direct creation, request accept/reject, archive, lock/block, sequence allocation, unread/read, member IDs, WebSocket permissions, partner policy check, and partner webhook dispatch.
- `apps/chat/views_roles.py` includes custom conversation roles and permission assignment.
- `apps/partners/*` includes partner main conversations, partner channels/groups, role assignments, policy/DLP/audit/webhook foundations.

Nest:

- `src/chat/chat.types.ts` includes message kinds and events for messages, reactions, receipts, typing, presence, pins/stars, and calls.
- `src/chat/features/messages/` stores text, styled text, voice, sticker, contacts, poll, event, attachments, replies, reactions, receipts, pinned/starred/deleted/edited state.
- `src/chat/features/calls/` stores call sessions and history.
- `src/chat/features/e2ee/` includes conversation key APIs.

React Native:

- `MessagesScreen.tsx` has Chats, Updates, and Calls tabs plus chat filters.
- `UpdatesTab.tsx` has status and channel UI with backend calls.
- `CallsTab.tsx` has call history UI.
- `HubTab.tsx` is still a placeholder.
- `ChatRoomPage.tsx` and `useChatMessaging.ts` handle live chat, send, retry, E2EE, attachments, replies, reactions, typing, receipts, and local persistence.
- `AddContactsPage.tsx` and its form components create contacts, groups, channels, and communities.

## Phase Output

This folder now contains:

- a product spec;
- a status tracker;
- a README;
- 16 implementation phase documents.

## Best Prompt For Phase 01

```text
Please proceed with Phase 01 of the KIS Messaging Platform Roadmap without using git commands. Focus on message reliability, cache correctness, delivery speed, conversation-list consistency, and history alignment. Use docs/messaging-platform-roadmap/phase-01-message-reliability-and-cache.md as the source of truth. Keep local development working, do not redesign UI broadly, run the safe validation commands or record blockers, and update docs/messaging-platform-roadmap/status.md and docs/BUILD_STATE.md.
```

