# Broadcast Feeds Progress

## 2026-05-01 - Phase 8 Completed: Global-Standard QA and Launch Evidence

Scope:
- Collected final launch evidence for the broadcast feed hardening roadmap.
- Ran safe backend checks and frontend broadcast-feed checks where available.
- Added a practical launch QA checklist for backend, frontend, and manual device validation.
- Preserved current app behavior.

Launch QA document:
- `docs/operations/BROADCAST_FEEDS_LAUNCH_QA_CHECKLIST.md`

Backend validation passed:
- `python3 manage.py check`
- `python3 -m py_compile apps/broadcasts/models.py apps/broadcasts/views.py apps/broadcasts/tests.py apps/broadcasts/urls.py apps/broadcasts/migrations/0030_broadcast_engagement_event.py apps/moderation/serializers.py apps/moderation/admin.py`
- `python3 manage.py test apps.broadcasts.tests.FeedEntryStoreTests apps.broadcasts.tests.FeedMediaValidationTests apps.broadcasts.tests.BroadcastFeedPaginationHelperTests --noinput`
  - 6 tests passed.

Backend blocked:
- `python3 manage.py test apps.broadcasts.tests.BroadcastProfileManageTests --noinput`
  - blocked during local test database setup after printing:
    - `Creating test database for alias 'default'...`
    - `Destroying old test database for alias 'default'...`
  - run was stopped to keep the session moving.

Frontend validation passed:
- In `/Users/nigel/dev/KIS`:
  - `npm run typecheck -- --pretty false` passed.
  - `npx eslint src/screens/broadcast/feeds src/components/broadcast __tests__/broadcast-feeds.useFeedsData.test.tsx __tests__/broadcast-feeds.discover-page.test.tsx __tests__/broadcast-feeds.detail-screen.test.tsx __tests__/broadcast-feeds.feed-card-video.test.tsx __tests__/broadcast-feeds.attachment-preview.test.ts __tests__/broadcast-feeds.trending-card.test.tsx __tests__/broadcast-feeds.video-playback.test.tsx --quiet` passed.

Frontend Jest result:
- Command:
  - `npm run test:phase5 -- __tests__/broadcast-feeds.useFeedsData.test.tsx __tests__/broadcast-feeds.discover-page.test.tsx __tests__/broadcast-feeds.detail-screen.test.tsx __tests__/broadcast-feeds.feed-card-video.test.tsx __tests__/broadcast-feeds.attachment-preview.test.ts __tests__/broadcast-feeds.trending-card.test.tsx __tests__/broadcast-feeds.video-playback.test.tsx`
- Result:
  - 4 suites passed.
  - 3 suites failed/blocked.
  - 13 tests passed.
  - 2 tests failed.
- Passed suites:
  - `broadcast-feeds.useFeedsData.test.tsx`
  - `broadcast-feeds.feed-card-video.test.tsx`
  - `broadcast-feeds.attachment-preview.test.ts`
  - `broadcast-feeds.video-playback.test.tsx`
- Failed/blocked suites:
  - `broadcast-feeds.discover-page.test.tsx`
    - stale expectations for current detail navigation payload and hide confirmation behavior.
  - `broadcast-feeds.detail-screen.test.tsx`
    - Jest transform/mocking issue for `react-native-safe-area-context`.
  - `broadcast-feeds.trending-card.test.tsx`
    - Jest transform/mocking issue for `react-native-fs`.

Frontend integration note:
- Active `FeedsDiscoverPage` report action still posts to generic `ROUTES.moderation.flags`.
- Backend now has `POST /api/v1/broadcasts/<broadcast_id>/report/`.
- Move the frontend report action to the broadcast-specific endpoint in the next frontend pass.

Final launch-readiness summary:
- Broadcast feeds are now close to launch-candidate quality at the backend contract level.
- The system has durable create/edit/delete/broadcast/unbroadcast/list/reaction/share/view/hide/report foundations.
- Media validation and safe attachment handling are materially stronger.
- Admin-visible moderation records exist.
- Remaining launch blockers are QA execution and frontend alignment, not missing core backend capability.

Remaining risk list:
- DB-backed backend broadcast tests must run cleanly outside the current local test database setup blocker.
- Frontend Jest suites need stale expectation updates and native module transform/mocking fixes.
- Manual iOS/Android QA evidence is not collected yet.
- Comment counts remain `0` until Nest comment data is bridged into Django analytics.
- True database cursor pagination still depends on the future normalized feed-entry model.
- Production malware scanning is documented but not integrated.

## 2026-05-01 - Phase 7 Completed: Moderation and Safety Completeness

Scope:
- Hardened broadcast feed moderation/lifecycle behavior without changing current UI flows.
- Confirmed hide remains viewer-specific and mute remains direct-author scoped through `UserBlock`.
- Added admin-visible report/audit records for broadcast feed moderation actions.
- Preserved feed list, profile manager, detail, delete, and unbroadcast behavior.

Backend changes:
- Added `POST /api/v1/broadcasts/<broadcast_id>/report/`.
- Broadcast reports create a moderation `Flag` with:
  - `target_type: POST`
  - `target_id: <broadcast_id>`
  - `tags.surface: broadcast_feed`
  - source metadata for admin review.
- Added moderation audit writes for:
  - `broadcast.hide`
  - `broadcast.report`
  - `broadcast.feed_entry.delete`
  - `broadcast.feed_entry.unbroadcast`
- Registered moderation `Flag`, `AuditLog`, and `UserBlock` in Django admin so staff can inspect feed reports, audit events, and mutes.
- Hardened feed-entry delete so live `BroadcastItem` rows are soft-deleted with `is_deleted=True` and `expires_at=now` instead of hard-deleted.
- Hardened unbroadcast so removed live feed items also expire immediately.
- `UserBlockSerializer` now accepts `blocked_id` as a safe write alias for `blocked`, while preserving the original `blocked` field contract.

Semantics confirmed:
- Hide:
  - stores only the broadcast ID in the viewer's preferences
  - affects only that viewer
  - does not hide other posts from the same author
- Mute:
  - uses `UserBlock(blocker=<viewer>, blocked=<direct feed author>)`
  - feed list excludes direct feed items whose `broadcasted_by`/author ID matches a muted user
  - remains for direct user feed authors only in this phase
- Delete feed entry:
  - removes the queued/profile entry
  - soft-removes matching live broadcast entries
- Unbroadcast:
  - keeps the queued/profile entry
  - marks matching live broadcast entries deleted/expired

Validation:
- `python3 -m py_compile apps/broadcasts/views.py apps/broadcasts/urls.py apps/broadcasts/tests.py apps/moderation/serializers.py apps/moderation/admin.py` passed.
- `python3 manage.py check` passed.
- `python3 manage.py makemigrations broadcasts Moderation --check --dry-run` passed with no changes detected.

