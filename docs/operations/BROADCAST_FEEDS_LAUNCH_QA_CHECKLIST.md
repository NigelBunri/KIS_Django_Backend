# Broadcast Feeds Launch QA Checklist

Last updated: 2026-05-01

## Scope

This checklist covers the KIS broadcast feed system after the 90% hardening phases:

- feed profile creation and profile manager queue
- advanced composer payload preservation
- media validation and attachment rendering
- broadcast/unbroadcast/delete lifecycle
- public feed list, ranking bridge, pagination, and search
- feed detail navigation and media fallback
- reactions, comments, shares, saves, hides, mutes, reports
- moderation/audit visibility

## Backend Regression Commands

Fast checks that passed during Phase 8:

```bash
python3 manage.py check
python3 -m py_compile apps/broadcasts/models.py apps/broadcasts/views.py apps/broadcasts/tests.py apps/broadcasts/urls.py apps/broadcasts/migrations/0030_broadcast_engagement_event.py apps/moderation/serializers.py apps/moderation/admin.py
python3 manage.py test apps.broadcasts.tests.FeedEntryStoreTests apps.broadcasts.tests.FeedMediaValidationTests apps.broadcasts.tests.BroadcastFeedPaginationHelperTests --noinput
```

DB-backed suite to rerun when local test database setup is healthy:

```bash
python3 manage.py test apps.broadcasts.tests.BroadcastProfileManageTests --noinput
```

Focused DB-backed commands to rerun:

```bash
python3 manage.py test apps.broadcasts.tests.BroadcastProfileManageTests.test_broadcast_feed_entry_returns_broadcast_id_and_marks_live apps.broadcasts.tests.BroadcastProfileManageTests.test_unbroadcast_feed_entry_removes_live_item_without_deleting_queue_entry apps.broadcasts.tests.BroadcastProfileManageTests.test_patch_feed_entry_syncs_existing_broadcast_snapshot apps.broadcasts.tests.BroadcastProfileManageTests.test_delete_feed_attachment_syncs_existing_broadcast_snapshot --noinput
python3 manage.py test apps.broadcasts.tests.BroadcastProfileManageTests.test_share_endpoint_is_repeatable_and_returns_stable_payload apps.broadcasts.tests.BroadcastProfileManageTests.test_view_endpoint_is_idempotent_within_window_and_counts_once apps.broadcasts.tests.BroadcastProfileManageTests.test_feed_list_exposes_engagement_counts_and_records_impression_once_per_window --noinput
python3 manage.py test apps.broadcasts.tests.BroadcastProfileManageTests.test_hide_broadcast_is_idempotent apps.broadcasts.tests.BroadcastProfileManageTests.test_report_broadcast_creates_admin_visible_flag_and_audit_log --noinput
```

## Frontend Regression Commands

Commands run during Phase 8:

```bash
npm run typecheck -- --pretty false
npx eslint src/screens/broadcast/feeds src/components/broadcast __tests__/broadcast-feeds.useFeedsData.test.tsx __tests__/broadcast-feeds.discover-page.test.tsx __tests__/broadcast-feeds.detail-screen.test.tsx __tests__/broadcast-feeds.feed-card-video.test.tsx __tests__/broadcast-feeds.attachment-preview.test.ts __tests__/broadcast-feeds.trending-card.test.tsx __tests__/broadcast-feeds.video-playback.test.tsx --quiet
npm run test:phase5 -- __tests__/broadcast-feeds.useFeedsData.test.tsx __tests__/broadcast-feeds.discover-page.test.tsx __tests__/broadcast-feeds.detail-screen.test.tsx __tests__/broadcast-feeds.feed-card-video.test.tsx __tests__/broadcast-feeds.attachment-preview.test.ts __tests__/broadcast-feeds.trending-card.test.tsx __tests__/broadcast-feeds.video-playback.test.tsx
```

Phase 8 frontend result:

