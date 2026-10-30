# KIS Referral Lifetime Cash-Commission System

Covers the flat 24%, lifetime, real-money referral commission system —
additive to, and independent of, the pre-existing one-time tiered
KIS-Coins referral reward (`apps.referrals.services` /
`docs/REWARDS_REFERRALS_DEPLOYMENT.md`). This document covers only the
delta: the cash-commission engine, payout-account connection, and the
mobile dashboard.

## 1. Why a second, separate system

`apps.referrals.services` already existed and is untouched by this
project: it pays a **one-time**, tier-rate (2–20% depending on the
referrer's own account tier) bonus in **KIS Coins** (an internal point,
`apps.rewards.RewardLedgerEntry`) the first time a referred user makes a
qualifying payment. It cannot be repurposed for this requirement because
the two systems differ on every axis that matters:

| | Existing (KIS Coins) | This project (cash) |
|---|---|---|
| Rate | 2–20%, by referrer's tier | Flat 24%, always |
| Frequency | Once, ever (`Referral.referred_user` is `OneToOneField`) | Every qualifying payment, forever |
| Currency | Internal points | Real money (USD, via Stripe/Flutterwave) |
| Payout | N/A (spent in-app) | External payout account required |

Both read the same permanent `Referral` row (created once, at
registration, by `apps.referrals.services.register_referral`) to find a
user's referrer — that relationship, and all of its existing
anti-self-referral / anti-duplicate-attribution guarantees, is fully
shared and was not modified.

## 2. Models (`apps/referrals/models.py`)