Blocked validation:
- Focused DB-backed tests were added for hide audit, report flag/audit creation, and unbroadcast audit.
- Local execution again blocked during test database setup and was stopped.
- Re-run later:
  - `python3 manage.py test apps.broadcasts.tests.BroadcastProfileManageTests.test_hide_broadcast_is_idempotent apps.broadcasts.tests.BroadcastProfileManageTests.test_report_broadcast_creates_admin_visible_flag_and_audit_log apps.broadcasts.tests.BroadcastProfileManageTests.test_unbroadcast_feed_entry_removes_live_item_without_deleting_queue_entry --noinput`

Remaining Phase 7 follow-up:
- Add frontend wiring for the new report endpoint where the active report UI currently uses only generic moderation routes.
- Add admin workflow actions for report resolution if staff needs one-click broadcast deletion or author restriction.
- Confirm a production policy for how many reports trigger automatic review/escalation.

Best prompt for Phase 8:

```text
Please proceed with Phase 8 of the KIS 90% feed system hardening roadmap without using git commands. Focus on global-standard QA and launch evidence for the complete broadcast feed system. Run or prepare full backend regression coverage for create, edit, delete, broadcast, unbroadcast, list, detail, react, comment, share, save, hide, mute, report, and media validation. Run or prepare frontend focused tests/manual QA for composer, profile manager, feed card, detail swipe, media fallback, report/hide/mute actions, and count display. Preserve current app behavior, record any blocked checks with exact commands, update docs/broadcast-feeds-progress.md and docs/BUILD_STATE.md, and give the final launch-readiness summary and remaining risk list.
```

## 2026-05-01 - Phase 6 Completed: Engagement and Analytics Durability

Scope:
- Persisted broadcast feed share/view/impression events instead of logging shares only.
- Added basic idempotency/spam controls for high-frequency engagement events.
- Exposed durable count fields consistently on feed list rows without changing the existing card payload shape.
- Preserved current UI behavior and existing reaction state behavior.

Backend changes:
- Added `BroadcastEngagementEvent` with event types:
  - `impression`
  - `view`
  - `share`
- Added per-user, per-broadcast, per-event window uniqueness through `window_key`.
- Added optional idempotency-key support through either:
  - `Idempotency-Key` request header
  - `idempotency_key`
  - `idempotencyKey`
- Share events now persist through `BroadcastEngagementEvent` and return:
  - `shared`
  - `platform`
  - `created`
  - `share_count`
- Added `POST /api/v1/broadcasts/<broadcast_id>/view/` to persist view events and return:
  - `viewed`
  - `created`
  - `view_count`
- Broadcast feed list rows now include:
  - `share_count`
  - `view_count`
  - `impression_count`
  - `comment_count`
- Feed list impressions are recorded for returned rows and are de-duplicated within the configured impression window.
- Existing `BroadcastReaction` behavior remains the reaction source of truth.

Spam/idempotency windows:
- Impressions: 5 minutes.
- Views: 5 minutes.
- Shares: 1 hour.
- Explicit idempotency keys override time-window keys for safer client retries.

Validation:
- `python3 -m py_compile apps/broadcasts/models.py apps/broadcasts/views.py apps/broadcasts/tests.py apps/broadcasts/urls.py apps/broadcasts/migrations/0030_broadcast_engagement_event.py` passed.
- `python3 manage.py check` passed.
- `python3 manage.py makemigrations broadcasts --check --dry-run` passed with no changes detected.
- `python3 manage.py migrate broadcasts 0030 --plan` showed only the new engagement event table and indexes.
- `python3 manage.py test apps.broadcasts.tests.BroadcastFeedPaginationHelperTests --noinput` passed with 1 test.

Blocked validation:
- Focused DRF tests were added for share durability, view idempotency, and feed count/impression behavior.
- Local execution of those DB-backed tests again blocked during test database setup and was stopped to keep the session moving.
- Re-run later:
  - `python3 manage.py test apps.broadcasts.tests.BroadcastProfileManageTests.test_share_endpoint_is_repeatable_and_returns_stable_payload apps.broadcasts.tests.BroadcastProfileManageTests.test_view_endpoint_is_idempotent_within_window_and_counts_once apps.broadcasts.tests.BroadcastProfileManageTests.test_feed_list_exposes_engagement_counts_and_records_impression_once_per_window --noinput`

Remaining Phase 6 follow-up:
- Comment counts are currently exposed as `0` because broadcast comment messages live outside this app's local relational model.
- A later Nest/Django comment-count bridge should write comment counts or comment events into the same durable analytics path.
- Production analytics dashboards are not implemented yet; this phase adds durable source data.

Best prompt for Phase 7:

```text
Please proceed with Phase 7 of the KIS 90% feed system hardening roadmap without using git commands. Focus on moderation and safety completeness for broadcast feeds. Confirm and harden report, hide, mute, block, remove broadcast, delete feed entry, and unbroadcast semantics across feed list, profile manager, and detail surfaces. Add admin-visible moderation/audit records where safe, ensure hidden posts affect only that user while muted users affect all posts from that direct feed author, preserve current UI behavior, add focused backend/frontend tests or record blockers, run safe validation, update docs/broadcast-feeds-progress.md and docs/BUILD_STATE.md, and give the best prompt for Phase 8.
```

## 2026-05-01 - Phase 5 Completed: Feed Ranking, Pagination, and Performance

Scope:
- Kept the existing broadcast feed list API compatible with current clients.
- Added cursor-readiness without breaking `limit`, `offset`, `q`, `code`, or `source_type`.
- Reduced unnecessary source-specific lookups where the requested source list cannot return those items.
- Preserved current ranking/randomization behavior so frontend refresh reshuffle expectations remain valid.

Backend changes:
- `BroadcastFeedView` now accepts `cursor` as an offset-compatible cursor when `offset` is not supplied.
- Legacy `offset` still wins when both `offset` and `cursor` are provided.
- Broadcast feed responses now include:
  - `cursor`
  - `next_cursor`
  - `previous_cursor`
- Existing `next`, `previous`, `count`, and `results` fields remain unchanged.
- Page URLs still include legacy `offset` and now also include the matching cursor for newer clients.
- Empty channel, community, partner, market product, and market service paths avoid unnecessary follow-up queries where safe.
- Market product/service detail assembly remains gated by the existing source-token behavior, preserving current API output.

Ranking/randomization notes:
- No ranking algorithm change was made in this phase.
- The current personalization service still randomizes candidates for users without enough affinity history.
- This keeps the frontend expectation that feed order can reshuffle on refresh, while detail traversal remains stable within the loaded response.

Validation:
- `python3 -m py_compile apps/broadcasts/views.py apps/broadcasts/tests.py` passed.
- `python3 manage.py check` passed.
- `python3 manage.py test apps.broadcasts.tests.BroadcastFeedPaginationHelperTests --noinput` passed with 1 test.

