# KIS Billing + Rewards + Referrals + KIS Coins + Scheduling — Deployment Guide

Covers everything built across the 14-phase billing/rewards/referrals/scheduling
project: `apps.rewards`, `apps.referrals`, the redemption-related changes to
`apps.billing`, the Celery/Beat scheduler wiring, and the related mobile UX.

This document is the Phase 14 (final phase) deliverable. It does not change
any code — it documents what exists, what an operator needs to do before and
after deploying it, and what business decisions are still open.

---

## 1. Pre-deploy checklist

### 1.1 Environment variables / secrets

Nothing new and KIS-Coins/referral-specific is required beyond what the
existing infrastructure already needs:

- `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` — already required for the
  three pre-existing scheduled jobs (media upload expiry ×2, subscription
  expiry); the three new jobs added in this project reuse the same broker
  and result backend, no new variables.
- No new third-party API keys, no new payment-provider credentials, no new
  feature flags were introduced by this project. `KIS_LEGACY_WALLET_UPGRADE_ENABLED`
  and `KIS_LEGACY_PROMO_CASH_BONUS_ENABLED` already existed and already
  default to off — this project did not add or change either.

### 1.2 Migration order

Run migrations before deploying the new code, as normal. Two migrations in
this project are **data migrations**, not just schema changes — both are
idempotent and safe to re-run if a deploy is retried:

- `apps/rewards/migrations/0002_backfill_loyalty_points.py` — backfills
  legacy `apps.commerce.LoyaltyPoint` rows into the new `RewardLedgerEntry`
  ledger. Read-only with respect to the legacy table; skips rows already
  backfilled (via `idempotency_key`), so re-running is safe.
- `apps/referrals/migrations/0004_referral_qualified_at.py` — backfills the
  new `qualified_at` field for any pre-existing QUALIFIED/REWARDED
  `Referral` rows, from `updated_at` as a best-effort proxy. Also safe to
  re-run (only touches rows where `qualified_at` is still NULL).

Both print a one-line summary to stdout during `migrate` (`created N,
skipped M` / `backfilled N`) — check deploy logs for these lines to confirm
they ran as expected, not silently as a no-op when rows were actually
expected.

### 1.3 The Celery worker/beat/redis production services — STILL PENDING

**This is the single most important item in this checklist.** `render.yaml`
at the repo root fully defines three additional services — `kis-celery-worker`,
`kis-celery-beat`, and a managed `kis-redis` instance — as a complete,
correct Render Blueprint. The file has carried this note since Phase 10:

> "applying it (via `render blueprint launch` or connecting this repo as a
> Blueprint in the Render dashboard) is what actually provisions and starts
> billing for these services, and that step is intentionally left for a
> human to trigger explicitly."

**As of the end of Phase 13, this has never been confirmed done.** Every
local check of `verify_celery_launch` throughout Phases 10–13 has reported
`worker_ping: no worker responded — is one running?`, which only proves no
worker is reachable from the local dev machine — it says nothing about
production. **Before or immediately after this deploy, confirm directly in
the Render dashboard whether `kis-celery-worker` and `kis-celery-beat` are
provisioned and running.** If they are not, every scheduled job described in
this document (reward expiration, referral settlement, reconciliation, and
the three pre-existing jobs) will be correctly configured but will never
actually execute in production — `calculate_redemption`/`apply_rewards`/
manual redemption flows are unaffected (they're request-time, not
scheduled), but nothing will expire coins, settle referrals, or reconcile
without a live worker + beat process.

---

## 2. Post-deploy verification

Run these against the production environment (via a one-off shell/task on
the deployed `kis-django-backend` service, not locally):

```bash
python manage.py verify_celery_launch --json --strict
```

Expect `"ready": true` and all of the following checks to show `"pass"`:

- `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` — present.
- `CELERY_BEAT_SCHEDULER` — `django_celery_beat.schedulers:DatabaseScheduler`.
- `CELERY_BEAT_SCHEDULE` — `"6 periodic task(s) configured"`.
- All six `beat_schedule:*` entries:
  - `expire-abandoned-media-uploads`
  - `expire-unattached-media-uploads`
  - `expire-subscriptions`
  - `expire-reward-ledger-entries`
  - `confirm-settled-referrals`
  - `reconcile-rewards-and-referrals`
- `broker_connectivity` — `connected`.
- `worker_ping` — **this is the one that matters most**: it must say `N
  worker(s) responded`, not the warning seen in every local check
  throughout this project. This is the actual proof a live worker process
  exists in production, not just that it's configured to exist.

If `worker_ping` still warns after deploy, that confirms the Render
Blueprint services from §1.3 have not been launched — stop and resolve that
before considering this feature live, since no scheduled job will run
otherwise.

**Spot checks** (optional, but recommended once a worker is confirmed live):

