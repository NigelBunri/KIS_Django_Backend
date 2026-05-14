# React Native Launch QA Checklist

Use this checklist after the Phase 11-13 type/lint hardening and before production release. Do not use real production credentials in local QA. Record device, build, backend environment, tester, and date for each run.

## Evidence Header

Complete this in the production release ticket, not with secret values:

- Tester: `TODO_TESTER`
- Date: `TODO_DATE`
- App build/version: `TODO_BUILD`
- Platform/device/OS: `TODO_PLATFORM_DEVICE_OS`
- Backend environment: `TODO_BACKEND_ENVIRONMENT`
- API base URL label, not tokenized URL: `TODO_API_LABEL`
- Evidence ticket/link: `TODO_EVIDENCE_TICKET`

## Required Gates

Run from `/Users/nigel/dev/KIS`:

```bash
npm run typecheck
npx eslint . --quiet
npm run ci:launch
```

Expected result:

- TypeScript passes.
- Strict lint passes.
- Production dependency audit reports 0 React Native production vulnerabilities.
- Launch typecheck and launch lint pass.

## Focused Regression Tests

Run from `/Users/nigel/dev/KIS`:

```bash
npm run test:phase5 -- __tests__/broadcast-feeds.video-playback.test.tsx __tests__/phase5.wallet-modal.test.tsx __tests__/phase5.profile-controller.test.tsx
```

Expected result:

- 3 focused suites pass.
- 10 focused tests pass.
- No Watchman service is required.

Coverage:

- Broadcast feed video fallback.
- Wallet modal transfer gating before recipient verification.
- Profile controller phone-change/session behavior and wallet verification payload behavior.

## Health Service Sessions And Appointments

Smoke path:

- Open a health institution service from broadcast or health cards.
- Start a service session.
- Confirm appointment booking context appears when a booking id is present.
- Pull to reload or reopen the appointment/session screen.
- Verify appointment status, date/time, and patient/user rows render without crashes.
- Open calendar/ICS action when available.
- Cancel and reschedule only in a non-production test environment.

Pass criteria:

- No "undefined is not a function" or missing helper crashes.
- Booking state reloads after cancel/reschedule.
- Unauthorized or non-owner booking data is not shown.
- Failed booking calls show user-safe errors without token or OTP leakage.

## Service Booking, Reschedule, And Cancel

Smoke path:

- Open a market service.
- Select a date/time slot.
- Add package, add-ons, participant count, staff count, remote/on-site options, address fields, requirements, terms acceptance, and optional requested price.
- Continue to review and confirm booking.
- Open booking details.
- Verify cancellation and reschedule disabled states respect time windows and status.
- Reschedule to a new slot in a non-production test environment.

Pass criteria:

- Submitted payload reflects current visible form state.
- Requirement and terms validation cannot be bypassed.
- Scheduled date calculations do not flicker or disable actions incorrectly.
- Booking refresh events update downstream screens.

## Bible Reader And Plans

Smoke path:

- Open Bible reader.
- Switch language and translation.
- Swipe between previous/next chapter.
- Open filters and close by gesture.
- Load highlights, notes, bookmarks, and highlight colors.
- Open Bible plans and change date range.
- Create/update/delete one local-safe reading plan event.

Pass criteria:

- Loader effects do not loop.
- Translation/language state stays in sync.
- Offline/local fallback data merges without duplicate rows.
- Swipe navigation loads the intended chapter.

## Education Management And Detail

Smoke path:

- Open education management modal.
- Confirm institution list and quick stats render.
- Open institution dashboard.
- Open detail views for courses, modules, lessons, materials, assessments, events, broadcasts, enrollments, bookings, and staff where test data exists.
- Create/edit a course module and module item in a non-production test environment.
- Open learner-facing education detail and verify viewer enrollment/booking state updates after enrollment/booking action.

Pass criteria:

- No stale quick stats or institution rows after refresh.
- Detail collections render titles/summaries consistently.
- Modal navigation does not lose the active institution.
- Education actions do not expose bearer tokens in URLs or logs.

