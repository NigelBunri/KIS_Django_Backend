# KIS Messaging Platform Roadmap

Goal: finish the current KIS messaging implementation first, then upgrade it toward WhatsApp/Telegram-grade messaging while preserving KIS-specific partner/company messaging, governance, updates/status, channels, and calls.

This folder is written for low-Codex-usage handoff. Each phase is a standalone page you can paste into normal ChatGPT. The phase pages tell ChatGPT:

- what feature is being completed;
- which backend/frontend files to request;
- what sections to inspect before changing code;
- what to add, remove, or preserve;
- which validation commands to run;
- what status/build-state notes to update.

Do not use git commands for this project unless Nigel explicitly asks.

## Current System Shape

Django backend:

- Chat models: `apps/chat/models.py`
- Chat APIs: `apps/chat/views.py`
- Chat serializers: `apps/chat/serializers.py`
- Chat URLs: `apps/chat/urls.py`
- Chat services: `apps/chat/services.py`
- Chat roles: `apps/chat/views_roles.py`
- Chat discussion helper: `apps/chat/discussion.py`
- Core groups/channels/communities: `apps/core/models.py`, `apps/core/views.py`, `apps/core/serializers.py`
- Partner messaging/governance: `apps/partners/models.py`, `apps/partners/views.py`, `apps/partners/serializers.py`, `apps/partners/services.py`

Nest backend:

- Chat types/events: `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/Nestjs/CC_Node_Backend/src/chat/chat.types.ts`
- WebSocket gateway: `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/Nestjs/CC_Node_Backend/src/realtime/chat.gateway.ts`
- Message handlers: `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/Nestjs/CC_Node_Backend/src/realtime/handlers/messages.ts`
- Message schema/service: `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/Nestjs/CC_Node_Backend/src/chat/features/messages/`
- Calls: `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/Nestjs/CC_Node_Backend/src/chat/features/calls/`
- E2EE/key APIs: `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/Nestjs/CC_Node_Backend/src/chat/features/e2ee/`
- Reactions, receipts, search, sync, pins, stars, threads, presence, moderation: `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/Nestjs/CC_Node_Backend/src/chat/features/`
- Django integration clients: `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/Nestjs/CC_Node_Backend/src/chat/integrations/django/`

React Native frontend:

- Main messaging screen: `/Users/nigel/dev/KIS/src/screens/tabs/MessagesScreen.tsx`
- Messaging subtabs:
  - `/Users/nigel/dev/KIS/src/screens/tabs/MesssagingSubTabs/UpdatesTab.tsx`
  - `/Users/nigel/dev/KIS/src/screens/tabs/MesssagingSubTabs/CallsTab.tsx`
  - `/Users/nigel/dev/KIS/src/screens/tabs/MesssagingSubTabs/HubTab.tsx`
- Chat room:
  - `/Users/nigel/dev/KIS/src/Module/ChatRoom/ChatRoomPage.tsx`
  - `/Users/nigel/dev/KIS/src/Module/ChatRoom/ChatRoomHandlers.tsx`
  - `/Users/nigel/dev/KIS/src/Module/ChatRoom/hooks/useChatMessaging.ts`
  - `/Users/nigel/dev/KIS/src/Module/ChatRoom/hooks/useChatPersistence.ts`
  - `/Users/nigel/dev/KIS/src/Module/ChatRoom/normalizeConversation.ts`
  - `/Users/nigel/dev/KIS/src/Module/ChatRoom/messagesUtils.ts`
  - `/Users/nigel/dev/KIS/src/Module/ChatRoom/uploadFileToBackend.ts`
- Chat components: `/Users/nigel/dev/KIS/src/Module/ChatRoom/componets/`
- Contact/group/channel/community creation: `/Users/nigel/dev/KIS/src/Module/AddContacts/`
- Socket provider: `/Users/nigel/dev/KIS/SocketProvider.tsx`
- E2EE helpers: `/Users/nigel/dev/KIS/src/security/`
- Network routes/cache:
  - `/Users/nigel/dev/KIS/src/network/index.ts`
  - `/Users/nigel/dev/KIS/src/network/routes/socialRoutes.ts`
  - `/Users/nigel/dev/KIS/src/network/cache.tsx`

## Target Product

KIS Messaging should become a polished secure communication system with:

- WhatsApp-grade direct chats, groups, updates/status, voice/video calls, media handling, and multi-device reliability.
- Telegram-grade channels, communities, large-group tooling, search, folders, scheduled/silent messages, bots/automation hooks, and live stream/chat foundations.
- KIS-specific partner/company messaging with organization roles, DLP, audit logs, policy checks, webhooks, and staff/admin controls.
- Strong E2EE UX where appropriate, with device trust, key verification, and no silent plaintext downgrade in production.
- Complete UI-to-backend consistency: no visible button should be only decoration unless clearly marked as coming soon.

## Phase Order

First complete current KIS features:

1. `phase-00-analysis-and-product-spec.md`
2. `phase-01-message-reliability-and-cache.md`
3. `phase-02-e2ee-device-trust-and-history.md`
4. `phase-03-chat-list-presence-and-unread.md`
5. `phase-04-current-message-types-completion.md`
6. `phase-05-groups-channels-communities-current-completion.md`
7. `phase-06-updates-status-current-completion.md`
8. `phase-07-calls-current-completion.md`
9. `phase-08-partner-messaging-completion.md`

Then add WhatsApp/Telegram-grade gaps:

10. `phase-09-privacy-disappearing-view-once-chat-lock.md`
11. `phase-10-multi-device-sync-and-backup.md`
12. `phase-11-advanced-calls-screen-share-call-links.md`
13. `phase-12-telegram-grade-channels-large-groups-topics.md`
14. `phase-13-bots-automation-public-usernames-folders.md`
15. `phase-14-search-media-files-saved-messages.md`
16. `phase-15-moderation-safety-admin-analytics.md`
17. `phase-16-qa-launch-runbook.md`

Each phase should update:

- `docs/messaging-platform-roadmap/status.md`
- `docs/BUILD_STATE.md`

## Golden Rules For Normal ChatGPT Sessions

- Paste only the phase file you are doing.
- If ChatGPT asks for code, paste the specific files listed in the phase, not the whole project.
- Ask ChatGPT for exact replacement blocks or a patch-style response.
- Preserve local development and existing behavior unless the phase explicitly says to disable or replace it.
- If a check is blocked, record the exact command and blocker in `status.md`, then move on.
- Do not remove existing APIs until the new path is proven by tests and UI.
- Do not expose `.env` secret values in chat.

