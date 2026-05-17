# KIS Profitability 80%+ Roadmap - Phase 13 Billing Readiness, Entitlements, And Feature Flag Architecture

Date: 2026-05-16

Status: Completed as a safe non-enforcing foundation. No live charges, payment provider connection, subscriptions, trials, entitlement gates, usage-limit enforcement, promotion checkout, enterprise lead capture, or hard blocks were enabled.

## Phase Objective

Add a shared backend/frontend foundation for plan identifiers, entitlement checks, usage meters, free-plan limits, trial-readiness metadata, billing status placeholders, and profitability feature flags across all locked-preview monetization surfaces.

This phase keeps the profitability roadmap safe:

- all plan and entitlement metadata is preview-only;
- existing free behavior remains available;
- entitlement helpers return pass-through access and do not block users;
- billing flags default off;
- live payment providers are not connected;
- KIS promotional credits remain non-cash reward/subsidy credits only.

## Backend Changes

Added backend profitability entitlement catalog:

- `apps/billing/profitability_entitlements.py`

Added authenticated read-only endpoint:

- `GET /api/v1/billing/profitability-entitlements/`

Updated:

- `apps/billing/views.py`
- `apps/billing/urls.py`
- `config/settings/base.py`
- `apps/billing/tests.py`

### Backend Catalog Includes

- plan identifiers and billing modes for Consumer Plus, Family Plus, Creator Pro/Growth, Institution Starter/Growth, Partner Workspace Pro, Seller Pro, Instructor Pro, Education Institution Pro, Health Provider Pro, Health Institution Growth, Verification Processing, Promotion Packages, and Enterprise;
- disabled feature flags:
  - `KIS_PROFITABILITY_BILLING_ENABLED`;
  - `KIS_PROFITABILITY_ENTITLEMENTS_ENFORCED`;
  - `KIS_PROFITABILITY_TRIALS_ENABLED`;
  - `KIS_PROFITABILITY_PROMOTION_CHECKOUT_ENABLED`;
  - `KIS_PROFITABILITY_ENTERPRISE_LEADS_ENABLED`;
- non-enforcing entitlement keys;
- non-enforcing usage meters and free-plan limits;
- billing status placeholders;
- promotional-credit safety policy.

## Frontend Changes

Expanded frontend pricing foundation:

- `/Users/nigel/dev/KIS/src/services/profitabilityPricing.ts`

Added frontend entitlement catalog service:

- `/Users/nigel/dev/KIS/src/services/profitabilityEntitlementsService.ts`

Updated billing route map:

- `/Users/nigel/dev/KIS/src/network/routes/billingRoutes.ts`

### Frontend Foundation Includes

- `PROFITABILITY_FEATURE_FLAGS`;
- `PROFITABILITY_ENTITLEMENTS`;
- `PROFITABILITY_USAGE_METERS`;
- `getProfitabilityFeatureFlag`;
- `getProfitabilityEntitlement`;
- `getProfitabilityUsageMeter`;
- `canUseProfitabilityFeature`;
- `fetchProfitabilityEntitlementCatalog`;
- local fallback catalog when the backend endpoint is unavailable.

## Non-Enforcement Rules

This phase intentionally preserves pass-through behavior:

- `enabled` remains `false`;
- `enforcement_enabled` remains `false`;
- `billing_live` remains `false`;
- entitlement checks return `allowed: true`;
- usage meters report limits but are not enforced;
- free-plan limits are planning metadata only;
- trial metadata is preview-only;
- enterprise lead capture is disabled;
- promotion checkout is disabled;
- no live payment provider is connected.

## Safety Position

- KIS promotional credits are not cash, not transferable, not withdrawable, and not exchange-rated.
- This phase does not reintroduce wallet/KISC as money.
- Feature flags must remain disabled until legal/product/security/QA approval.
- Entitlement enforcement must not launch until existing users, institutions, creators, partners, and health/education operators have a migration and grace policy.
- Promotions require sponsored labels, moderation, frequency caps, child/youth filters, and audit logs before launch.

## Validation

Backend compile:

```text
cd /Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis && python3 -m py_compile config/settings/base.py apps/billing/profitability_entitlements.py apps/billing/views.py apps/billing/urls.py apps/billing/tests.py
```

Result: passed.

Django check:

```text
cd /Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis && python3 manage.py check
```

Result: passed.

Focused backend test:

```text
cd /Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis && python3 manage.py test apps.billing.tests.BillingWalletFlowTests.test_profitability_entitlement_catalog_is_preview_only --keepdb --noinput
```

Result: passed.

Focused frontend lint:

```text
cd /Users/nigel/dev/KIS && npx eslint src/services/profitabilityPricing.ts src/services/profitabilityEntitlementsService.ts src/network/routes/billingRoutes.ts --quiet
```

Result: passed.

## Remaining Risks

- This phase is metadata-only. It does not implement real subscriptions, checkout, billing provider state, invoices, refunds, taxes, support workflows, or entitlement enforcement.
- Backend and frontend catalog values are duplicated and must be reconciled before live billing.
- Usage meters are not connected to real counted activity yet.
- Feature flags exist but are not wired into every monetized surface.
- Launching enforcement requires migration/grace rules, billing QA, legal review, support playbooks, and rollback plans.

## Best Prompt For Phase 14

```text
Please implement Phase 14 of the KIS Profitability 80%+ Roadmap without using git commands. Focus on Revenue Analytics, Conversion Tracking, And Profitability Command Center. Build on the disabled pricing catalog, entitlement metadata, usage meters, and locked-preview monetization surfaces to add safe backend/frontend foundations for revenue analytics without tracking private sensitive data. Add read-only analytics placeholders or endpoints for plan-interest events, upgrade prompt impressions, verification fee interest, promotion package interest, enterprise packaging interest, usage-meter summaries, direct USD payment readiness, and module-level revenue potential across profile, Bible, messaging, broadcast/channels, partners, commerce, education, health, verification, and public web. Do not enable live charges, intrusive tracking, dark patterns, or private health/payment/verification data exposure. Preserve existing APIs/UI behavior, run focused validation, update docs/profitability-roadmap/phase-14-revenue-analytics-command-center.md and docs/BUILD_STATE.md with risks, validation, and the best prompt for Phase 15.
```
