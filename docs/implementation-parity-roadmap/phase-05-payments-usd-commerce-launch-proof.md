# Phase 05 - Payments And USD-Only Commerce Launch Proof

Date: 2026-05-17

## Scope

This phase verified and tightened the launch path for USD-only direct-provider payments across commerce, service bookings, education bookings, health billing, upgrade/subscription placeholders, receipts, and historical wallet/KIS promotional-credit displays.

The launch policy remains:

- Paid commerce, education, health, and upgrade flows are USD-first.
- Flutterwave/direct-provider payment intents are the approved path for new paid workflows.
- KIS promotional credits are reward/subsidy credits only.
- Promotional credits are not cash, not transferable, not withdrawable, and not exchange-rated.
- Legacy wallet deposit, transfer, checkout, upgrade, and conversion flows stay disabled by default.
- Live charges and provider payment-link generation remain disabled unless staging/production evidence explicitly approves them.

## Changes Made

- Added a read-only payment launch verifier:
  - `apps/billing/management/commands/verify_payment_launch.py`
- The verifier checks, without printing secrets or making provider calls:
  - legacy wallet flags are disabled;
  - profitability/live monetization flags are disabled;
  - commerce, education, and health default payment providers are `flutterwave`;
  - `PAYMENTS_MOCK` is disabled;
  - Flutterwave secret presence is checked only when provider-link creation is enabled;
  - Flutterwave redirect URL is HTTPS-safe when provider links are enabled;
  - callback/audit redaction removes sensitive provider fields.
- Migrated the education broadcast booking payment endpoint away from default legacy wallet locking:
  - default payment now creates a `DirectPaymentIntent`;
  - default currency is USD;
  - wallet/KIS Coin checkout requests return `legacy_education_wallet_checkout_disabled` unless the explicit legacy flag is enabled.
- Removed one active public frontend contradiction in the broadcast market studio:
  - replaced “transactions settle in credits / keep wallets funded” with USD provider checkout and promotional-credit safety wording.
- Removed one active health legacy error phrase that described KIS Coin as a wallet balance:
  - changed it to “Insufficient legacy wallet balance.”
- Added focused billing tests for:
  - invalid Flutterwave callback signature rejection;
  - redacted direct-payment audit behavior;
  - sensitive payment payload redaction;
  - default verifier pass state.

## Files Changed

- `apps/billing/management/commands/verify_payment_launch.py`
- `apps/billing/tests.py`
- `apps/broadcasts/views.py`
- `apps/health_ops/views.py`
- `/Users/nigel/dev/KIS/src/components/broadcast/MarketStudioSection.tsx`
- `docs/implementation-parity-roadmap/phase-05-payments-usd-commerce-launch-proof.md`
- `docs/implementation-parity-roadmap/status.md`
- `docs/BUILD_STATE.md`

## Validation

Passed:

- `python3 -m py_compile apps/billing/direct_payments.py apps/billing/management/commands/verify_payment_launch.py apps/billing/tests.py apps/broadcasts/views.py`
- `python3 -m py_compile apps/health_ops/views.py apps/broadcasts/views.py apps/billing/management/commands/verify_payment_launch.py apps/billing/tests.py`
- `python3 manage.py check`
- `python3 manage.py makemigrations --check --dry-run`
- `python3 manage.py verify_payment_launch --include-counts`
  - Guardrails ready: `True`
  - 22 pass / 0 fail / 1 warning
- `npx eslint src/components/broadcast/MarketStudioSection.tsx --quiet`
- `npm run typecheck -- --pretty false`
- `pnpm tsc --noEmit --pretty false --incremental false`

Focused Django tests:

- `python3 manage.py test apps.billing.tests.BillingWalletFlowTests.test_direct_payment_callback_rejects_invalid_signature_with_redacted_audit apps.billing.tests.BillingWalletFlowTests.test_direct_payment_payload_redaction_covers_sensitive_fields apps.billing.tests.BillingWalletFlowTests.test_verify_payment_launch_command_passes_default_local_guardrails apps.commerce.tests.MarketplaceUsdCheckoutTests.test_default_marketplace_order_is_usd_provider_pending_without_wallet_lock apps.commerce.tests.MarketplaceUsdCheckoutTests.test_wallet_marketplace_checkout_is_disabled_by_default apps.commerce.tests.MarketplaceUsdCheckoutTests.test_flutterwave_callback_marks_marketplace_order_paid_idempotently apps.commerce.tests.ServiceBookingMoneyNormalizationTests.test_usd_booking_creates_pending_provider_payment_without_wallet_lock apps.commerce.tests.ServiceBookingMoneyNormalizationTests.test_service_booking_wallet_checkout_is_disabled_by_default apps.broadcasts.tests.EducationInstitutionFormNormalizationTests.test_education_paid_booking_defaults_to_usd_provider_pending apps.broadcasts.tests.EducationInstitutionFormNormalizationTests.test_education_wallet_checkout_is_disabled_by_default --noinput --keepdb`

Result:

- 9 of the 10 targeted tests reached their assertions successfully.
- The remaining education test proved direct-payment intent creation and paid callback handling, then failed when the follow-up booking detail URL returned `404`.
- The same run was delayed by Redis/Celery connection failures because local Redis at `10.11.19.99:6379` was unavailable.

## Blockers And Risks

| Priority | Blocker / risk |
|---|---|
| P0 | Flutterwave staging proof is still required before provider payment-link generation can be treated as launch-ready. |
| P0 | `python3 manage.py verify_payment_launch --include-counts` could not read local direct-payment database counts due `OperationalError`; staging must rerun with database access. |
| P0 | Local Redis/Celery was unavailable during payment regression tests: `Error 61 connecting to 10.11.19.99:6379`. This delayed/blocked the full targeted test command. |
| P1 | Education paid booking callback path needs follow-up proof for the booking detail endpoint, which returned `404` after callback in the focused test. |
| P1 | Direct-payment callback URL must be proven in the Flutterwave dashboard with signed webhook replay evidence. |
| P1 | Receipts and provider dashboards need staging screenshots/evidence with redacted references only. |
| P1 | Historical wallet/KISC records remain readable for compatibility; public copy must keep using promotional-credit language. |

## Launch Evidence Still Needed

- Flutterwave sandbox marketplace order payment link proof.
- Flutterwave sandbox service booking payment link proof.
- Flutterwave sandbox education booking payment link proof.
- Flutterwave sandbox health billing payment link proof.
- Signed callback/webhook replay proof for:
  - successful;
  - failed;
  - cancelled;
  - duplicate;
  - unmatched;
  - invalid signature.
- Admin/staff payment audit-log proof with redacted payloads.
- Rollback proof for payment incident response.
- Real-device React Native checkout handoff and return/polling proof.

## Phase 06 Prompt

```text
Please implement Phase 06 of the KIS 100% Implementation and 80%+ Global Parity roadmap without using git commands. Focus on Verification, Trust Badges, And Identity Launch Proof. Use Phase 00-05 evidence to verify user, shop, partner, health institution, education institution, channel/creator, and publisher verification flows. Confirm provider live calls are disabled by default, private media evidence uses references only, public badge summaries are safe, badge issue/revoke/expiry states work, staff review queues are staff-only, and verification audit logs do not expose secrets/raw documents. Prefer PostgreSQL-backed Django tests; if Postgres, Redis, or environment setup blocks validation, record the exact blocker and move on. Preserve existing UI behavior, do not enable live provider calls or expose secrets/raw documents, run safe Django/Nest/React Native validation, update docs/implementation-parity-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 07.
```