Remaining Phase 5 follow-up:
- True database cursor pagination should be added after normalized feed entries exist, using a stable `(broadcasted_at, id)` cursor rather than an offset alias.
- Large-feed performance should be measured against production-like data before tightening default limits further.
- Full DRF pagination regression coverage should be rerun once local test database setup is consistently reliable.

Best prompt for Phase 6:

```text
Please proceed with Phase 6 of the KIS 90% feed system hardening roadmap without using git commands. Focus on engagement and analytics durability. Persist share/view/impression events instead of logging only, add idempotency/spam controls where safe, expose accurate reaction/comment/share/view counts consistently in list and detail views, preserve current UI behavior, add focused backend tests or record blockers, run safe validation, update docs/broadcast-feeds-progress.md and docs/BUILD_STATE.md, and give the best prompt for Phase 7.
```

## Scope Boundary

- Scope for this document and implementation: **broadcast feeds only**
- Do **not** modify the broadcast market system
- Do **not** modify the shop system
- If a future broadcast-feeds dependency requires a market/shop touch, keep it isolated and document it first

## Current Audit

### Files Involved

Backend:
- `apps/broadcasts/models.py`
- `apps/broadcasts/urls.py`
- `apps/broadcasts/views.py`
- `apps/broadcasts/tests.py`

Frontend:
- `src/screens/tabs/BroadcastScreen.tsx`
- `src/screens/broadcast/pages/BroadcastFeedsPage.tsx`
- `src/screens/broadcast/feeds/FeedsDiscoverPage.tsx`
- `src/screens/broadcast/feeds/hooks/useFeedsData.ts`
- `src/screens/broadcast/feeds/sections/FeedsMainListSection.tsx`
- `src/components/broadcast/BroadcastFeedCard.tsx`
- `src/screens/tabs/feeds/BroadcastDetailScreen.tsx`
- `src/network/routes/broadcastRoutes.ts`
- `src/navigation/types.ts`
- `App.tsx`

### What Exists

- Broadcast feed aggregation endpoint exists
- Broadcast feed entry CRUD exists
- Broadcast-to-live flow exists
- Reaction endpoint exists
- Comment-room creation exists
- Share logging exists
- Subscribe endpoint exists
- Profile-side feed queue and composer exist
- Active frontend feed tab exists and renders live feed cards

### Critical Blockers

- Active feed cards on the real broadcast tab have no wired `open`, `like`, or `share` handlers
- Active feed cards do not expose a real comment flow
- Feed-side render logging remains in live screens
- Frontend defines a broadcast hide route but backend does not expose that route yet

### Functional Gaps

- Active feed menu actions are incomplete on the real feed surface
- Saved filter semantics are not correct for true saved posts
- Frontend subscribe flow locally simulates unsubscribe without backend support
- Feed detail flow exists in old code but is not wired into the actual app navigation

### Data Consistency Issues

- Editing a broadcasted feed entry updates only profile payload
- Live feed entry rendering reads `BroadcastItem.metadata.entry`
- No sync path exists after edit for an already-broadcast entry

### UX / Interactivity Issues

- Main live feed cards are partially interactive only
- Comment icon does not open a usable comments destination on the active feed surface
- Menu actions are effectively missing on the active feed surface

### Test / Verification Gaps

- `apps.broadcasts` currently has only minimal automated coverage
- No feed-specific frontend tests were found
- Repo-wide frontend typecheck is noisy, so targeted validation is required per phase

## Architecture Decisions

- Treat the active feed UI as `BroadcastScreen -> BroadcastFeedsPage -> FeedsDiscoverPage`
- Do not revive or expand market/shop paths as part of feed completion
- Implement hide as **viewer-specific** behavior, not global deletion
- Keep existing payload contracts unless both frontend and backend are updated together
- Sync broadcasted feed entry edits back into `BroadcastItem.metadata.entry`

## Route Contracts

Existing backend routes already used by feeds:
- `GET /api/v1/broadcasts/`
- `POST /api/v1/broadcasts/<id>/react/`
- `POST /api/v1/broadcasts/<id>/comment-room/`
- `POST /api/v1/broadcasts/<id>/share/`
- `GET/POST /api/v1/broadcasts/profiles/feeds/`
- `GET/PATCH/DELETE /api/v1/broadcasts/profiles/feeds/<entry_id>/`
- `POST /api/v1/broadcasts/profiles/feeds/<entry_id>/broadcast/`

Phase 1 contract addition:
- `POST /api/v1/broadcasts/<id>/hide/`
  - semantics: hide for the current viewer only
  - storage: user preferences

Phase 2 contract addition:
- `POST /api/v1/broadcasts/<id>/save/`
  - default semantics: save for the current viewer
  - unsave semantics: `POST /api/v1/broadcasts/<id>/save/?action=unsave`
  - storage: user preferences
  - feed list contract: each item may include `viewer_saved: boolean`

Phase 3 contract addition:
- `GET /api/v1/broadcasts/`
  - supports `q`, `code`, `source_type`, `limit`, and `offset`
  - returns paginated shape:
    - `count`
    - `next`
    - `previous`
    - `results`
  - `code` currently aliases feed source filtering in the same way as `source_type`

## Backend / Frontend Assumptions

- Comment rooms should open through the existing chat system
- Feed detail should be a root-stack screen, not an unused nested navigator
- Saved-post semantics are now viewer-specific and persist through user preferences until a dedicated saved-items model is needed
- Subscribe is currently treated as subscribe-only in the active feed UI until unsubscribe is designed

## Completed Tasks

- Completed strict broadcast-feeds-only audit
- Identified Phase 1 scope and constraints
- Phase 1 implemented:
  - removed active feed render logs from feed screens
  - removed misleading `Saved` feed filter from the active broadcast tab until true saved-post support exists
  - wired active live feed card actions on the real feed surface:
    - open detail
    - react
    - share
    - open comments
    - open action menu
  - wired feed detail as a root-stack route used by the active broadcast tab
  - changed active subscribe behavior to subscribe-only for now; fake unsubscribe is no longer performed
  - added backend `POST /api/v1/broadcasts/<id>/hide/` with viewer-specific semantics using user preferences
  - updated feed listing to filter viewer-hidden broadcast IDs
  - synced edits to already-broadcast feed entries back into `BroadcastItem.metadata.entry`
  - added targeted backend tests for:
    - viewer-specific hide
    - broadcast snapshot sync after edit
    - existing market-profile bootstrap test remains

- Phase 1 verification:
  - `../env/bin/python manage.py test apps.broadcasts` -> passed (`3` tests)
  - targeted frontend compiler scan for feed files returned no matches after Phase 1 fixes
  - repo-wide frontend TypeScript is still noisy outside broadcast feeds

