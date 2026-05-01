# React Native Typecheck Triage

The React Native project-wide typecheck is now a clean gate as of Phase 11.
This document preserves the repaired failure groups and tracks the React Native launch gates so future phases can continue safely by domain.

## Command

```bash
cd /Users/nigel/dev/KIS
npm run typecheck
```

## Current Failure Groups

## Phase 15 Focused Regression Test Path

Phase 15 added a no-Watchman focused Jest command for launch regression tests that do not need the full React Native Jest preset:

```bash
cd /Users/nigel/dev/KIS
npm run test:phase5 -- __tests__/broadcast-feeds.video-playback.test.tsx __tests__/phase5.wallet-modal.test.tsx __tests__/phase5.profile-controller.test.tsx
```

Measured status on 2026-04-30:

- `npm run test:phase5 -- __tests__/broadcast-feeds.video-playback.test.tsx __tests__/phase5.wallet-modal.test.tsx __tests__/phase5.profile-controller.test.tsx` passed with 3 suites and 10 tests.
- `npm run typecheck` passed.
- `npx eslint . --quiet` passed.
- `npm run ci:launch` passed.
- `npm audit --omit=dev --legacy-peer-deps` passed through `ci:launch` with 0 vulnerabilities.

Coverage:

- Broadcast feed video fallback source selection and failure state behavior.
- Wallet modal transfer gating before recipient verification.
- Profile controller phone-change/session clearing behavior and wallet verification payload behavior.

Notes:

- Default `npm test` still uses the broader React Native Jest preset and may invoke Watchman.
- Use `npm run test:phase5 -- <files>` for focused launch regression checks until the broader Jest preset is repaired.
- Service booking and health appointment helper tests remain future candidates; runtime QA checklist coverage is the current launch control.

## Phase 14 Runtime QA Confidence

Phase 14 did not change React Native runtime code. It added a launch QA checklist and revalidated the clean gates from Phase 13.

Validation:

```bash
cd /Users/nigel/dev/KIS
npm run typecheck
npx eslint . --quiet
npm run ci:launch
```

Measured status on 2026-04-30:

- `npm run typecheck` passed.
- `npx eslint . --quiet` passed.
- `npm run ci:launch` passed.
- `npm audit --omit=dev --legacy-peer-deps` passed through `ci:launch` with 0 vulnerabilities.
- `npm run typecheck:launch` passed through `ci:launch`.
- `npm run lint:launch` passed through `ci:launch`.

Runtime QA artifact:

- `docs/operations/REACT_NATIVE_LAUNCH_QA_CHECKLIST.md`

Focused Jest blocker:

- Running focused tests without extra flags was blocked by Watchman trying to write `/Users/nigel/Library/LaunchAgents/com.github.facebook.watchman.plist`.
- Running the same focused tests with `--no-watchman` reached Jest but failed before tests executed because React Native `jest/setup.js` is loaded as ESM without the required transform.

Phase 15 follow-up:

- Added `npm run test:phase5 -- <files>` for the focused no-Watchman regression path.
- Re-ran focused tests for broadcast feed video fallback, wallet modal gating, and profile controller phone-change/session flows.

## Phase 13 Strict Lint Closure

Phase 13 closed the full React Native strict lint baseline.

Validation:

```bash
cd /Users/nigel/dev/KIS
npm run typecheck
npx eslint . --quiet
npm run ci:launch
```

Measured status on 2026-04-30:

- `npm run typecheck` passed.
- `npx eslint . --quiet` passed.
- `npm run ci:launch` passed.
- `npm audit --omit=dev --legacy-peer-deps` passed through `ci:launch` with 0 vulnerabilities.
- `npm run typecheck:launch` passed through `ci:launch`.
- `npm run lint:launch` passed through `ci:launch`.

Lint groups repaired:

- `src/screens/tabs/profile-screen/EducationManagementModal.tsx`
  - stable hub-derived `institutions` and `quickStats`.
  - corrected detail render callback dependencies.
  - removed unused detail stack and preview material state exposure.
- Tests and support UI unused symbols:
  - broadcast feed discover test.
  - wallet modal test.
  - broadcast feed section helper.
  - shared text input helper style.
  - language switcher locals.
  - broadcast healthcare formatter.
  - health institution/session/catalog helpers.
  - profile controller KISC constant.

