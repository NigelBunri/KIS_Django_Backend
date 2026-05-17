# Phase 08 - Bible, Spiritual Growth, And KCAN Vision Launch Proof

Date: 2026-05-17

## Scope

This phase verifies the launch-safe Bible, spiritual growth, and KCAN vision foundation without changing normal user-facing behavior. It focuses on Bible reader UX contracts, reading plans/reminders, highlights, notes, comments, daily meditations, offline/low-bandwidth readiness, KCAN/partner ministry publishing, Our Vision page evidence, child/family-safe spiritual content controls, notification badge read-state, and moderation/media safety for devotional content.

## Implementation Completed

- Added a read-only, non-secret launch verifier:
  - `python3 manage.py verify_bible_launch`
  - `python3 manage.py verify_bible_launch --strict`
- Verified URL contracts for:
  - translations/books/chapters/reader/parallel reader/search/daily/stats/spiritual growth;
  - translation registry;
  - daily passages, meditations, prayer months/days, and KCAN content audit;
  - reading plans, enrollments, reading history, reading events, from-selection planner creation;
  - bookmarks, notes, highlights, memory verses, preferences, and current preferences;
  - Bible courses, lessons, enrollments, progress, comments, reactions, shares, live-session placeholders, recordings, and credentials.
- Tightened Bible reminder notifications so reminder metadata includes exact Bible source and target metadata in `context_data`:
  - `source=bible`;
  - `badge_source=bible`;
  - `target_type=bible_reading_event`;
  - `target_id=<event id>` in context.
- Added attachment redaction for Bible lesson and assignment submission API output:
  - strips raw paths, storage keys, private URLs, signed URLs, tokens, and similar private attachment fields;
  - preserves public URLs and safe metadata.
- Added focused tests for:
  - launch verifier safe defaults;
  - private attachment field redaction;
  - exact Bible reminder badge metadata.
- Confirmed existing tests cover:
  - public translation registry excludes unlicensed/restricted translations;
  - restricted translations cannot be read directly;
  - reader supports verse ranges;
  - planner events can be created from a selected verse;
  - spiritual growth summary exposes counts, offline readiness, family-safe state, and media safety state.

## Safety Decisions

- Bible translation publication remains limited to public, licensed, valid/warning translations.
- Live AI provider calls remain disabled by default for Bible/spiritual assistance.
- Live media-safety provider calls remain disabled by default; current launch mode depends on local validation plus quarantine/review states.
- Public indexing remains disabled until SEO/privacy/abuse evidence is approved.
- KCAN/Bible public publishing and indexing remain launch-gated until staging evidence is attached.
- Certificate/share URLs must not expose bearer tokens in app flows; the backend verifier does not print token values.

## Validation

Passed:

- `python3 -m py_compile apps/bible/management/commands/verify_bible_launch.py apps/bible/management/commands/dispatch_bible_reading_reminders.py apps/bible/serializers.py apps/bible/tests.py apps/bible/views.py`
- `python3 manage.py check`
- `python3 manage.py makemigrations --check --dry-run`
- `python3 manage.py verify_bible_launch --include-counts`
  - 8 pass / 0 fail / 1 warning.
- `python3 manage.py test apps.bible.tests.BibleTranslationRegistryTests --noinput --keepdb`
  - PostgreSQL-backed: 10 tests passed.
- React Native `npm run typecheck -- --pretty false`
- React Native `npx eslint src/screens/tabs/BibleScreen.tsx src/screens/tabs/bible/useBibleData.ts src/components/Bible src/services/bibleOfflineCache.ts src/services/biblePreferenceStore.ts src/services/bibleUserPersistence.ts src/components/broadcast/KcanVisionModal.tsx --quiet`
- Nest `pnpm tsc --noEmit --pretty false --incremental false`

Warnings / blockers:

- `verify_bible_launch --include-counts` could not read Bible/KCAN database counts locally due `OperationalError`.
- The first reminder test run hit local Redis/Celery result-store retries at `10.114.180.99:6379`; the test was adjusted to mock the delivery enqueue and then passed. Staging still needs real Celery/Redis proof.

## Remaining Launch Evidence Needed

| Priority | Evidence gap |
|---|---|
| P0 | Staging `python3 manage.py verify_bible_launch --strict --include-counts` with database access. |
| P0 | Real-device Bible reader QA: tabs, sticky tab behavior, verse navigation, highlight/comment filter navigation, notes, bookmarks, memory verses, and dark/light contrast. |
| P0 | Real-device daily meditation, prayer calendar, reading plan reminder, badge decrement, and offline/low-bandwidth QA. |
| P0 | KCAN Our Vision page QA, including image fullscreen/zoom behavior and close affordance on small devices. |
| P0 | Staging proof that Bible/KCAN reminder notifications create exact source/target metadata and realtime badge refresh works through Django/Nest/React Native. |
| P0 | Staging proof that devotional/course media attachments route through media safety and do not expose private storage paths. |
| P1 | Final licensing/legal review for all imported translations and any audio/devotional content. |
| P1 | Product/pastoral review for any future AI Bible assistance before live provider calls are enabled. |

## Phase 09 Prompt

```text
Please implement Phase 09 of the KIS 100% Implementation and 80%+ Global Parity roadmap without using git commands. Focus on Commerce, Market, Shops, And Service Booking Launch Proof. Use Phase 00-08 evidence to verify marketplace discovery, shop/product/service management, buyer-facing product/service detail, cart/order/service-booking reliability, seller trust badges, USD-only direct-payment readiness, fulfillment/completion/complaint windows, reviews/questions safety, media safety for product/service images, notification badge read-state, and rollback/audit evidence. Prefer PostgreSQL-backed Django tests; if Postgres, Redis, Flutterwave sandbox, or environment setup blocks validation, record the exact blocker and move on. Preserve existing UI behavior, do not enable live charges or legacy wallet/KIS-credit-as-money flows, do not expose secrets/private media paths/payment data, run safe Django/Nest/React Native validation, update docs/implementation-parity-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 10.
```
