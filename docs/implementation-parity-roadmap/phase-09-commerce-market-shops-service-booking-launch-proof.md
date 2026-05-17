# Phase 09 - Commerce, Market, Shops, And Service Booking Launch Proof

Date: 2026-05-17

## Scope

This phase verifies the launch-safe commerce slice across marketplace discovery, shop/product/service management, cart/order/service-booking reliability, seller trust, USD-only direct-payment readiness, fulfillment and complaint windows, reviews/questions safety, commerce media safety, notification read-state hooks, and rollback/audit evidence.

## Implemented

- Added a read-only commerce launch verifier:
  - `python3 manage.py verify_commerce_launch`
  - `python3 manage.py verify_commerce_launch --strict`
  - `python3 manage.py verify_commerce_launch --include-counts`
- The verifier checks:
  - commerce route contracts for discovery, shops, products, product reviews/questions, shop services, service bookings, complaints, carts, marketplace orders, and provider orders;
  - legacy wallet/KIS-credit checkout/deposit/transfer/conversion flags remain disabled;
  - commerce default payment provider remains `flutterwave`;
  - direct provider payment links remain disabled by default unless staging/production evidence is approved;
  - mock payments are not enabled;
  - payment payload redaction works without printing secrets or payment data;
  - central media safety is enabled for commerce;
  - dangerous executable/script upload extensions remain blocked;
  - common commerce image/document MIME and extension policy is covered;
  - marketplace 3-day auto-satisfaction and service booking completion windows are present.
- Added focused PostgreSQL-backed regression coverage for:
  - the commerce launch verifier safe-default output;
  - USD-first marketplace checkout with direct payment intents;
  - disabled wallet/KIS-credit marketplace checkout;
  - Flutterwave callback idempotency;
  - marketplace provider-completed orders moving into the 3-day satisfaction window;
  - marketplace auto-satisfaction after the complaint window when no complaint exists;
  - historical KISC order compatibility labels;
  - product detail trust/review/question/fulfillment summaries;
  - cart subtotal sync;
  - service booking USD provider-pending payment flow;
  - disabled service booking wallet/KIS-credit checkout.

## Evidence

Passed:

- `python3 -m py_compile apps/commerce/management/commands/verify_commerce_launch.py apps/commerce/tests.py apps/commerce/services.py apps/commerce/views.py apps/commerce/serializers.py`
- `python3 manage.py check`
- `python3 manage.py makemigrations --check --dry-run`
- `python3 manage.py verify_commerce_launch`
- `python3 manage.py verify_commerce_launch --include-counts`
- `python3 manage.py test apps.commerce.tests.CommerceLaunchProofCommandTests apps.commerce.tests.MarketplaceUsdCheckoutTests apps.commerce.tests.ServiceBookingMoneyNormalizationTests apps.commerce.tests.CommerceAmazonCoreApiTests --noinput --keepdb`
  - PostgreSQL-backed: 13 tests.
- React Native `npm run typecheck -- --pretty false`
- React Native `npx eslint src/screens/broadcast/market src/screens/market src/components/broadcast/MarketStudioSection.tsx --quiet`
- Nest `pnpm tsc --noEmit --pretty false --incremental false`

## Validation Warnings

- `verify_commerce_launch --include-counts` passed guardrails but could not read optional commerce/payment counts locally due `OperationalError`. Staging must run it with real database access.
- The first focused commerce test run hit local Redis/Celery result-backend retries while notification side effects were not mocked. Tests were tightened to mock notification delivery enqueue for commerce state-machine assertions, and the focused test suite then passed.
- Flutterwave sandbox link creation and signed callback proof were not executed locally because live provider calls remain disabled by default.

## Remaining Launch Evidence Needed

| Priority | Evidence gap |
|---|---|
| P0 | Staging `python3 manage.py verify_commerce_launch --strict --include-counts` with migrated PostgreSQL access. |
| P0 | Flutterwave sandbox proof for marketplace order and service booking payment link creation. |
| P0 | Signed Flutterwave webhook replay proof for paid, failed, cancelled, duplicate, and unmatched commerce payments. |
| P0 | Real-device React Native QA for market discovery, product/service detail, cart, order creation, checkout handoff, return refresh, pending/failed/cancelled UI, provider order completion, buyer satisfaction, and complaint creation. |
| P0 | Celery/Redis staging proof that marketplace auto-satisfaction runs after three days and does not run when a complaint is open. |
| P1 | Commerce notification badge proof for product/service/shop/order updates and service booking reminders. |
| P1 | Product/service media QA proving unsafe or quarantined images/documents do not publish or render publicly. |
| P1 | Seller trust badge product approval for which verification/badge states are visible to buyers at launch. |

## Risk Position

Commerce is launchable only with direct USD payment evidence and real-device QA. Legacy wallet/KIS-credit-as-money flows remain disabled by default and must stay disabled for production launch. Historical KISC records remain readable through compatibility labels.

## Phase 10 Prompt

```text
Please implement Phase 10 of the KIS 100% Implementation and 80%+ Global Parity roadmap without using git commands. Focus on Education Courses, Institutions, Enrollment, And Learning Launch Proof. Use Phase 00-09 evidence to verify education discovery, institution/course/module/lesson management, enrollment/payment state, certificates, reviews/Q&A, institution trust badges, media safety for education uploads, notification badge read-state, offline/low-bandwidth learning placeholders, and rollback/audit evidence. Prefer PostgreSQL-backed Django tests; if Postgres, Redis, Flutterwave sandbox, or environment setup blocks validation, record the exact blocker and move on. Preserve existing UI behavior, do not enable live charges or legacy wallet/KIS-credit-as-money flows, do not expose secrets/private media paths/payment data, run safe Django/Nest/React Native validation, update docs/implementation-parity-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 11.
```
