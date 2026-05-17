# KIS Profitability 80%+ Roadmap - Phase 07 Health Revenue Engine

Date: 2026-05-16

Status: Completed as a safe locked-preview implementation. No live charges, hard gates, subscriptions, payment provider calls, service fees, appointment fees, provider payouts, care-plan charges, or health access restrictions were enabled.

## Phase Objective

Prepare health monetization for providers, institutions, dashboards, services, bookings, sessions, billing, and care operations without changing existing free health behavior.

This phase uses the disabled pricing catalog and keeps the financial redesign intact:

- health payments remain USD/direct-provider first;
- Health Provider Pro, Health Institution Growth, and Promotion Packages are visible but not live;
- appointment/service fee visibility is shown as a future reporting concept only;
- care-plan premium readiness is preview-only;
- direct provider payment state is visible without wallet/KIS-credit settlement;
- KIS promotional credits remain non-cash reward/subsidy credits only;
- health monetization copy avoids diagnosis claims and outcome guarantees.

## Frontend Changes

Added `Health Institution Growth` to the disabled frontend pricing catalog:

- `/Users/nigel/dev/KIS/src/services/profitabilityPricing.ts`

Added reusable health revenue preview component:

- `/Users/nigel/dev/KIS/src/components/profitability/HealthRevenuePreviewCard.tsx`

Wired preview states into:

- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/HealthManagementModal.tsx`
- `/Users/nigel/dev/KIS/src/features/health-dashboard/ui/InstitutionDashboardShell.tsx`
- `/Users/nigel/dev/KIS/src/screens/health/InstitutionServicesCatalogScreen.tsx`
- `/Users/nigel/dev/KIS/src/screens/health/HealthInstitutionCardsScreen.tsx`
- `/Users/nigel/dev/KIS/src/screens/health/HealthServiceSessionScreen.tsx`

## Preview Areas

| Area | Preview State | Purpose |
|---|---|---|
| Profile health management | Health revenue engine preview | Shows provider analytics, care-plan readiness, USD payment state, service fees, and promotion entry points. |
| Provider dashboard | Provider growth preview | Shows Health Provider Pro and Health Institution Growth readiness near operational analytics. |
| Service catalog | Service revenue preview | Shows service fee visibility, booking payment state, provider analytics, and promotion readiness. |
| Health cards / booking | Health booking revenue preview | Shows USD booking/payment state and future reviewed promotion without changing booking behavior. |
| Service session | Service session revenue preview | Shows session workflow value, payment state, care-plan readiness, and provider analytics. |
| Billing engine | Billing visibility preview | Shows provider fees, platform fee policy, refund impact, payment references, and audit-safe payment state. |

## Locked-But-Visible Rules

Every health revenue preview uses:

- disabled Health Provider Pro, Health Institution Growth, and Promotion Packages price labels;
- "NOT LIVE" badge;
- explicit copy that current free health behavior remains available;
- USD/direct-provider-first payment copy;
- promotional-credit safety copy;
- provider-fee/platform-fee/refund/payment-state visibility as reporting previews only;
- medical safety copy that this is operational readiness only, not medical diagnosis or outcome advice.

No component opens plan checkout, changes entitlements, hides health tools, starts provider calls, creates appointment fees, charges care-plan fees, changes billing settlement, or changes health access rules.

## Health Safety Position

- KIS promotional credits are not cash, not transferable, not withdrawable, and not exchange-rated.
- New paid health payment copy remains USD/direct-provider first.
- Health copy avoids diagnosis claims, medical outcome guarantees, and unsafe care promises.
- Health promotion is described as future reviewed placement, not automatic ad buying.
- Payment visibility is shown as future reporting, not active deduction or charge collection.
- Existing health institution, service, appointment, session, billing, and care workflow behavior is preserved.

## Validation

Focused validation command:

```text
cd /Users/nigel/dev/KIS && npx eslint src/components/profitability/HealthRevenuePreviewCard.tsx src/services/profitabilityPricing.ts src/screens/tabs/profile-screen/HealthManagementModal.tsx src/features/health-dashboard/ui/InstitutionDashboardShell.tsx src/screens/health/InstitutionServicesCatalogScreen.tsx src/screens/health/HealthInstitutionCardsScreen.tsx src/screens/health/HealthServiceSessionScreen.tsx --quiet
```

Result: passed.

## Remaining Risks

- This phase is preview-only. It does not implement paid subscriptions, entitlements, health provider payouts, service fees, care-plan fees, provider fee reconciliation, taxes, refunds, invoices, promotion checkout, or support workflows.
- Health pricing requires product, legal, medical compliance, accounting, provider-cost, refund, and market review before launch.
- Paid appointment/session access must be reconciled with Flutterwave payment state and clinical safety rules before activation.
- Care-plan monetization must not imply diagnosis, emergency support, guaranteed outcomes, or replacement of licensed medical care.
- Backend feature flags and entitlement enforcement are still intentionally deferred.

## Best Prompt For Phase 08

```text
Please implement Phase 08 of the KIS Profitability 80%+ Roadmap without using git commands. Focus on Partner And Ministry Workspace Revenue Engine. Using the disabled pricing catalog and previous monetization previews, add safe locked-but-visible Partner Workspace Pro/Enterprise states across partner workspaces, subrooms/channels, member roles, announcements, events, moderation, analytics, and group messaging surfaces. Prepare workspace seat visibility, premium moderation/audit previews, event/promotion entry points, partner analytics previews, USD-only payment copy, and enterprise contact readiness without enabling live charges or hard-blocking current free partner behavior. Keep KIS promotional credits non-cash, non-transferable, non-withdrawable, and not exchange-rated, preserve existing partner APIs/UI behavior, run focused validation, update docs/profitability-roadmap/phase-08-partner-workspace-revenue-engine.md and docs/BUILD_STATE.md with risks, validation, and the best prompt for Phase 09.
```