## Broadcast Feed Video Fallback

Smoke path:

- Open a feed item with both stream and file video sources.
- Confirm safer file source is preferred when loopback/local stream source is risky.
- Force or simulate stream failure in a test build and verify fallback source is used.
- Force all sources to fail and verify a clear failure state.

Pass criteria:

- Fallback text appears only after fallback happens.
- Final failure state lists tried source types.
- Console logs do not contain credentials or signed private URLs.

## Market Product, Cart, And Orders

Smoke path:

- Open market product details.
- Select variants, custom attributes, quantity, and custom description.
- Add to cart.
- Open cart detail and modify/remove items.
- Place an order in a non-production test environment.
- Open buyer and provider order pages.
- Upload complaint attachment in a non-production test environment.

Pass criteria:

- Add-to-cart payload reflects selected variant and attributes.
- Cart/order pages do not crash on empty or awaiting-satisfaction states.
- Attachment upload uses React Native `FormData` safely.
- Order totals and statuses render consistently.

## Wallet Modal

Smoke path:

- Open wallet transfer modal.
- Enter amount and recipient.
- Verify submit is disabled before recipient verification.
- Verify submit enables after verified receiver data is returned.
- Submit only in a non-production test environment.

Pass criteria:

- Receiver name and phone are shown before submit.
- Transfer cannot proceed with unverified recipient.
- Errors do not leak account identifiers beyond expected user-facing text.

## Language Switcher

Smoke path:

- Open language switcher if enabled in the build.
- Switch to each supported language.
- Close and reopen the modal.

Pass criteria:

- Selected language persists according to app settings.
- Modal closes cleanly after selection.
- Hidden/disabled launcher code does not create layout gaps.

## Profile Controller

Smoke path:

- Load profile.
- Save a non-phone profile change.
- Save a phone-number change in a non-production test environment.
- Continue through forced re-login prompt.
- Verify wallet recipient verification flow still blocks transfer until verified.

Pass criteria:

- Non-phone profile save does not sign the user out.
- Phone change clears local auth and forces sign-in again.
- Cached profile refresh does not overwrite freshly saved state unexpectedly.

## Verification Center And Badges

Smoke path:

- Open profile dashboard and tap the verification badge/status card.
- Submit only private media reference metadata in a non-production environment.
- Open market/shop management and verify shop verification action opens.
- Open health institution management and verify health verification action opens.
- Open education institution workspace overview and verify education verification action opens.
- Open partner workspace and verify partner verification action opens.
- Refresh after a staff-issued badge and confirm the public badge appears.
- Refresh after badge revocation and confirm the public badge disappears.

Pass criteria:

- Verification sheet never asks for raw document contents or base64.
- Provider handoff text stays placeholder-only unless explicitly enabled.
- No provider secrets or bearer tokens appear in UI/logs.
- Badge rows render consistently in light and dark themes.
- Revoked/expired badges do not display publicly after refresh.

## Evidence To Save

- Command outputs for required gates.
- Command output for focused regression tests.
- Device and OS versions.
- Backend/API environment name.
- Screenshots for successful health booking, service booking review, education detail, market cart, wallet verification, and video fallback.
- Screenshots for verification badge display on profile, shop, partner, health institution, and education institution.
- Notes for any blocked path, including missing test data or provider dependency.

## Phase 16 Current Evidence Status

Recorded on 2026-04-30:

- Local React Native gates passed:
  - `npm run typecheck`
  - `npx eslint . --quiet`
  - `npm run ci:launch`
- Focused no-Watchman regression tests passed:
  - `npm run test:phase5 -- __tests__/broadcast-feeds.video-playback.test.tsx __tests__/phase5.wallet-modal.test.tsx __tests__/phase5.profile-controller.test.tsx`
  - 3 suites passed.
  - 10 tests passed.
- Runtime QA still needs simulator/device execution with non-production data.
- Attach real runtime evidence to the release ticket instead of storing screenshots
  or environment-specific URLs in this repository.
