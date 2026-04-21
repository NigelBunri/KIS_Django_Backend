# Broadcast Feeds Progress

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