- **`ReferralPayoutAccount`** — one per user. Exactly one of
  `provider="stripe"` / `provider="flutterwave"` can be `ACTIVE` at a
  time (connecting one clears the other's fields). Never stores a raw
  bank account number — only the Flutterwave-issued beneficiary id, or
  the Stripe Connect Express account id.
- **`ReferralCommission`** — one row per qualifying payment (not per
  referred user). `source_payment_reference` is unique — the webhook-retry
  idempotency guard. Lifecycle: `pending → approved → paid`, with
  `reversed` and `failed_payout` as terminal/retryable side-states.

## 3. Lifecycle

```
Flutterwave tier_upgrade webhook (apps.billing.views)
  └─ award_referral_commission()           → PENDING  (flat 24% of gross)
       └─ [14-day settlement window, Celery beat, hourly]
            sweep_approvable_referral_commissions() → APPROVED
                 └─ [hourly]
                    sweep_payable_referral_commissions()
                        ├─ Stripe:      create_stripe_transfer()        → PAID (synchronous)
                        └─ Flutterwave: initiate_flutterwave_transfer() → stays APPROVED
                                            └─ transfer.completed webhook → PAID / FAILED_PAYOUT
```

A refund/chargeback on the underlying subscription
(`apps.billing.services.reverse_tier_upgrade_payment`) independently
reverses both the KIS-Coins reward (if any) and the matching
`ReferralCommission` (if any) — `REVERSED`, regardless of which status it
was in, including already-`PAID` (flagged in the audit log for manual
clawback rather than silently attempted automatically).

The settlement window (14 days) mirrors the existing KIS-Coins engine's
own window — a commission is not approved for payout while the
underlying payment could still be charged back by the provider itself.

## 4. API

All endpoints require authentication and act only on `request.user`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/referrals/commissions/` | Dashboard: rate, payout account, totals (pending/approved/paid/earned), history (last 50) |
| POST | `/api/v1/referrals/payout-account/flutterwave/` | Connect via Flutterwave Beneficiary (`account_bank`, `account_number`, optional `beneficiary_name`) |
| DELETE | `/api/v1/referrals/payout-account/flutterwave/` | Disconnect |
| GET | `/api/v1/referrals/payout-account/stripe/` | Re-query live Stripe Connect status and sync it locally |
| POST | `/api/v1/referrals/payout-account/stripe/` | Create (if needed) and return a fresh Express onboarding link |

The referral code/link itself is **not** repeated here — the mobile
client already has it from the existing `GET /api/v1/referrals/me/`.

## 5. Payment-provider integration

- **Flutterwave** — uses the **Beneficiaries API**
  (`apps/billing/referral_payouts.py`), not a subaccount (subaccounts are
  for splitting one transaction at charge time; a commission payout is an
  independent transfer against an already-settled balance). The raw bank
  account number is never persisted — only the provider-issued
  beneficiary id; it's re-fetched from Flutterwave itself at payout time.
- **Stripe** — standard **Stripe Connect Express** onboarding
  (`apps/billing/stripe_connect.py`, already existed, reused as-is) plus
  a platform-to-connected-account `Transfer` at payout time
  (`create_stripe_transfer`).
- **No new secrets.** Both providers reuse the existing
  `FLW_SECRET_KEY` / `FLW_WEBHOOK_SECRET` / `STRIPE_SECRET_KEY` /
  `STRIPE_WEBHOOK_SECRET` settings — nothing new to provision.
- **Compliance boundary respected**: KIS never holds referred-user funds
  for later distribution. A Stripe Transfer moves money KIS has already
  been paid (its own settled Connect platform balance) to the referrer's
  own Express account; a Flutterwave transfer pays out to a bank account
  the referrer themselves connected. Both are provider-native
  revenue-share mechanisms, not KIS operating a wallet.

## 6. Deferred referral attribution (mobile)

Two independent mechanisms feed the same `referral_code` field at
registration (`POST /api/v1/auth/register/`) — see
`apps/accounts/views.py`. Both are best-effort; neither is required for
registration to succeed, and an unrecognized code is simply a no-op in
`register_referral`.

- **Android — Google Play Install Referrer** (new, this project):
  `android/app/src/main/java/com/kingdom/impact/InstallReferrerModule.kt`
  + `src/utils/installReferrer.ts` (KIS mobile repo). Authoritative — the
  referrer string comes from Google Play's own install record, not
  anything editable on-device. Requires the Play Store listing URL to
  carry `&referrer=ref_code%3D<CODE>`, which the website's
  `OpenInApp.tsx` component now appends automatically whenever it has an
  attribution code (website repo, `components/website-builder/OpenInApp.tsx`).
- **iOS and Android fallback — clipboard** (pre-existing):
  `src/utils/referralAttribution.ts`. The website writes the code to the
  clipboard immediately before redirecting to either store; the app reads
  it back once, at first registration screen mount.

On Android, Install Referrer is tried first; the clipboard value is only
used if Install Referrer returns nothing (older device, no Play Store,
feature unsupported, or this install didn't come from a referral link).

### iOS has no reliable deferred-attribution mechanism — be explicit about this

**The clipboard mechanism is not equivalent to reliable deferred deep
linking, and must not be described as if it were.** It is the best
available option on iOS without a paid third-party attribution SDK
(Branch, AppsFlyer, Adjust — none of which are integrated, and adding one
is a product/cost decision this project did not make), but it is a
heuristic with real, known failure modes:

- The App Store install flow can genuinely drop the clipboard write
  entirely (iOS clears the clipboard, the user switches apps before the
  write lands, Safari/in-app-browser clipboard permission is denied).
- There is no cryptographic or server-verified link between "this
  clipboard string" and "this specific click" — a user who copies
  *anything* else matching the 6–10 char uppercase pattern between
  leaving the website and opening KIS for the first time gets
  misattributed (low-probability given the pattern, but non-zero, and
  unlike Android's Install Referrer, entirely outside Google/Apple's own
  verified install record).
- It only works for a direct website → store → install path on the same
  device. A link forwarded by text/chat and opened on a different device
  than the one that visits the store, or an install from a browser whose
  clipboard write silently fails, loses attribution entirely — and
  because it is a silent, best-effort mechanism by design, that loss is
  invisible (the registration simply proceeds with no referral_code,
  exactly like an organic, non-referred install).

Treat iOS attribution as **"reasonably effective, not reliable"** in any
product or analytics conversation. Android's Install Referrer, once the
Play Store listing is live, has a materially stronger reliability
guarantee than iOS has today.

## 7. What has NOT been verified (be honest about this)

- **No live provider smoke test.** `create_flutterwave_beneficiary`,
  `initiate_flutterwave_transfer`, and `create_stripe_transfer` are
  covered by mocked-response unit tests only (matching this codebase's
  existing convention for provider integrations). Verify against each
  provider's real sandbox before the first real payout run.
- **Play Install Referrer cannot be end-to-end tested yet** — KIS is not
  yet live on the Play Store (`kis.availability.android` is still off in
  the website's product config), so there is no real store listing for
  Google Play to attach a referrer string to. The native module compiles
  and runs correctly (`InstallReferrerClient` returns `null` safely on an
  emulator/no-Play-Store device, verified) — the end-to-end path can only
  be verified for real after the Play Store listing goes live.
- **No physical-device test matrix was run** for either attribution path
  — this is JS/native-code-level and backend-level testing only.
- **No automatic retry for `FAILED_PAYOUT`.** Once a payout attempt fails
  (insufficient platform balance, provider error, invalid beneficiary),
  the commission sits in `FAILED_PAYOUT` until an admin reverses it or a
  future payout-account reconnect triggers a fresh approve→pay cycle via
  a new commission — there is no scheduled "retry failed payouts N times"
  sweep. This matches the codebase's existing conservative "never
  silently retry a financial operation" doctrine, but means failed
  payouts need human attention, not just time, to resolve.

## 8. Production-readiness audit — findings and fixes (this pass)

A follow-up audit (after the initial implementation report) found and
fixed three genuine defects, verified below with real evidence rather
than re-asserted:

1. **Duplicate Flutterwave payout risk (fixed).** `initiate_commission_payout`
   had no guard against being called again on a commission whose
   Flutterwave transfer was already in flight (status still `APPROVED`,
   awaiting the `transfer.completed` webhook). Since the hourly sweep
   re-selects every `APPROVED` commission, a webhook that took longer
   than an hour to arrive — or never arrived — would cause a second real
   transfer with a fresh reference. Fixed by skipping re-attempt whenever
   `payout_reference` is already set. Regression test:
   `test_flutterwave_payout_not_reattempted_while_already_in_flight`
   (apps/referrals/test_commissions.py) — asserts the mocked transfer
   function is called exactly once across two `initiate_commission_payout`
   calls on the same commission.
2. **`ReferralPayoutAccount` never synced by the Stripe `account.updated`
   webhook (fixed).** `_sync_stripe_connect_account_status`
   (apps/billing/views.py) only checked four pre-existing payout-holder
   models; `ReferralPayoutAccount` was missing entirely, so a referrer's
   account only ever activated if they manually reopened the Referrals
   screen (which re-queries Stripe directly). Fixed by adding it to the
   lookup, plus deriving its cached `status` enum the same way the manual
   GET endpoint already does (the other four models don't use that enum,
   so the generic sync logic didn't do this automatically). Regression
   tests: `test_syncs_matching_referral_payout_account_and_derives_status`,
   `test_referral_payout_account_stays_pending_when_payouts_not_yet_enabled`
   (apps/billing/test_marketplace_payments.py).
3. **Android Install Referrer crash risk (fixed, evidence-based).**
   The native module originally called the blocking
   `client.installReferrer` IPC synchronously inside
   `onInstallReferrerSetupFinished`. Web research surfaced a real,
   previously-shipped production crash in this exact pattern —
   [bluesky-social/social-app PR #12037](https://github.com/bluesky-social/social-app/pull/12037)
   — where a `DeadObjectException`/`SecurityException` from the Play
   Store process dying mid-call could race against the same listener's
   own disconnect-cleanup path. Fixed by adopting the same shape as that
   PR's fix: the blocking call now runs on a background dispatcher inside
   `runCatching`, and promise resolution + `endConnection()` are both
   funneled through a single coroutine scope so a disconnect firing
   mid-call can never race the resolution path. Re-verified with a real
   online Gradle Kotlin compile (`BUILD SUCCESSFUL`) after the change.

Two items were investigated and found to be **pre-existing platform
limitations, not defects in this project**: there is no partial-refund
concept anywhere in this billing system (`reverse_tier_upgrade_payment`
and the Flutterwave self-service refund endpoint are both full-amount-only
by design) — the commission reversal path correctly mirrors that same
all-or-nothing behavior rather than inventing partial-refund support the
rest of the platform doesn't have; and referral ownership permanence was
confirmed structurally sound — `register_referral` is called from exactly
two places in the codebase, both immediately after creating a brand-new
`User` row in the same request, so there is no live code path where an
*existing* account could acquire or change a referrer (the model's
`OneToOneField` on `referred_user` is enforced defense-in-depth, not the
only thing preventing it).

## 9. Payout-lifecycle hardening (follow-up audit)

A focused second pass specifically on payout reliability/recovery found
and fixed a severe gap: **a local DB transaction cannot make an external
payment request atomic.** The original code only persisted
`payout_attempts`/`payout_reference` *after* the provider call returned —
a crash or lost response between "Flutterwave accepted the transfer" and
"we saved the result" left no durable record of which reference may be
in flight, so the next sweep would mint a fresh reference and send a
second real transfer.

Fixed with a durable `payout_attempt_reference` field
(migration `0006`), persisted in its own short transaction *before* any
provider is ever called, plus:
- **Flutterwave**: reconciliation-by-lookup (`GET /v3/transfers?reference=`)
  before every attempt, an `X-Idempotency-Key` header as a provider-side
  backstop for a true concurrent race, and webhook/reconciliation
  correlation by *our* reference (not just Flutterwave's returned id,
  which may never have been captured).
- **Stripe**: already safe via its own documented `idempotency_key`
  contract — a retry with the same reference returns the original
  Transfer rather than creating a second one. A `transfer_group`-based
  lookup was added for admin-recovery parity, not because the automatic
  path needed it.
- **Admin recovery**: a new `reconcile_payout_selected` admin action
  (and `reconcile_commission_payout` service function) that queries the
  real provider and applies ground truth — including promoting an
  already-`FAILED_PAYOUT` commission to `PAID` if the provider says it
  actually succeeded. `PAID` itself is treated as terminal everywhere —
  no later event, successful or not, can change it.

Also fixed in the same pass: `_finalize_payout_failed` wasn't persisting
which provider had been attempted, which would have made a failed
Flutterwave attempt permanently unrecoverable by the webhook/admin paths
above — caught by a test that exercises the real failure path rather
than hand-constructing `FAILED_PAYOUT` state.

Fourteen new tests cover: crash-before-persist recovery (reused vs.
provider-confirmed-successful vs. provider-confirmed-failed), duplicate
and out-of-order webhook delivery, promotion from `FAILED_PAYOUT` to
`PAID`, admin-triggered reconciliation, concurrent settlement workers
(proves both racing calls use the identical reference/idempotency key —
deduplicating a true millisecond-level race between two simultaneous
provider requests is the provider's own job at that point, not something
a mock can prove), and a payout-account disconnect/provider-switch while
a transfer is still in flight.

## 10. Deployment checklist

1. Run `python manage.py migrate referrals` (migrations `0005`-`0006`,
   additive — two new tables plus one new field, no change to any
   existing table).
2. No new environment variables.
3. Celery Beat picks up `approve-referral-commissions` and
   `pay-out-referral-commissions` (hourly) automatically from
   `CELERY_BEAT_SCHEDULE` — no separate registration step.
4. Mobile: requires a native rebuild (new Android Gradle dependency +
   native module) before the Install Referrer path is live; the
   clipboard fallback and the rest of the feature work with a JS-only
   OTA update.
5. Website: the `OpenInApp.tsx` change is a plain Next.js deploy, no
   migration or config change.