- One real achievement grant: trigger any action wired to
  `apps.rewards.services.grant_achievement` (or run it directly via shell
  for a test user) and confirm `GET /api/v1/rewards/balance/` reflects it.
- `GET /api/v1/referrals/me/` for a real authenticated user returns a
  `code` and the current `current_referral_rate_percent`.
- `python manage.py reconcile_rewards --limit 100` runs clean (0 anomalies
  expected on a fresh dataset).

---

## 3. Open business decisions — for the user/product owner, not resolved by this project

None of these block a technical deploy — the code behaves safely and
correctly with the current defaults in every case — but each represents a
real product/business decision that was deliberately not made unilaterally
across all 14 phases. Flagging as an explicit checklist rather than letting
them stay buried in phase-report prose:

| # | Decision | Current default | Where it lives |
|---|---|---|---|
| 1 | What is one KIS Coin actually worth when applied as a subscription discount? | `RedemptionPolicy.coin_value_cents = 1.0000` (1 coin = 1 cent) — an explicit placeholder since Phase 1, never a real business number | `apps/rewards/models.py`, editable live via `/admin` (Phase 12) |
| 2 | ~~Should the legacy flat-200-point referral path be retired?~~ **RESOLVED** — retired in the pre-deployment hardening pass. `apply_referral_reward_if_pending` is now a permanent no-op; the two call sites that used to invoke it at account activation were removed. `qualify_referral`/`confirm_referral_reward` (the tier-aware, payment-gated engine) is now the single authoritative referral reward path. Historical `Referral`/`LoyaltyPoint` rows from the old path are untouched. | n/a — resolved | `apps/referrals/services.py` |
| 3 | Is 14 days the right referral settlement window? | `REFERRAL_SETTLEMENT_WINDOW_DAYS = 14` — proposed by Phase 11, re-confirmed as the recommended default in the hardening pass, still a plain code constant rather than a DB-editable setting (deliberately not converted — see the hardening pass's own report for why) | `apps/referrals/services.py`, one constant, no other code impact if changed |
| 4 | Which reward types (if any) should actually expire? | Every grant function (`grant_achievement`, `grant_repeatable`, `grant_promo_bonus`, `create_pending_entry`) accepts an `expires_at`, but nothing currently passes a real value — the Phase 11 expiration sweep is fully built and scheduled but will find zero real candidates until this is decided. The hardening pass added expiration-date visibility to `LoyaltyScreen.tsx`'s history so this is ready to display the moment a real expiry is ever set. | `apps/rewards/services.py` |
| 5 | Redis vs. Postgres for Celery task results? | `CELERY_RESULT_BACKEND` is Redis — fast, simple, but `django_celery_results`' `TaskResult`/`GroupResult` admin panels (auto-registered, reachable) are permanently empty as a result | `config/settings/base.py` |

---

## 4. Final go/no-go summary

This directly answers the question that started this entire project, before
Phase 0: **is the billing/rewards/referrals section ready for production
deployment?**

**Yes, from a code-correctness and safety standpoint**, with one
operational item outstanding:

- All financial logic (redemption ceilings, referral qualification/
  settlement, reversal/expiration) is server-authoritative, tested
  (including real-concurrency `TransactionTestCase` coverage for every
  race-sensitive path), and has been re-verified clean across 13 phases —
  713 tests, 708 passing, with the 5 failing tests being pre-existing,
  unrelated to this project, and already documented since Phase 8.
- Legal/compliance-reviewed disclaimer language is used consistently
  everywhere KIS Coins are described to a user (mobile UI and the
  Phase 9 education screen), matching the exact wording specified at the
  start of this project.
- No unsupported financial functionality (deposits, P2P transfers, currency
  conversion) was found active — Phase 7 confirmed and removed the mobile
  UI panels for these; the corresponding backend flows remain gated behind
  `KIS_LEGACY_*_ENABLED` flags that default to off.
- Admin surfaces correctly distinguish editable configuration
  (`ReferralRateConfig`, `RedemptionPolicy` with save-time validation,
  `AchievementDefinition`, `RepeatableRewardRule`) from read-only financial
  records (`RewardLedgerEntry`, `Referral`).

**What actually blocks real-world effect, not code readiness**: §1.3 above
— confirm the Render Blueprint's worker/beat/redis services are actually
running in production. Everything this project built that depends on a
scheduled job (reward expiration, referral settlement, reconciliation) is
inert without it. This is a five-minute operational check/action, not
further development.

**What's deliberately left as an open product decision, not a blocker**:
the five items in §3 — none of them represent unsafe or incorrect behavior
at their current defaults, they represent real business choices this
project's scope was never meant to make unilaterally.

---

*This document was produced as the Phase 14 deliverable of the KIS Billing
+ Rewards + Referrals + KIS Coins + Scheduling project (Phases 0–14). No
source code, tests, or configuration were changed to produce it.*
