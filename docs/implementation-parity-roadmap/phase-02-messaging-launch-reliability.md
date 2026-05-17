# Phase 02 - Messaging 100% Launch Reliability

Date: 2026-05-17

Purpose: close the highest-risk launch reliability gaps in the messaging core while preserving the current UI and existing API behavior.

## Scope Completed

- Hardened generic `POST /api/v1/conversations/` direct conversation creation so it uses the canonical direct identity service and stable `direct_key`.
- Preserved direct request workflow semantics for initiator, recipient, pending state, and owner/member roles.
- Kept subroom creation idempotent for one parent message by allowing the view to return the existing subroom instead of failing serializer unique validation.
- Fixed conversation search so `last_message_preview` participates in the base queryset search path.
- Hardened React Native `useChatAuth` so chat rooms recover current user identity from durable user/profile cache when legacy `AUTH_CACHE/USER_KEY` is empty.
- Updated chat tests to use actual mounted API paths instead of stale reverse names.

## Files Changed

- `apps/chat/serializers.py`
- `apps/chat/views.py`
- `apps/chat/tests.py`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/hooks/useChatAuth.ts`

## Validation

Passed:

```bash
python3 -m py_compile apps/chat/views.py apps/chat/serializers.py apps/chat/tests.py
python3 manage.py check
python3 manage.py makemigrations --check --dry-run
python3 manage.py test apps.chat.tests.ConversationUnreadContractTests --noinput --keepdb
pnpm tsc --noEmit --pretty false --incremental false
npx eslint src/Module/ChatRoom/hooks/useChatAuth.ts src/Module/ChatRoom/normalizeConversation.ts src/screens/tabs/MessagesScreen.tsx --quiet
npm run typecheck -- --pretty false
```

The focused Django chat contract ran against the current PostgreSQL-backed test database and passed 11 tests.

## Remaining Messaging Risks

- Real iOS/Android device QA is still required for restart alignment, long conversations, bidirectional latency, media attachments, calls, statuses/updates, and push/realtime unread badges.
- E2EE fallback/history behavior still needs production policy sign-off.
- Calls/WebRTC quality was not proven in this phase.
- Partner messaging and main chat list integration still need product-level QA.

## Best Prompt For Phase 03

```text
Please implement Phase 03 of the KIS 100% Implementation and 80%+ Global Parity roadmap without using git commands. Focus on Notification And Badge Accuracy. Use the Phase 00 launch scope, Phase 01 security evidence, and Phase 02 messaging reliability work to make main-tab notification badges exact and production-ready. Verify backend producers and read-state lifecycle for Messages, Bible, Broadcast/Channels, Partners, Profile, Commerce/Market, Education, and Health. Ensure every badge-counted source has consistent source/type/target_type/target_id metadata, every consumer screen marks the exact source read/viewed, realtime `main_tab_badges.updated` events trigger refresh, and counts decrement immediately when content is consumed. Prefer PostgreSQL-backed Django tests; if Postgres or environment setup blocks validation, record the exact blocker and move on. Preserve existing UI behavior, run safe Django/Nest/React Native validation, update docs/implementation-parity-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 04.
```