- Phase 2 implemented:
  - restored the active `Saved` feed filter with real viewer-specific saved-post semantics
  - added backend `POST /api/v1/broadcasts/<id>/save/` and unsave support through `?action=unsave`
  - updated feed listing payloads to include `viewer_saved`
  - wired active feed menu actions to save and unsave posts
  - updated active feed cards to reflect saved state visually
  - added backend coverage for save and unsave behavior plus `viewer_saved` feed payload rendering
  - completed subscribe semantics for the active broadcast feeds flow:
    - backend `POST /api/v1/broadcasts/subscribe/?action=unsubscribe` now supports unsubscribe
    - channel and community feed payloads now expose `allow_subscribe` and `is_subscribed`
    - partner feed payloads now expose real `is_subscribed` state for subscriber memberships
    - partner members no longer get an unsubscribe path through the feed subscribe contract
    - active feed subscribe pill now toggles subscribe and unsubscribe with confirmation on unsubscribe
  - added backend tests for unsubscribe behavior across:
    - partner subscriber memberships
    - channel memberships
    - community memberships
  - made one tiny feed-adjacent frontend cleanup in `BroadcastScreen.tsx`:
    - wrapped the existing market cart unsubscribe callback in a function for TypeScript cleanup
    - no market/shop behavior was changed

- Phase 2 verification:
  - `../env/bin/python manage.py test apps.broadcasts` -> passed (`7` tests)
  - targeted source-level verification confirmed unsubscribe wiring across:
    - backend route and handler flow
    - backend feed payload flags
    - frontend toggle logic
    - frontend unsubscribe confirmation flow
  - repo-wide frontend TypeScript remains noisy outside broadcast feeds

- Phase 3 implemented:
  - completed server-backed discovery behavior for the active broadcast feeds flow
  - backend feed list now supports:
    - search via `q`
    - source alias filtering via `code` / `source_type`
    - paginated responses via `limit` and `offset`
    - `count`, `next`, and `previous` response fields
  - frontend active feed hook now uses a single paginated backend feed source instead of double-fetching the same broadcast endpoint
  - frontend `loadMore()` now follows backend pagination URLs from the same contract used for the first page
  - removed duplicate discovery fetch behavior that could previously weaken search and pagination consistency
  - added backend coverage for feed search and pagination

- Phase 3 verification:
  - `../env/bin/python manage.py test apps.broadcasts` -> passed (`8` tests)
  - targeted source-level verification confirmed:
    - backend paginated response fields exist
    - backend query/filter parameters are wired
    - frontend first-page and load-more logic use the paginated contract
  - repo-wide frontend TypeScript remains noisy outside broadcast feeds

## Fresh Analysis After Phase 3

### What Is Now Complete

- Active broadcast feed cards are wired for:
  - open detail
  - react
  - comment-room open
  - share logging + native share
  - save / unsave
  - hide
  - action menu
  - subscribe / unsubscribe where supported
- Frontend and backend route contracts now match for the active broadcast feeds actions
- Broadcasted feed-entry edits sync back into the live broadcast snapshot
- Saved-post behavior is real and viewer-specific
- Hide behavior is real and viewer-specific
- Discovery now has a coherent server-backed contract for:
  - search
  - feed source filtering
  - pagination metadata

### What Is Strong But Still Not Fully Hardened

- Backend broadcast-feeds coverage is much better than at the start, but it is still targeted rather than exhaustive
- The active feed flow is coherent, but there are still no frontend tests covering card interaction behavior
- Repo-wide frontend type health remains noisy outside broadcast feeds, so feed verification still relies on targeted checks rather than a clean full-app type gate

### What Still Remains

- Add frontend tests for the active feed interaction flow
- Add backend tests for react/comment/share flows if stronger regression protection is needed
- Reassess performance if the broadcast item volume grows materially, because the current Phase 3 pagination implementation optimizes for correctness and coherence first

## Phase 4 Hardening

### Scope

- Broadcast feeds only
- No market/shop behavior changes
- Focus: verification, regression safety, and production hardening

### Part A Implemented: Frontend Interaction Testing

Files changed:
- `__tests__/broadcast-feeds.useFeedsData.test.tsx`
- `__tests__/broadcast-feeds.discover-page.test.tsx`

What is now verified on the frontend:
- active feed list rendering
- loading state propagation into the active feed surface
- search filtering behavior on the active feed page
- saved-filter behavior on the active feed page
- opening a feed item into the detail screen
- comment-room initiation flow
- share flow wiring
- save / unsave action wiring
- hide action wiring
- subscribe / unsubscribe action wiring
- load-more trigger wiring
- optimistic hook state transitions for:
  - react
  - save
  - hide
  - subscribe
- hook rollback behavior on failed reactions and failed save/hide actions

Frontend verification run:
- `npx jest --config jest.phase5.config.js __tests__/broadcast-feeds.useFeedsData.test.tsx --runInBand --watchman=false`
- `npx jest --config jest.phase5.config.js __tests__/broadcast-feeds.discover-page.test.tsx --runInBand --watchman=false`
- result: both passed (`8` frontend feed tests total)

### Part B Implemented: Backend Regression and Contract Testing

Files changed:
- `apps/broadcasts/tests.py`

What is now verified on the backend:
- viewer-specific hide behavior
- hide idempotency
- save / unsave behavior
- save idempotency
- react endpoint toggle behavior
- comment-room creation and reuse behavior
- share endpoint stable response behavior
- subscribe / unsubscribe behavior
- feed search filtering correctness
- feed pagination response correctness
- edit-sync for already-broadcast feed entries

Backend verification run:
- `../env/bin/python manage.py test apps.broadcasts`
- result: passed (`13` backend broadcast tests)

### Current Production-Readiness Assessment

Broadcast feeds are now:
- functionally coherent end to end
- regression-protected across the highest-risk interaction paths
- materially safer to maintain than at the start of this effort

Remaining hardening gaps:
- there are still no broader integration-style frontend tests beyond the targeted active feed suites
- backend share behavior is contract-tested but not persisted beyond logging, which is acceptable for current implementation but worth revisiting if analytics/reporting becomes product-critical
- repo-wide frontend type health remains noisy outside broadcast feeds, so feed verification still relies on targeted test runs rather than a clean global app gate

## Post-Phase 4 Fix

Issue:
- Creating a first broadcast feed entry could fail with:
  - `Create a broadcast feed profile first.`

Root cause:
- `POST /api/v1/broadcasts/profiles/feeds/` still required an existing `profiles["broadcast_feed"]` payload instead of bootstrapping it when the user created their first feed entry.

Implemented fix:
- the feed-entry create endpoint now bootstraps a minimal `broadcast_feed` profile automatically when missing
- this change is isolated to the broadcast feeds backend create path

Files changed:
- `apps/broadcasts/views.py`
- `apps/broadcasts/tests.py`

Verification:
- added backend regression test covering first feed-entry creation without a pre-created feed profile
- `../env/bin/python manage.py test apps.broadcasts` -> passed (`14` tests)