Current strict gate status:

- Full React Native TypeScript is clean.
- Full React Native strict lint is clean.
- Launch CI remains clean.
- Next work should shift from lint closure to focused runtime regression tests and smoke-test checklists for flows touched in Phases 11-13.

## Phase 12 Strict Lint Cleanup

Phase 12 reduced the full React Native strict lint baseline while keeping full TypeScript and launch CI green.

Validation:

```bash
cd /Users/nigel/dev/KIS
npm run typecheck
npm run ci:launch
npx eslint . --quiet
```

Measured status on 2026-04-30:

- `npm run typecheck` passed.
- `npm run ci:launch` passed.
- `npm audit --omit=dev --legacy-peer-deps` passed through `ci:launch` with 0 vulnerabilities.
- `npm run typecheck:launch` passed through `ci:launch`.
- `npm run lint:launch` passed through `ci:launch`.
- `npx eslint . --quiet` still fails with 23 errors.

High-risk hook groups repaired:

- `src/screens/market/ServiceBookingScreen.tsx`
- `src/screens/market/ServiceBookingDetailsPage.tsx`
- `src/screens/health/AvailabilityManagementScreen.tsx`
- `src/components/Bible/BiblePlansPanel.tsx`
- `src/components/Bible/BibleReaderPanel.tsx`
- `src/screens/broadcast/education/components/EducationDetailSheet.tsx`
- `src/screens/tabs/ProfileScreen.tsx`
- `src/screens/tabs/MesssagingSubTabs/UpdatesTab.tsx`
- `src/components/broadcast/BroadcastFeedVideoPreview.tsx`
- `src/screens/broadcast/market/ProductDetailsPage.tsx`
- `src/screens/broadcast/market/pages/MarketProductsPage.tsx`
- `src/screens/broadcast/market/pages/ShopProductsPage.tsx`

Remaining strict lint groups:

- `src/screens/tabs/profile-screen/EducationManagementModal.tsx`
  - stabilize `institutions` and `quickStats`.
  - review `palette`, `palette.primaryStrong`, and `getEducationRecordTitle` callback dependencies.
  - remove unused modal state only after verifying no UI flow depends on it.
- Unused-symbol cleanup:
  - broadcast feed tests.
  - wallet modal test imports.
  - broadcast feed section helper.
  - shared text input helper style.
  - language switcher locals.
  - broadcast healthcare `toKisc`.
  - healthcare unused helpers/state setters.
  - profile controller unused KISC constant.

Phase 13 should be able to make `npx eslint . --quiet` pass if the education-management modal hook review is handled carefully.

## Phase 11 Strict Typecheck Cleanup

Phase 11 converted the full project TypeScript gate from failing to passing.

Validation:

```bash
cd /Users/nigel/dev/KIS
npm run typecheck
npm run ci:launch
npx eslint . --quiet
```

Measured status on 2026-04-30:

- `npm run typecheck` passed.
- `npm run ci:launch` passed.
- `npm audit --omit=dev --legacy-peer-deps` passed through `ci:launch` with 0 vulnerabilities.
- `npm run typecheck:launch` passed through `ci:launch`.
- `npm run lint:launch` passed through `ci:launch`.
- `npx eslint . --quiet` still fails with 70 errors.

Typecheck groups repaired:

- Health service session missing appointment/service session symbols.
- Health institution card undefined session startup symbol.
- Market cart/order/dashboard upload and callback typing.
- Broadcast market response field drift and missing style keys.
- Broadcast feed saved/source typing.
- Education viewer-state/progress union cast.
- Broadcast tab `searchContext` prop mismatch.

High-signal lint groups repaired:

- `SocketProvider` call-control callbacks are now stable and included in the context memo dependencies.
- `ShopDashboardScreen` member permission callbacks and product/service broadcast callbacks now include required dependencies.
- Touched market files had unused imports/locals cleaned where safe.

Remaining strict lint groups:

- Service booking hook dependencies and scheduled date memoization.
- Health availability draft dependency review.
- Bible reader/plans loader callback dependencies.
- Education detail sheet complex dependency extraction.
- Profile broadcast CTA callback stability.
- Updates/status style arrays that need stable memoization.
- Unused symbols in tests and older UI modules.