- Typecheck passed.
- Targeted lint passed.
- Jest passed 4 of 7 suites:
  - `broadcast-feeds.useFeedsData.test.tsx`
  - `broadcast-feeds.feed-card-video.test.tsx`
  - `broadcast-feeds.attachment-preview.test.ts`
  - `broadcast-feeds.video-playback.test.tsx`
- Jest failures/blockers:
  - `broadcast-feeds.discover-page.test.tsx` has stale expectations for the current detail navigation payload and hide confirmation behavior.
  - `broadcast-feeds.detail-screen.test.tsx` needs Jest transform/mocking coverage for `react-native-safe-area-context`.
  - `broadcast-feeds.trending-card.test.tsx` needs Jest transform/mocking coverage for `react-native-fs`.

## Manual Device QA

Run this on one iOS simulator/device and one Android emulator/device before launch.

### Composer And Profile Manager

- Create text feed with rich styling; verify styled text appears in profile manager, feed card, and detail.
- Create image feed; verify image preview, broadcast, detail image, and vertical see-all image display.
- Create video feed; verify thumbnail, fallback video source, and playback controls.
- Create short video feed; verify duration expectation and detail swipe behavior.
- Create document feed; verify file metadata and safe preview state.
- Create audio feed; verify metadata and fallback display.
- Create poll, event, and link feed; verify payload persists after edit and broadcast.
- Edit a queued feed entry and confirm live broadcast snapshot updates.
- Delete an attachment and confirm live broadcast snapshot updates.
- Delete a queued feed entry and confirm the live item disappears.

### Feed List And Detail

- Pull-to-refresh feed and confirm order reshuffles only on refresh.
- Open feed detail and swipe up/down through items.
- Verify first-item pull-down refresh and last-item pull-up end message.
- Verify detail action buttons show counts and do not overlap media/text.
- Verify cursor/offset pagination still loads more results.
- Verify `q`, `code`, and `source_type` filters still work.

### Engagement

- React once; verify count increments.
- Tap same reaction again; verify reaction toggles off.
- Change reaction; verify selected emoji changes without duplicate count.
- Open comments; verify comment room is created/reused.
- Share and cancel at OS share sheet; confirm backend share is only called by the app after the intended share flow.
- Save/unsave and verify card state updates.
- Open detail after list engagement and confirm displayed counts match list expectations.

### Moderation

- Hide a post; verify only that post disappears for that user.
- Confirm other posts from the same direct author still appear after hide.
- Mute a direct feed author; verify all posts from that author disappear.
- Confirm mute is not offered or fails gracefully when the feed item has no direct user author.
- Report a broadcast; verify success UI and backend moderation `Flag`/`AuditLog` entries.
- Verify staff can see broadcast feed reports in Django admin or moderation API.

### Media Safety

- Try unsupported local media extension; verify clear validation error.
- Try unsupported remote attachment payload; verify clear validation error.
- Verify large media over tier/storage limit is rejected.
- Verify remote video thumbnail metadata displays.
- Verify broken media URL falls back gracefully without a blank card.

## Launch Readiness Summary

Current readiness: near launch-candidate for broadcast feeds, with important QA blockers still open.

Ready:

- Core backend contracts exist for create/edit/delete/broadcast/unbroadcast/list/react/comment/share/save/hide/report.
- Advanced composer payloads are preserved.
- Media validation is in place for local and remote payloads.
- Engagement events are durable for share/view/impression.
- Moderation/audit records are admin-visible.
- Fast backend checks passed.
- React Native typecheck and targeted lint passed.

Not ready until resolved:

- Local DB-backed backend suite must run cleanly outside the current test database setup blocker.
- Frontend broadcast Jest suites need expectation/config updates.
- Frontend report action should move from generic moderation flags to the broadcast-specific report endpoint.
- Manual iOS/Android device QA evidence is still required.
- Comment counts need a Nest/Django bridge before they can be considered accurate.
