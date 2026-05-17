# Phase 03 - Notification And Badge Accuracy

Date: 2026-05-17

## Goal

Make main-tab notification badges exact enough for launch across Messages, Bible, Broadcast/Channels, Partners, Profile, Commerce/Market, Education, and Health while preserving existing app behavior.

## Scope Completed

- Hardened central notification metadata so new notifications carry stable badge/read-state metadata:
  - `source`
  - `badge_source`
  - `type`
  - `notification_type`
  - `target_type`
  - `target_id`
- Expanded badge source inference so education, health, market, product, service, order, course, lesson, and channel-content notifications map to the correct launch tab.
- Expanded `/api/v1/notifications/mark-source-read/` target aliases for consumer screens:
  - Bible daily passages, meditations, and reading events.
  - Channel content.
  - Education course/lesson content.
  - Health institutions, appointments, and sessions.
  - Market products, services, service bookings, and shops.
  - Partner communities/groups.
  - Chat conversations.
- Confirmed the backend counter endpoint remains the source of truth:
  - `/api/v1/notifications/main-tab-badge-counts/`
- Confirmed React Native already refreshes badge counts from backend and falls back to local inference when unavailable.
- Confirmed React Native listens for local and realtime badge events including:
  - `main_tab_badges.updated`
  - chat/message events
  - broadcast/channel events
  - Bible schedule/meditation events
  - partner/community events
- Confirmed key consumer screens already call `markMainTabNotificationSourceRead` for:
  - Bible tabs.
  - Broadcast market shops.
  - Market product detail.
  - Education detail sheets.
  - Health institution detail.
  - Partner communities and partner groups.
- Added focused backend regression coverage proving badge increment/decrement and metadata exactness across launch sources.

## Files Changed

- `apps/notifications/services.py`
- `apps/notifications/views.py`
- `apps/notifications/tests.py`
- `docs/implementation-parity-roadmap/phase-03-notification-badge-accuracy.md`
- `docs/implementation-parity-roadmap/status.md`
- `docs/BUILD_STATE.md`

## Validation

Passed:

- `python3 -m py_compile apps/notifications/services.py apps/notifications/views.py apps/notifications/tests.py`
- `python3 manage.py check`
- `python3 manage.py makemigrations --check --dry-run`
- `python3 manage.py test apps.notifications.tests.MainTabBadgeCountsAPITest apps.notifications.tests.NotificationAPITest --noinput --keepdb`
  - PostgreSQL-backed: 9 tests passed.
- React Native `npx eslint src/services/mainTabNotificationBadges.ts src/navigation/AppNavigator.tsx --quiet`
- React Native `npm run typecheck -- --pretty false`
- Nest `pnpm tsc --noEmit --pretty false --incremental false`

## Validation Notes

- Local realtime badge emission logs `Connection refused` when Nest is not running or not accepting the internal callback. This is expected local behavior: notification mutations still succeed, and React Native refreshes on focus/local events/fallback events.
- Production still needs an end-to-end realtime proof where Django emits `main_tab_badges.updated`, Nest accepts the signed internal call, and iOS/Android refresh the backend counter immediately.

## Remaining Risks

| Priority | Risk |
|---|---|
| P0 | Real-device proof is still needed for badge decrement timing across all tabs. |
| P0 | Producer coverage must be checked in staging for every real notification producer, not only synthetic regression notifications. |
| P1 | Profile badge currently represents total unread in-app notifications; this may intentionally overlap with source-specific tab badges unless product decides Profile should count only account/profile notifications. |
| P1 | Channel watch-history badge decrement depends on content consumer screens recording viewed/watch history consistently. |
| P1 | Bible missed reading schedules include local and backend-derived state; full parity needs real-device schedule QA. |

## Phase 04 Prompt

```text
Please implement Phase 04 of the KIS 100% Implementation and 80%+ Global Parity roadmap without using git commands. Focus on Media Safety And Christian Moderation Production Proof. Use the Phase 00 launch scope and Phase 01-03 evidence to prove and tighten the central media safety gate across DMs, group/partner messages, feeds/channels, comments, profile media, commerce, education, health, verification, and public embeds. Ensure MIME/extension/size validation, private-media handling, quarantine/review states, explicit-content provider flags disabled by default, staff moderation queues, report/appeal hooks, child/youth-safe defaults, audit logs, and user-safe blocked/review messages are wired where safe. Prefer PostgreSQL-backed Django tests; if Postgres or environment setup blocks validation, record the exact blocker and move on. Preserve existing UI behavior, do not expose secrets or raw storage paths, run safe Django/Nest/React Native validation, update docs/implementation-parity-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 05.
```
