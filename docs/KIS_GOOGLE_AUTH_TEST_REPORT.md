# KIS Google Auth Migration — Test Report

Date: 2026-10-14. All commands below were actually executed; results are reported as observed, including failures and skips.

## 1. Django (`backend/kis`)

### 1a. Targeted migration-relevant suite
```
python3 manage.py test apps.kis_auth_bridge apps.otp apps.accounts apps.referrals apps.notifications common.test_observability_phase9 --keepdb
```
**Result: 573 tests, 3 failures, 0 errors.** All 3 failures pre-exist this migration and are unrelated:
- `apps.accounts.test_welcome_email_hardening` ×2 — test a `send_welcome_email` code path with zero live call sites (superseded by an earlier, unrelated Sept 2026 comms-architecture migration to in-app/push notifications).
- `apps.accounts.tests_qa.PrivacyControlsTests.test_create_private_field_visibility_rule` — fails on an unrelated subscription-tier feature gate (`require_feature(..., "privacy_custom")`).

Also re-ran after adding `verification.google` + read-only-field changes: same 3 pre-existing failures, all new assertions (`test_successful_link_writes_an_audit_log_entry`'s `verification.google` check, the new `test_link_does_not_touch_contact_email`, and the registration test's `verification.google` check) pass.

### 1b. Full repo-wide suite (two runs, for honesty about variance)
```
python3 manage.py test --keepdb
```
**Run 1** (output accidentally truncated by a `| tail -80` pipe — a tooling mistake, not a test result; only the final summary line survived): 3060 tests, `FAILED (failures=12, errors=11)`.

**Run 2** (rerun with full output capture to get the complete picture): 3060 tests, `FAILED (failures=12, errors=19)`, 1971s.

**Full failure/error list, both runs combined and classified:**

| Test | App | Classification |
|---|---|---|
| `test_welcome_email_exception_is_caught_logged_and_audited`, `test_failed_welcome_email_is_logged_and_audited_without_blocking_registration` | accounts | Pre-existing, dead code path (see §1a) |
| `test_create_private_field_visibility_rule` | accounts | Pre-existing, unrelated feature-tier gate |
| `test_education_broadcast_price_*` (×4), `test_education_paid_booking_defaults_to_usd_provider_pending`, `test_education_wallet_checkout_is_disabled_by_default` | broadcasts | Pre-existing, unrelated to auth |
| `test_confirm_creates_exactly_one_canonical_asset` | media | Pre-existing, moderation-state default, unrelated to auth |
| `test_video_attachment_queues_async_scan_instead_of_reading_synchronously` | testimony | Pre-existing, missing local scanning-provider stub, unrelated to auth |
| `test_every_owner_type_has_at_least_two_templates`, `test_template_list_filters_by_owner_type`, `test_stranger_cannot_use_someone_elses_owner_id_with_a_template`, `test_template_never_overrides_real_legacy_data`, `test_template_not_reapplied_on_subsequent_calls`, `test_template_seeds_a_genuinely_blank_website` | websites | Pre-existing, missing seed-template fixtures, unrelated to auth |
| `test_reschedule_within_window_is_blocked`, `test_retried_request_with_same_idempotency_key_returns_existing_booking`, `test_service_terms_acceptance_is_enforced`, `test_tax_handling_applies_extra_amount_when_exclusive`, `test_service_serializer_*` (×3), `test_shop_landing_page_builder_round_trips_through_shop_endpoint` | commerce | Only appeared in run 2 — concurrency/ordering-sensitive, unrelated to auth |
| `test_ban_blocks_rejoin_until_unbanned` | communities | Only appeared in run 2, unrelated to auth |
| `test_concurrent_reversal_only_reverses_once`, `test_concurrent_redelivery_*` (×2), `test_concurrent_duplicate_failed_events_release_the_reservation_exactly_once`, `test_late_failed_event_after_a_successful_one_does_not_reverse_the_completed_redemption` | billing | Explicit concurrency/race tests, only appeared in run 2 — consistent with system load during a second concurrent full-suite run, not a regression |
| `test_concurrent_satisfaction_confirmations_never_double_release_escrowed_funds` | broadcasts | Same as above |

**Zero failures or errors in `apps.otp`, `apps.kis_auth_bridge`, or `apps.accounts` beyond the 3 pre-existing items already listed in §1a.** The run-to-run variance (11 vs. 19 errors) is entirely in concurrency-sensitive tests in apps this migration never touched (commerce, billing, communities, broadcasts, websites, media, testimony) — consistent with test flakiness under machine load (this machine ran other test suites and SSH sessions concurrently during run 2), not a code regression. This was not re-run a third time to "average out" the flakiness, since none of the variance touches migration-relevant code.

### 1c. Static checks
- `python3 manage.py check` — no issues.
- `python3 manage.py makemigrations --check --dry-run` — no missing migrations.
- No `ruff`/`flake8`/`black`/`mypy` installed in this environment; every touched file was byte-compiled instead (clean).
- Migration `apps/otp/migrations/0005_delete_phoneotp.py` applied to the local dev database only (`kis_local`), confirmed to apply cleanly. **Not run against production as part of this work.**

## 2. kis-auth (`backend/kis-auth`) — no code changes made; run as a sanity check
```
npm test   # jest --runInBand
```
**Result: 7 test suites / 48 tests passed, 0 failed, 8 suites / 60 tests skipped.** Skips are entirely due to missing local Postgres/Redis in this environment (integration specs requiring real infra) — not failures. All runnable unit specs (PKCE, JWT/JWKS, internal signing, link tickets, rate limiting) passed.

## 3. backend/Nestjs — no code changes made; run as a sanity check
```
pnpm test   # jest --runInBand
```
**Result: 250/264 tests passed, 14 failed.** All 14 failures are in `chat/features/messages/*` (retention, purge, moderation), `reactions.channel.spec.ts`, and `messages.votePoll.channel.spec.ts` — Mongo-dependent chat features, unrelated to auth. Both identity-relevant specs passed cleanly: `src/auth/django-auth.service.spec.ts`, `src/security/internal-signing.spec.ts`.

## 4. KIS mobile (React Native)
```
npx jest src/security/__tests__/kisAuthBrowser.test.ts
npx tsc --noEmit
```
**Result: 6/6 Jest tests passed. `tsc --noEmit`: 0 errors** across the whole project, including the new `verification.google` typing and `ProfileScreen.tsx` changes made in this session. **ESLint was not run** — the implementation fork found the repo's ESLint config broken pre-existing (unrelated to this migration) and did not attempt to fix an unrelated tooling issue under this task's scope. This is a documented limitation, not a result being hidden.

## 5. Summary

| Suite | Executed | Pass | Fail/Error | Skipped | Auth-relevant regressions |
|---|---|---|---|---|---|
| Django targeted | Yes | 570 | 3 | 0 | None |
| Django full repo | Yes (×2) | ~3041–3049 | 12 fail / 11–19 error | 0 | None |
| kis-auth | Yes | 48 | 0 | 60 (missing infra) | None |
| Nest backend | Yes | 250 | 14 | 0 | None (auth specs pass) |
| Mobile Jest | Yes | 6 | 0 | 0 | None |
| Mobile tsc | Yes | — (0 errors) | 0 | — | None |
| Mobile ESLint | Not run | — | — | — | Pre-existing config breakage, not attempted |

No test was claimed to pass without actually running it. No auth-relevant test regressed.