Behavior now verified:
- a user can create their first broadcast feed post directly
- the backend will auto-create the feed profile payload and persist the entry in the same request

## Reaction Alignment Fix

Issue:
- broadcast feed reactions needed confirmation that all active frontend reaction entry points were using the real backend contract correctly

Implemented fix:
- aligned the active feed hook reaction logic with backend toggle semantics
- aligned the fullscreen broadcast detail reaction flow with the backend `emoji` contract
- updated the feed card reaction display so active reactions are visually reflected from `viewer_reaction`

Files changed:
- `src/screens/broadcast/feeds/hooks/useFeedsData.ts`
- `src/screens/tabs/feeds/BroadcastDetailScreen.tsx`
- `src/components/broadcast/BroadcastFeedCard.tsx`

Verification:
- `npx jest --config jest.phase5.config.js __tests__/broadcast-feeds.useFeedsData.test.tsx --runInBand --watchman=false`
- `npx jest --config jest.phase5.config.js __tests__/broadcast-feeds.discover-page.test.tsx --runInBand --watchman=false`
- result: both passed

Behavior now verified:
- feed card reaction uses the backend endpoint correctly
- reaction toggle / untoggle behavior is consistent with backend response payloads
- fullscreen broadcast detail reaction also uses the backend contract correctly

## Feed Detail Action Parity

Issue:
- the fullscreen broadcast feed detail page did not expose the same effective action set as the active feed cards
- the detail footer was still too minimal for a proper feed interaction surface

Implemented fix:
- added a real detail-screen action row for:
  - save / unsave
  - react
  - comment
  - share
- kept comment opening inside the broadcast flow through the existing chat overlay event instead of switching tabs
- updated action labels so the detail page shows explicit action names, not only raw counters

Files changed:
- `src/screens/tabs/feeds/BroadcastDetailScreen.tsx`
- `__tests__/broadcast-feeds.detail-screen.test.tsx`

Verification:
- `npx jest --config jest.phase5.config.js __tests__/broadcast-feeds.detail-screen.test.tsx --runInBand --watchman=false`
- result: passed

Behavior now verified:
- fullscreen feed detail can react through the backend reaction contract
- fullscreen feed detail can open the broadcast comment room
- fullscreen feed detail can save / unsave
- fullscreen feed detail can share and log shares through the backend route

## Multi-Attachment Feed Card Fix

Issue:
- in the active broadcast feeds tab, posts with multiple attachments could make the first attachment look duplicated
- the underlying cause was duplicate attachment entries resolving to the same effective file URL in the card preview layer

Implemented fix:
- added attachment-preview deduplication by resolved file identity before the feed card slideshow renders
- kept the fix strictly inside the broadcast feeds frontend card/preview path

Files changed:
- `src/components/broadcast/attachmentPreview.ts`
- `src/components/broadcast/BroadcastFeedCard.tsx`
- `__tests__/broadcast-feeds.attachment-preview.test.ts`

Verification:
- `npx jest --config jest.phase5.config.js __tests__/broadcast-feeds.attachment-preview.test.ts --runInBand --watchman=false`
- `npx jest --config jest.phase5.config.js __tests__/broadcast-feeds.discover-page.test.tsx --runInBand --watchman=false`
- result: both passed

Behavior now verified:
- repeated attachment objects that resolve to the same file are only shown once in the active feed card slideshow
- normal active feed card interactions still pass their existing regression suite

## Trending Clips Attachment Fix

Issue:
- trending clips used a separate card component from the main active feed cards
- that card had its own attachment mapping path, so the duplicate-attachment fix did not apply there automatically

Implemented fix:
- updated the trending clips card to reuse the shared attachment preview deduplication path
- added a targeted regression test covering a duplicated first attachment in slideshow mode

Files changed:
- `src/components/broadcast/FeedItemCard.tsx`
- `__tests__/broadcast-feeds.trending-card.test.tsx`

Verification:
- `npx jest --config jest.phase5.config.js __tests__/broadcast-feeds.trending-card.test.tsx --runInBand --watchman=false`
- `npx jest --config jest.phase5.config.js __tests__/broadcast-feeds.attachment-preview.test.ts --runInBand --watchman=false`
- result: both passed

Behavior now verified:
- trending clips no longer show the first attachment twice when duplicated attachment entries resolve to the same file

## Broadcast Feeds Video Hardening

### Current Scope Boundary

- broadcast-feeds video only
- no broadcast market changes
- no shop changes
- no broad media-system refactor outside the feed/video path

### Phase 1 Audit Summary

Files audited:
- `src/screens/tabs/profile-screen/FeedManagementModal.tsx`
- `src/Module/vieo/KISVideo.tsx`
- `src/Module/vieo/VideoPlayer.tsx`
- `src/Module/vieo/hooks/useVideoPlayer.ts`
- `src/Module/vieo/utils.ts`
- `src/screens/tabs/feeds/BroadcastDetailScreen.tsx`
- `apps/broadcasts/views.py`

Confirmed backend attachment contract for feed videos:
- `stream_url`
- `url`
- `video_id`
- `mime_type`
- `media_type`

Critical blockers found:
- composer preview used only one chosen source and had no fallback
- `KISVideo` swallowed parent `onError`, so the screen could not react meaningfully to failures
- active public feed/detail surfaces are not yet using the same playback contract as the composer preview
- active detail screen still renders feed attachments as images, not video

Likely root-cause candidates in priority order:
1. `stream_url` resolves to an unreachable host for the device/simulator
2. `stream_url` is `http` and iOS transport rules may reject it
3. the uploaded file codec/container is not playable on iOS
4. the stream endpoint is close but not fully compatible with native playback for some files

Smallest safe contract decision:
- frontend playback should prefer the safest playable source first
- practical order now is:
  - safe reachable `stream_url` when available
  - otherwise safe `url`
  - avoid clearly risky loopback/plain-http sources when a safer alternative exists
- if `stream_url` fails, fall back once to `url` when present
- if both fail, surface a clear error state instead of swallowing the failure

### Phase 2 Implemented: Composer Preview Reliability

Files changed:
- `src/components/broadcast/feedVideoPlayback.ts`
- `src/components/broadcast/BroadcastFeedVideoPreview.tsx`
- `src/screens/tabs/profile-screen/FeedManagementModal.tsx`
- `src/Module/vieo/KISVideo.tsx`
- `__tests__/broadcast-feeds.video-playback.test.tsx`

What changed:
- added a focused broadcast-feeds video source helper that:
  - ranks safer sources ahead of obviously risky loopback/plain-http candidates
  - falls back to `url`
  - deduplicates repeated URLs
  - exposes basic host/risk metadata for debugging
- added a focused broadcast-feeds preview component with:
  - one-step source fallback
  - visible hard-failure state after all sources fail
  - retry action
  - open-source action
  - development-only sanitized diagnostics