Phase 12 should treat those remaining hook dependency errors as functional review items, not mechanical dependency insertion.

## Phase 10 Launch Gate

Phase 10 added a bounded launch gate so dependency and security-sensitive checks can pass while the full historical app baseline remains visible.

New commands:

```bash
cd /Users/nigel/dev/KIS
npm run ci:launch
npm run typecheck:launch
npm run lint:launch
```

What the launch gate does:

- Runs production dependency audit with the current React Native peer-dependency baseline.
- Typechecks a scoped set of stable security/storage/API service files through `tsconfig.launch.json`.
- Runs ESLint with true hook-order violations still treated as errors.
- Demotes existing unused-symbol and exhaustive-deps cleanup work for the launch gate only.

What remains strict:

- `npm run typecheck` is still the full project typecheck and still fails.
- `npm run lint:ci` is still the full strict lint gate and still fails.

Phase 10 code cleanup:

- Removed one true hook-order violation in `src/screens/broadcast/market/pages/ShopServicesPage.tsx` by removing an unnecessary `useMemo` below an early return.

Phase 10 measured status:

- `npm run ci:launch` passed with registry access.
- `npm run typecheck` still fails in the groups below.
- `npm run lint:ci` still fails with 111 errors and 4415 warnings.

### Education

Files:

- `src/screens/broadcast/education/EducationV2DiscoverPage.tsx`

Themes:

- `EducationContentItem` union mismatch.
- `viewerState` and `progress` fields do not exist on some union members.

Suggested fix:

- Add a normalized education display type or narrow by `type` before adding viewer fields.

### Broadcast Feeds

Files:

- `src/screens/broadcast/feeds/FeedsDiscoverPage.tsx`
- `src/screens/broadcast/feeds/hooks/useFeedsData.ts`

Themes:

- `viewer_saved` missing on `BroadcastFeedItem`.
- `source` possibly undefined.

Suggested fix:

- Align frontend feed item type with backend response.
- Guard or default `source` before dereference.

### Broadcast Market

Files:

- `src/screens/broadcast/pages/BroadcastMarketPage.tsx`

Themes:

- snake_case/camelCase mismatch for price fields.
- missing viewer role fields.
- missing style keys.
- missing `booking` field on `MarketBroadcastItem`.

Suggested fix:

- Add response normalization layer for market cards.
- Extend types only after confirming backend contract.
- Add missing styles rather than using undeclared keys.

### Health

Files:

- `src/screens/health/HealthInstitutionCardsScreen.tsx`
- `src/screens/health/HealthServiceSessionScreen.tsx`

Themes:

- Missing `start` symbol.
- Missing appointment booking helpers/state.
- Missing service-session starter.

Suggested fix:

- Restore/import missing helper functions.
- Split appointment state into a typed hook to reduce repeated undefined symbols.

### Market

Files:

- `src/screens/market/cart/CartDetailPage.tsx`
- `src/screens/market/cart/CartsListPage.tsx`
- `src/screens/market/orders/MarketplaceOrderDetailPage.tsx`
- `src/screens/market/ShopDashboardScreen.tsx`
- `src/screens/market/ShopEditorDrawer.tsx`

Themes:

- Implicit `any`.
- Missing style keys.
- React Native `FormData` typing issue.
- Variant union does not include `danger`.
- Used-before-declaration.

Suggested fix:

- Add explicit item types.
- Add missing styles.
- Use React Native upload type casting in one helper.
- Update UI variant union deliberately.
- Move callback declarations before use.

### Broadcast Tabs

Files:

- `src/screens/tabs/BroadcastScreen.tsx`

Themes:

- Prop mismatch: `searchContext` not accepted by target component.

Suggested fix:

- Add prop to target component or remove caller prop after confirming intended behavior.

## Triage Order

1. Health missing symbols, because they can break runtime flows.
2. Market upload/order typing, because it touches commerce operations.
3. Broadcast market/feed contract fields.
4. Education union typing.
5. Broadcast tab prop cleanup.

## Guardrails

- Do not disable strict typechecking globally.
- Do not convert broad files to `any`.
- Prefer typed normalizers at API boundaries.
- Keep domain fixes small and rerun `npm run typecheck` after each group.
