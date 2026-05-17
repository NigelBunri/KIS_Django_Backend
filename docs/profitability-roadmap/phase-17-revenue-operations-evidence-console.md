# KIS Profitability 80%+ Roadmap - Phase 17 Revenue Operations Admin Evidence Console

Date: 2026-05-16

Status: Completed as a staff-only/read-only evidence readiness foundation. No live charges, production payment provider connection, entitlement enforcement, payment instrument collection, private document upload, raw provider payload storage, or private health/payment/verification data exposure was enabled.

## Phase Objective

Create the first Revenue Operations Admin Evidence Console foundation so KIS can later prove monetization readiness before any pricing, subscription, promotion, verification-fee, or enterprise revenue flow goes live.

This phase builds on:

- pricing launch gate;
- billing provider sandbox readiness;
- subscription lifecycle planning;
- profitability command center;
- disabled pricing catalog;
- entitlement metadata;
- KIS promotional-credit safety model.

## Backend Changes

Added staff-only evidence console summary:

- `apps/billing/profitability_revenue_ops.py`

Added staff-only read-only endpoint:

- `GET /api/v1/billing/profitability-revenue-ops-evidence/`

Updated:

- `apps/billing/views.py`
- `apps/billing/urls.py`
- `apps/billing/tests.py`

### Evidence Areas Covered

- legal review;
- pastoral and child-safety review;
- tax and accounting review;
- Flutterwave sandbox proof;
- invoice/receipt proof;
- refund/support proof;
- entitlement grace policy;
- promotion sponsored-label policy;
- verification fee policy;
- enterprise contract policy;
- privacy analytics policy;
- rollback proof.

All evidence areas are currently `evidence_required`. The console is intentionally read-only and does not store private attachments yet.

## Frontend Changes

Added revenue operations evidence service:

- `/Users/nigel/dev/KIS/src/services/profitabilityRevenueOpsService.ts`

Added dashboard card:

- `/Users/nigel/dev/KIS/src/components/dashboard/RevenueOpsEvidenceCard.tsx`

Updated billing route map:

- `/Users/nigel/dev/KIS/src/network/routes/billingRoutes.ts`

Wired the card into:

- `/Users/nigel/dev/KIS/src/screens/tabs/ProfileScreen.tsx`

## Safety Guardrails

This phase keeps:

- endpoint staff-only;
- console read-only;
- live charges disabled;
- production provider connection disabled;
- entitlement enforcement disabled;
- payment instrument collection disabled;
- raw provider payloads excluded;
- private health/payment/verification data excluded;
- KIS promotional credits non-cash, non-transferable, non-withdrawable, and not exchange-rated.

## Validation

Backend compile:

```text
cd /Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis && python3 -m py_compile apps/billing/profitability_revenue_ops.py apps/billing/views.py apps/billing/urls.py apps/billing/tests.py
```

Result: passed.

Django check:

```text
cd /Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis && python3 manage.py check
```

Result: passed.

Focused backend test:

```text
cd /Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis && python3 manage.py test apps.billing.tests.BillingWalletFlowTests.test_profitability_revenue_ops_evidence_console_is_staff_only_read_only --keepdb --noinput
```

Result: passed.

Focused frontend lint:

```text
cd /Users/nigel/dev/KIS && npx eslint src/components/dashboard/RevenueOpsEvidenceCard.tsx src/services/profitabilityRevenueOpsService.ts src/network/routes/billingRoutes.ts src/screens/tabs/ProfileScreen.tsx --quiet
```

Result: passed.

## Remaining Risks

- Evidence tracking is placeholder/read-only only. No durable evidence model, upload flow, private media attachment, approval workflow, or audit history was added.
- A real evidence console must add strict staff permissions, immutable audit logs, private media references, redaction, and reviewer sign-off.
- Regular users should not be given access to staff evidence details.
- Production monetization still requires signed legal, tax, pastoral/child-safety, payment, privacy, support, and rollback evidence.

## Best Prompt For Phase 18

```text
Please implement Phase 18 of the KIS Profitability 80%+ Roadmap without using git commands. Focus on Safe Evidence Storage, Approval Workflow, And Audit Trail Planning. Build on the staff-only revenue operations evidence console to add low-risk backend models or documented migration plans for revenue launch evidence records, approval states, immutable audit entries, private media references, reviewer roles, expiry/review reminders, and redacted staff serializers. Keep the system read-only or preview-only where full workflow risk is high. Do not enable live charges, production payment providers, entitlement enforcement, payment instrument collection, or private health/payment/verification data exposure. Preserve existing APIs/UI behavior, run focused validation, update docs/profitability-roadmap/phase-18-evidence-storage-approval-audit-planning.md and docs/BUILD_STATE.md with risks, validation, and the best prompt for Phase 19.
```