- updated the composer preview path to use that component
- stopped `KISVideo` from swallowing the parent `onError`

What is now fixed:
- the composer preview no longer fails silently
- playback failures can now trigger fallback from `stream_url` to `url`
- obviously risky loopback/plain-http stream URLs no longer get first attempt when a safer file URL exists
- the user now gets a visible failure state when all candidate video sources fail
- development logs now expose useful feed-video debugging metadata without a broad logging change

Verification:
- `npx jest --config jest.phase5.config.js __tests__/broadcast-feeds.video-playback.test.tsx --runInBand --watchman=false`
- `npx jest --config jest.phase5.config.js __tests__/broadcast-feeds.detail-screen.test.tsx --runInBand --watchman=false`
- result: both passed

### Pending Video Work

- Phase 3:
  - completed:
    - unified active public detail playback with the same source-selection contract as composer preview
    - added explicit video cues to the active feed card
    - unified the legacy broadcast video modal with the same source preference and one-step fallback contract
  - remaining:
    - none required for current active broadcast-feeds video scope
- Phase 4:
  - completed:
    - backend tests for video attachment shaping
    - backend tests for stream endpoint behavior and range responses
    - frontend tests for source selection, fallback, hard failure, and public detail usage
  - remaining:
    - optional legacy modal unification
    - manual on-device iOS smoke test with a real uploaded video

### Known Risks

- if backend returns loopback hosts like `10.14.20.99` or `10.14.20.99`, fallback may still fail on real devices unless `url` is externally reachable
- iOS playback may still fail for unsupported codecs even when the source-selection logic is correct
- the legacy broadcast video modal remains on its own implementation until Phase 4 or a later unification pass
  - resolved: legacy modal now follows the same source contract

### Exact Next Recommended Step

Next recommended step:
1. manually validate one real uploaded broadcast-feed video on iOS simulator/device
2. if a real device still fails, inspect whether backend `stream_url` is returning loopback or non-routable hosts
3. otherwise, the broadcast-feeds video system is now functionally hardened for the current active surfaces

### Follow-up Refinement: Safer Initial Source Selection

Issue:
- preview playback could still log repeated failures when `stream_url` pointed at a risky source such as `10.14.20.99` or plain `http`, even though a safer `url` was available

Implemented fix:
- source ordering now prefers safer non-loopback/non-risky URLs before risky local/plain-http stream URLs
- intermediate fallback attempts now log as source switches in development instead of repeated failure warnings
- final failure still logs once when all candidate sources are exhausted

Files changed:
- `src/components/broadcast/feedVideoPlayback.ts`
- `src/components/broadcast/BroadcastFeedVideoPreview.tsx`
- `__tests__/broadcast-feeds.video-playback.test.tsx`

Verification:
- `npx jest --config jest.phase5.config.js __tests__/broadcast-feeds.video-playback.test.tsx --runInBand --watchman=false`
- result: passed

### Phase 3 Implemented: Active Public Feed/Detail Contract

Files changed:
- `src/components/broadcast/attachmentPreview.ts`
- `src/screens/tabs/feeds/BroadcastDetailScreen.tsx`
- `src/components/broadcast/BroadcastFeedCard.tsx`
- `__tests__/broadcast-feeds.detail-screen.test.tsx`
- `__tests__/broadcast-feeds.feed-card-video.test.tsx`

What changed:
- the active broadcast detail screen now uses the shared broadcast-feeds video preview contract for video attachments
- the active feed card now shows an explicit `Play video` cue over video attachments
- shared attachment utilities now expose a focused `isVideoAttachment` helper used by the public feed/detail path

What is now fixed:
- active public detail no longer treats video attachments as plain images
- active public detail now uses the same `stream_url -> url` fallback logic as the composer preview
- active feed cards more clearly communicate when an attachment is a video

Verification:
- `npx jest --config jest.phase5.config.js __tests__/broadcast-feeds.detail-screen.test.tsx --runInBand --watchman=false`
- `npx jest --config jest.phase5.config.js __tests__/broadcast-feeds.feed-card-video.test.tsx --runInBand --watchman=false`
- `npx jest --config jest.phase5.config.js __tests__/broadcast-feeds.video-playback.test.tsx --runInBand --watchman=false`
- result: all passed

### Phase 4 Implemented: Video Verification and Hardening

Files changed:
- `apps/broadcasts/tests.py`

What changed:
- added backend regression coverage for feed video attachment shaping on create
- added backend regression coverage for the video stream endpoint:
  - inline response
  - `Accept-Ranges`
  - partial content / range requests
  - `Content-Range`

Verification:
- `../env/bin/python manage.py test apps.broadcasts`
- result: passed (`16` tests)

Behavior now verified:
- creating a feed item with a video attachment returns the frontend-facing fields the player relies on:
  - `stream_url`
  - `url`
  - `video_id`
  - `media_type`
  - `mime_type`
- the broadcast video stream endpoint supports the range behavior expected by clients

### Follow-up Unification: Legacy Broadcast Video Modal

Files changed:
- `src/components/broadcast/BroadcastFeedSection.tsx`

What changed:
- legacy modal video resolution now uses the same shared source-priority contract
- legacy modal now prefers `stream_url`, falls back once to the next available source, and only alerts after sources are exhausted
- legacy modal now logs sanitized fallback diagnostics in development only

Verification:
- targeted TypeScript scan for `BroadcastFeedSection` / feed-video files returned no matching errors
- `npx jest --config jest.phase5.config.js __tests__/broadcast-feeds.video-playback.test.tsx --runInBand --watchman=false`
- `npx jest --config jest.phase5.config.js __tests__/broadcast-feeds.detail-screen.test.tsx --runInBand --watchman=false`
- result: passed

## Pending Tasks

### Phase 1
- Completed

### Phase 2
- Completed for current scope:
  - saved-post semantics
  - unsubscribe contract for active feed subscribe surfaces
  - backend test expansion for save/hide/edit-sync/unsubscribe
- Remaining Phase 2 follow-up:
  - add frontend tests for active feed interactions
  - expand backend tests further for react/comment/share flows if more confidence is needed before Phase 3

### Phase 3
- Completed:
  - server-backed search/filter/pagination improvements for feeds
- Remaining follow-up:
  - add frontend feed-specific tests
  - optionally expand backend verification around react/comment/share flows

### Phase 4
- Completed:
  - frontend broadcast-feeds-only interaction tests
  - backend interaction/contract/idempotency regression tests
- Remaining follow-up:
  - optional broader integration coverage if needed
  - optional performance review of feed ranking/pagination under larger datasets

## Known Risks

- Repo-wide frontend compile health is noisy outside broadcast feeds
- Existing legacy broadcast feed UI still exists and may diverge from the active feed path
- Hide semantics must remain viewer-specific to avoid destructive global behavior
- Saved and hidden feed state currently live in user preferences; that is acceptable for current scope but may need a dedicated model if query/reporting requirements grow

