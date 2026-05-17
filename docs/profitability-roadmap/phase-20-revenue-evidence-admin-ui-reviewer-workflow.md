# KIS Profitability 80%+ Roadmap - Phase 20 Revenue Evidence Admin UI And Reviewer Workflow Hardening

Date: 2026-05-16

Status: Completed as a staff/admin UI foundation. No live charges, production payment provider connection, entitlement enforcement, payment instrument collection, wallet/KISC money behavior, or private health/payment/verification data exposure was enabled.

## Phase Objective

Build on the Phase 19 staff-only revenue launch evidence APIs and add a practical staff/admin frontend surface for:

- listing evidence records;
- creating safe redacted evidence records;
- viewing reviewer-role/status information;
- displaying private media references safely;
- submitting evidence;
- approving evidence;
- requesting changes;
- rejecting evidence;
- revoking evidence;
- rendering audit timeline entries;
- showing clear no-go launch messaging.

## Backend Status

No new backend behavior was required in this phase. The Phase 19 APIs remain the authority:

- `GET/POST /api/v1/billing/revenue-launch-evidence/`
- `GET/PATCH /api/v1/billing/revenue-launch-evidence/{id}/`
- `POST /api/v1/billing/revenue-launch-evidence/{id}/submit/`
- `POST /api/v1/billing/revenue-launch-evidence/{id}/approve/`
- `POST /api/v1/billing/revenue-launch-evidence/{id}/needs-changes/`
- `POST /api/v1/billing/revenue-launch-evidence/{id}/reject/`
- `POST /api/v1/billing/revenue-launch-evidence/{id}/revoke/`

Focused backend validation was re-run to confirm the existing API still enforces staff-only access, redaction, and audit creation.

## Frontend Changes

Expanded revenue launch evidence service:

- `/Users/nigel/dev/KIS/src/services/revenueLaunchEvidenceService.ts`

Added admin/reviewer panel:

- `/Users/nigel/dev/KIS/src/components/dashboard/RevenueEvidenceAdminPanel.tsx`

Updated billing routes:

- `/Users/nigel/dev/KIS/src/network/routes/billingRoutes.ts`

Wired the admin panel into:

- `/Users/nigel/dev/KIS/src/screens/tabs/ProfileScreen.tsx`

## UI Behavior

The admin panel supports:

- evidence area selector;
- redacted evidence creation form;
- optional private `MediaAsset` id reference field;
- list of latest evidence records;
- selected evidence state display;
- submit/approve/needs-changes/reject/revoke actions;
- reviewer display;
- safe private media reference display;
- audit timeline preview;
- empty/loading/error states;
- explicit no-go messaging.

The UI copy warns staff not to paste:

- secrets;
- raw provider payloads;
- payment data;
- private documents;
- private health/payment/verification details.

## Safety Guardrails

This phase keeps:

- staff-only backend authority;
- no payment activation;
- no entitlement enforcement;
- no payment instrument collection;
- no raw provider payload display;
- no raw document display;
- no private health/payment/verification data exposure;
- KIS promotional credits non-cash, non-transferable, non-withdrawable, and not exchange-rated.

## Validation

Frontend lint:

```text
cd /Users/nigel/dev/KIS && npx eslint src/components/dashboard/RevenueEvidenceAdminPanel.tsx src/services/revenueLaunchEvidenceService.ts src/network/routes/billingRoutes.ts src/screens/tabs/ProfileScreen.tsx --quiet
```

Result: passed.

Django check:

```text
cd /Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis && python3 manage.py check
```

Result: passed.

Focused backend test:

```text
cd /Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis && python3 manage.py test apps.billing.tests.BillingWalletFlowTests.test_revenue_launch_evidence_storage_is_staff_only_redacted_and_audited --keepdb --noinput
```

Result: passed.

## Remaining Risks

- The admin panel is intentionally compact and embedded in the profile dashboard. A dedicated staff operations screen may be needed before heavy use.
- Reviewer-role enforcement is still mostly visibility/workflow-level. Future phases should add stricter role/permission checks per evidence area.
- Private media references display as ids only; staging must prove signed access and redacted previews before operational use.
- The UI uses backend staff-only protection, but future navigation should hide this panel from non-staff users instead of relying only on API denial.
- Approval actions are available to any staff/admin user accepted by the backend; stricter approval separation should be added before launch sign-off.

## Best Prompt For Phase 21

```text
Please implement Phase 21 of the KIS Profitability 80%+ Roadmap without using git commands. Focus on Reviewer Role Enforcement, Evidence Expiry Reminders, And Launch Readiness Scoring. Build on the Phase 19 evidence storage APIs and Phase 20 admin UI to add safer reviewer-role checks per evidence area, expiry/review reminder metadata, readiness scoring based on approved evidence, staff-only filtered summaries, and frontend indicators showing which evidence areas are approved, expired, missing, or blocked. Do not enable live charges, production payment providers, entitlement enforcement, payment instrument collection, or private health/payment/verification data exposure. Preserve existing APIs/UI behavior, run focused validation, update docs/profitability-roadmap/phase-21-reviewer-role-expiry-readiness-scoring.md and docs/BUILD_STATE.md with risks, validation, and the best prompt for Phase 22.
```
