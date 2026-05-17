# KIS Profitability 80%+ Roadmap - Phase 18 Safe Evidence Storage, Approval Workflow, And Audit Trail Planning

Date: 2026-05-16

Status: Completed as a staff-only/read-only workflow plan. No database migration, evidence upload, approval write action, live charges, production payment provider connection, entitlement enforcement, payment instrument collection, or private health/payment/verification data exposure was enabled.

## Phase Objective

Plan the safe evidence storage, approval workflow, immutable audit trail, private media reference policy, reviewer roles, expiry reminders, and redacted staff serializer contract needed before KIS can store revenue-launch evidence.

This phase deliberately avoided new database tables because full workflow activation needs staff access review, private media review, audit review, and legal/security approval first.

## Backend Changes

Added staff-only evidence workflow plan:

- `apps/billing/profitability_evidence_workflow.py`

Added staff-only read-only endpoint:

- `GET /api/v1/billing/profitability-evidence-workflow-plan/`

Updated:

- `apps/billing/views.py`
- `apps/billing/urls.py`
- `apps/billing/tests.py`

### Workflow Plan Covers

- future `RevenueLaunchEvidenceRecord` model plan;
- future `RevenueLaunchEvidenceAuditEvent` model plan;
- approval states:
  - `draft`;
  - `submitted`;
  - `needs_changes`;
  - `approved`;
  - `rejected`;
  - `expired`;
  - `revoked`;
- reviewer roles:
  - legal;
  - pastoral/child-safety;
  - tax/accounting;
  - payment;
  - privacy/security;
  - release management;
- immutable audit event types;
- private media reference policy;
- redacted staff serializer contract;
- expiry/review reminder policy.

## Frontend Changes

Added evidence workflow service:

- `/Users/nigel/dev/KIS/src/services/profitabilityEvidenceWorkflowService.ts`

Added dashboard card:

- `/Users/nigel/dev/KIS/src/components/dashboard/EvidenceWorkflowPlanCard.tsx`

Updated billing route map:

- `/Users/nigel/dev/KIS/src/network/routes/billingRoutes.ts`

Wired the card into:

- `/Users/nigel/dev/KIS/src/screens/tabs/ProfileScreen.tsx`

## Redaction And Storage Rules

Future evidence records must not store:

- raw provider payloads;
- payment card data;
- bank account data;
- private health records;
- verification document bytes;
- raw storage paths;
- secret keys.

Future evidence records may store only:

- safe summaries;
- approval state;
- staff references;
- private `MediaAsset` references;
- expiry/review metadata;
- immutable redacted audit events.

## Safety Guardrails

This phase keeps:

- no database migration created;
- endpoint staff-only;
- workflow read-only;
- private media references only;
- no raw documents;
- no raw provider payloads;
- no payment instrument collection;
- no live charges;
- no entitlement enforcement;
- KIS promotional credits non-cash, non-transferable, non-withdrawable, and not exchange-rated.

## Validation

Backend compile:

```text
cd /Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis && python3 -m py_compile apps/billing/profitability_evidence_workflow.py apps/billing/views.py apps/billing/urls.py apps/billing/tests.py
```

Result: passed.

Django check:

```text
cd /Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis && python3 manage.py check
```

Result: passed.

Focused backend test:

```text
cd /Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis && python3 manage.py test apps.billing.tests.BillingWalletFlowTests.test_profitability_evidence_workflow_plan_is_staff_only_redacted --keepdb --noinput
```

Result: passed.

Focused frontend lint:

```text
cd /Users/nigel/dev/KIS && npx eslint src/components/dashboard/EvidenceWorkflowPlanCard.tsx src/services/profitabilityEvidenceWorkflowService.ts src/network/routes/billingRoutes.ts src/screens/tabs/ProfileScreen.tsx --quiet
```

Result: passed.

## Remaining Risks

- No durable evidence model exists yet. This phase is a migration-ready plan, not active storage.
- Future implementation must add migrations, strict staff permissions, immutable audit entries, private media signed access, redacted serializers, approval actions, and expiry reminders.
- Staff evidence screens must be hidden from ordinary users before adding write actions.
- Approval workflow should be reviewed by legal, privacy/security, finance, pastoral/child-safety, and release management before activation.

## Best Prompt For Phase 19

```text
Please implement Phase 19 of the KIS Profitability 80%+ Roadmap without using git commands. Focus on Low-Risk Revenue Evidence Storage Implementation. Build on the Phase 18 evidence workflow plan to add backend models/migrations for revenue launch evidence records and immutable audit events, using private media references only, redacted serializers, staff-only permissions, preview-only create/list/detail APIs, and focused tests for access control, redaction, and audit creation. Do not enable live charges, production payment providers, entitlement enforcement, payment instrument collection, or private health/payment/verification data exposure. Preserve existing APIs/UI behavior, run focused validation, update docs/profitability-roadmap/phase-19-revenue-evidence-storage-implementation.md and docs/BUILD_STATE.md with risks, validation, and the best prompt for Phase 20.
```