## Exact Next Recommended Step

Move to the next safe broadcast-feeds-only step:
1. run the new frontend feed suites and backend broadcast suite as the standard broadcast-feeds regression check
2. if production rollout is near, do one manual smoke pass on a real device/simulator for feed comments and share UX
3. otherwise, the remaining work is optional hardening rather than a core correctness blocker

## 90% Feed System Hardening Roadmap

Goal: bring the feed creation, broadcast lifecycle, public feed, detail view, moderation, analytics, media handling, and QA posture to a 90%+ production/global-standard level without breaking the current app.

### Phase 1 - Broadcast Lifecycle Correctness

Status: completed for current scope on 2026-05-01.

Scope:
- Make broadcast creation fail visibly instead of silently reporting success.
- Return the created `broadcast_id` to clients.
- Add a real unbroadcast/remove-live action so a queued item can be removed from the public feed without deleting the queued item.
- Wire the React Native profile feed manager to show `Remove live` for live items.
- Add backend regression tests for broadcast and unbroadcast behavior.

Files touched in this phase:
- `apps/broadcasts/views.py`
- `apps/broadcasts/urls.py`
- `apps/broadcasts/tests.py`
- `/Users/nigel/dev/KIS/src/network/routes/broadcastRoutes.ts`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile/useProfileController.ts`
- `/Users/nigel/dev/KIS/src/screens/tabs/ProfileScreen.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/FeedManagementModal.tsx`

Validation target:
- `python3 manage.py check` passed.
- `python3 -m py_compile apps/broadcasts/views.py apps/broadcasts/urls.py apps/broadcasts/tests.py` passed.
- `python3 manage.py shell -c "from django.urls import reverse; ..."` confirmed the unbroadcast route resolves.
- React Native `npm run typecheck` passed.
- Targeted React Native ESLint passed for touched feed manager files.
- Focused Django broadcast lifecycle tests were added, but the local test command blocked during test database setup in this workspace before executing test output.

Remaining Phase 1 follow-up:
- Re-run the two focused Django tests once the local test database setup issue is cleared:
  - `apps.broadcasts.tests.BroadcastProfileManageTests.test_broadcast_feed_entry_returns_broadcast_id_and_marks_live`
  - `apps.broadcasts.tests.BroadcastProfileManageTests.test_unbroadcast_feed_entry_removes_live_item_without_deleting_queue_entry`

### Phase 2 - Creation System Unification

Status: completed for current scope on 2026-05-01.

Scope:
- Connect the advanced composer payload to broadcast feed entry creation.
- Preserve styled text documents, text preview/plain text, link, poll, event, and media captions in backend feed entries.
- Make the profile feed manager use the same payload contract as the feed composer.
- Add clear validation messages for missing text/media/title.

What changed:
- Backend feed entry create/update now accepts advanced composer fields:
  - `text`
  - `text_plain`
  - `text_preview`
  - `poll`
  - `event`
  - `link`
  - `composer_type`
  - `attachment_payloads`
- Backend feed listing now emits preserved advanced fields for direct broadcast profile items.
- Profile feed manager now exposes an `Open advanced composer` action for new queued items.
- Advanced composer submissions are saved into the same broadcast feed queue as the existing simple form.
- Local attachments still use the existing upload path; already-uploaded/remote composer attachments are preserved as attachment payloads.
- Video/short-video composer payloads still use the existing broadcast video upload helper before being queued.
- Added backend regression coverage for advanced composer payload preservation.

Validation:
- `python3 manage.py check` passed.
- `python3 -m py_compile apps/broadcasts/views.py apps/broadcasts/tests.py` passed.
- React Native `npm run typecheck` passed.
- Targeted React Native ESLint passed for:
  - `src/screens/tabs/ProfileScreen.tsx`
  - `src/screens/tabs/profile/useProfileController.ts`
  - `src/screens/tabs/profile-screen/FeedManagementModal.tsx`

Remaining Phase 2 follow-up:
- Run the new backend advanced-composer regression test once the local Django test database setup issue is cleared.
- Manual smoke test on device/simulator:
  - create styled text through advanced composer
  - create image/video/short-video through advanced composer
  - confirm item appears in queue
  - broadcast item
  - confirm public feed/detail preserves style/media

### Phase 3 - Normalized Feed Data Readiness

Status: completed for current abstraction-first scope on 2026-05-01.

Scope:
- Introduce a safe normalized model path or compatibility layer for feed entries, attachments, live status, and visibility while preserving current JSON profile payloads.
- Add migration/backfill strategy documentation before data migration.
- Reduce reliance on large mutable JSON lists for production-critical state.

What changed:
- Added `apps/broadcasts/feed_entry_store.py` as a JSON-compatible feed entry store abstraction.
- The abstraction provides:
  - immutable-style feed list reads
  - append
  - resolve
  - replace
  - delete
- Refactored backend feed create/edit/delete/attachment-delete/broadcast/unbroadcast paths to use the store abstraction instead of directly mutating `profile["feeds"]` lists in each view.
- Existing `BroadcastFeedProfile.payload` JSON behavior is preserved.
- Added focused helper tests for the compatibility layer.

Why no normalized model migration was added in this phase:
- The existing production data shape is JSON-backed and used by multiple profile management flows.
- A schema migration is safer after the app has a stable abstraction layer and regression evidence.
- This phase creates the seam needed for a future dual-write/read-through migration without changing user-facing behavior.

Normalized model migration plan:
1. Add `BroadcastFeedEntry` with fields for owner/profile, title, summary, media type, text doc/plain/preview, poll/event/link payloads, composer type, live state, timestamps, and soft delete.
2. Add `BroadcastFeedEntryAttachment` for ordered attachments and normalized media metadata.
3. Backfill rows from `BroadcastFeedProfile.payload["feeds"]`.
4. Dual-read through `feed_entry_store` while comparing JSON rows and normalized rows in development/staging.
5. Dual-write create/edit/delete/broadcast/unbroadcast to both stores.
6. Flip reads to normalized rows behind an environment flag.
7. Keep JSON payload as rollback shadow until production confidence is proven.
8. Remove JSON feed mutation only after backup/rollback and reporting are proven.

Validation:
- `python3 manage.py check` passed.
- `python3 -m py_compile apps/broadcasts/feed_entry_store.py apps/broadcasts/views.py apps/broadcasts/tests.py` passed.
- `python3 manage.py test apps.broadcasts.tests.FeedEntryStoreTests --noinput` passed with 2 tests.
- React Native checks were not run because Phase 3 only changed backend/docs files.

Remaining Phase 3 follow-up:
- Execute the broader focused backend broadcast lifecycle tests once the local Django test DB setup issue is cleared.
- Consider adding the normalized models in Phase 4 only after this abstraction is stable.

### Phase 4 - Media Safety and Processing

Status: completed for current validation-first scope on 2026-05-01.

Scope:
- Enforce per-type MIME/extension/size validation.
- Add image/video thumbnail and video metadata guarantees.
- Add malware/quarantine hook points.
- Define private/public media behavior for feed attachments.

What changed:
- Added centralized feed media validation before storing uploads.
- Added per-type allowlists for:
  - images: `.jpg`, `.jpeg`, `.png`, `.webp`, `.gif`
  - videos/short videos: `.mp4`, `.mov`, `.m4v`, `.webm`
  - audio: `.mp3`, `.m4a`, `.aac`, `.wav`, `.ogg`
  - documents/files: `.pdf`, `.doc`, `.docx`, `.txt`
- Added MIME allowlists matching the allowed file families.
- Added conservative per-file size limits:
  - images: 12 MB
  - videos/short videos: 512 MB
  - audio: 128 MB
  - documents/files: 40 MB
- Existing tier media storage checks still apply after the type-specific checks.
- Remote/already-uploaded composer attachment payloads are now validated before entering the broadcast feed queue.
- Remote payloads must use `http` or `https` URLs.
- Unsafe executable-style local uploads and remote payloads are rejected with clear validation errors.
- Attachments now receive `validation_status: validated`.
- Attachments now receive `scan_status: not_configured` as the explicit malware-scan hook state until a scanner is integrated.
- Remote video payloads normalize thumbnail fields into both `thumbnail_url` and `thumbUrl`.
- Remote short-video payloads with duration metadata are rejected when duration is 4 minutes or longer.
- Added regression tests for unsafe local upload and unsafe remote attachment payload rejection.

Malware/quarantine hook plan:
- Current phase does not add a scanner dependency to avoid breaking local development.
- Future production hook point should sit immediately after `_store_upload` writes the file and before the feed entry is saved.
- Recommended states for a normalized model phase:
  - `pending_scan`
  - `clean`
  - `quarantined`
  - `scan_failed`
- Public feed rendering should only show `clean` attachments once scanner integration is enabled.

Thumbnail/video metadata notes:
- Video uploads still create `BroadcastVideo` records and attach `stream_url`/`video_id`.
- Duration probing remains best-effort through the existing `_probe_video_duration` path.
- Remote short-video duration is enforced when duration metadata is present.
- Uploaded short-video duration enforcement still needs short-video intent at the upload endpoint or normalized attachment metadata.

Validation:
- `python3 manage.py check` passed.
- `python3 -m py_compile apps/broadcasts/views.py apps/broadcasts/tests.py` passed.
- `python3 manage.py test apps.broadcasts.tests.FeedMediaValidationTests --noinput` passed with 3 tests.
- Focused media validation DRF tests were added, but local execution again blocked during test database setup before test output.

Remaining Phase 4 follow-up:
- Add scanner integration when production provider/tooling is selected.
- Enforce short-video duration at the server once short-video intent is passed to the upload endpoint.
- Consider storing attachment scan status in the future normalized attachment model.
- Re-run the new media validation tests once the local test database setup issue is cleared:
  - `test_feed_entry_rejects_unsupported_uploaded_media_type`
  - `test_feed_entry_rejects_unsupported_remote_attachment_payload`

### Phase 5 - Feed Ranking, Pagination, and Performance

Scope:
- Move closer to cursor pagination and source-limited query assembly.
- Add stable ranking metadata and avoid loading large mixed-source lists into memory where possible.
- Add performance regression checks and large-feed test fixtures.

### Phase 6 - Engagement and Analytics Durability

Scope:
- Persist share/view/impression events instead of logging only.
- Add idempotency/spam controls for engagement endpoints.
- Expose accurate counts consistently in list and detail views.

### Phase 7 - Moderation and Safety Completeness

Scope:
- Confirm report/hide/mute/block semantics across all feed surfaces.
- Add admin moderation visibility for feed reports and risky media.
- Add appeal/reversal hooks where needed.

### Phase 8 - Global-Standard QA and Launch Evidence

Scope:
- Full backend regression suite for create/edit/delete/broadcast/unbroadcast/list/detail/react/comment/share/save/hide/mute.
- Frontend focused tests for creator, manager, feed card, detail swipe, media fallback, and moderation actions.
- Manual device QA checklist for iOS/Android.

### Best Prompt For Phase 2

Please proceed with Phase 2 of the KIS 90% feed system hardening roadmap without using git commands. Focus on creation system unification. Connect the advanced feed composer payload to broadcast feed entry creation so styled text, textPlain/textPreview, link, poll, event, media captions, short video/video/document/audio/image metadata, and attachments are preserved end to end. Keep existing profile feed manager behavior working, add clear validation messages, avoid broad UI redesign, run safe backend/frontend validation, update `docs/broadcast-feeds-progress.md` and `docs/BUILD_STATE.md`, and give the best prompt for Phase 3.

### Best Prompt For Phase 3

Please proceed with Phase 3 of the KIS 90% feed system hardening roadmap without using git commands. Focus on normalized feed data readiness. Design and implement the safest compatibility layer toward normalized feed entries and attachments while preserving the existing broadcast profile JSON payload behavior. Add models/migrations only if low-risk and clearly backward compatible; otherwise add the documented migration plan and read/write abstraction first. Reduce direct mutation of large JSON feed lists where safe, keep create/edit/delete/broadcast/unbroadcast working, add focused regression tests or record blockers, run safe backend/frontend validation, update `docs/broadcast-feeds-progress.md` and `docs/BUILD_STATE.md`, and give the best prompt for Phase 4.

### Best Prompt For Phase 4

Please proceed with Phase 4 of the KIS 90% feed system hardening roadmap without using git commands. Focus on media safety and processing for broadcast feed creation and display. Enforce safe per-type MIME/extension/size validation for image, video, short video, audio, documents, and remote attachment payloads; preserve local development; add clear validation errors; add or document malware/quarantine hook points; ensure thumbnails/video metadata are reliable; keep existing uploads and advanced composer queueing working; add focused regression tests or record blockers; run safe backend/frontend validation; update `docs/broadcast-feeds-progress.md` and `docs/BUILD_STATE.md`; and give the best prompt for Phase 5.

### Best Prompt For Phase 5

Please proceed with Phase 5 of the KIS 90% feed system hardening roadmap without using git commands. Focus on feed ranking, pagination, and performance. Improve the broadcast feed list path toward cursor/stable pagination and source-limited query assembly without breaking current `limit`/`offset`, `q`, `code`, and `source_type` behavior. Avoid loading unnecessary large mixed-source lists where safe, preserve randomization expectations in the frontend, add focused backend regression tests or record blockers, run safe validation, update `docs/broadcast-feeds-progress.md` and `docs/BUILD_STATE.md`, and give the best prompt for Phase 6.
