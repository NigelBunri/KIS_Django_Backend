# KIS Profitability 80%+ Roadmap - Phase 21 Reviewer Role Enforcement, Evidence Expiry Reminders, And Launch Readiness Scoring

Date: 2026-05-16

Status: Completed as a staff-only governance layer. No live charges, production payment provider connection, entitlement enforcement, payment instrument collection, wallet/KISC money behavior, or private health/payment/verification data exposure was enabled.

## Phase Objective

Harden the revenue launch evidence system with:

- reviewer-role checks per evidence area;
- expiry-aware readiness scoring;
- reminder metadata for evidence near expiry;
- staff-only filtered readiness summaries;
- frontend indicators for approved, expired, missing, or blocked evidence areas.

## Backend Changes

Added readiness/governance module:

- `apps/billing/profitability_revenue_readiness.py`

Updated:

- `apps/billing/serializers.py`
- `apps/billing/views.py`
- `apps/billing/urls.py`
- `apps/billing/tests.py`

### Reviewer Role Enforcement

Review decisions now require a mapped reviewer role unless the actor is superuser.

Supported role mapping includes:

- `legal_review` -> `legal_reviewer`
- `pastoral_child_safety_review` -> `pastoral_safety_reviewer`
- `tax_accounting_review` -> `tax_accounting_reviewer`
- `flutterwave_sandbox_proof` -> `payment_reviewer`
- `invoice_receipt_proof` -> `tax_accounting_reviewer`
- `refund_support_proof` -> `support_reviewer`
- `entitlement_grace_policy` -> `product_reviewer`
- `promotion_sponsored_label_policy` -> `trust_safety_reviewer`
- `verification_fee_policy` -> `verification_reviewer`
- `enterprise_contract_policy` -> `enterprise_reviewer`
- `privacy_analytics_policy` -> `privacy_security_reviewer`
- `rollback_proof` -> `release_manager`

Roles are read from Django groups and safe user metadata keys such as `revenue_reviewer_roles`, `staff_roles`, or `roles`.

### Readiness Endpoint

Added staff-only endpoint:

- `GET /api/v1/billing/profitability-revenue-readiness/`

The endpoint returns:

- readiness percent;
- approved/blocked/expired/missing counts;
- per-area state;
- required reviewer role;
- whether current staff user can review;
- latest record metadata;
- expiry reminder metadata;
- no-go status until all required evidence is approved and non-expired.

### Serializer Enhancements

Revenue launch evidence records now expose:

- `required_reviewer_role`;
- `is_expired`.

## Frontend Changes

Added readiness service:

- `/Users/nigel/dev/KIS/src/services/profitabilityRevenueReadinessService.ts`

Updated:

- `/Users/nigel/dev/KIS/src/components/dashboard/RevenueEvidenceAdminPanel.tsx`
- `/Users/nigel/dev/KIS/src/services/revenueLaunchEvidenceService.ts`
- `/Users/nigel/dev/KIS/src/network/routes/billingRoutes.ts`

### Frontend Indicators

The revenue evidence admin panel now shows:

- readiness percent;
- approved/total evidence count;
- blocked/expired count;
- per-area state chips;
- required reviewer role;
- expired record indicator;
- no-go status copy based on backend readiness.

## Safety Guardrails

This phase keeps:

- all readiness APIs staff-only;
- launch status read-only;
- no live payment behavior;
- no entitlement enforcement;
- no payment instrument collection;
- no raw provider payloads;
- no private health/payment/verification data exposure;
- KIS promotional credits non-cash, non-transferable, non-withdrawable, and not exchange-rated.

## Validation

Backend compile:

```text
cd /Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis && python3 -m py_compile apps/billing/profitability_revenue_readiness.py apps/billing/serializers.py apps/billing/views.py apps/billing/urls.py apps/billing/tests.py
```

Result: passed.

Django check:

```text
cd /Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis && python3 manage.py check
```

Result: passed.

Migration dry run:

```text
cd /Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis && python3 manage.py makemigrations --check --dry-run
```

Result: passed, no changes detected.

Focused backend tests:

```text
cd /Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis && python3 manage.py test apps.billing.tests.BillingWalletFlowTests.test_revenue_readiness_scores_and_reviewer_roles_are_enforced --keepdb --noinput
```

Result: passed.

```text
cd /Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis && python3 manage.py test apps.billing.tests.BillingWalletFlowTests.test_revenue_launch_evidence_storage_is_staff_only_redacted_and_audited --keepdb --noinput
```

Result: passed.

Focused frontend lint:

```text
cd /Users/nigel/dev/KIS && npx eslint src/components/dashboard/RevenueEvidenceAdminPanel.tsx src/services/profitabilityRevenueReadinessService.ts src/services/revenueLaunchEvidenceService.ts src/network/routes/billingRoutes.ts --quiet
```

Result: passed.

## Remaining Risks

- Reviewer roles are currently read from Django groups or user metadata. A dedicated staff-role management UI is still needed for operations.
- Reminder metadata is computed but not dispatched. Future phases should connect staff notifications after privacy/routing review.
- Approval separation is improved, but superusers still bypass reviewer role checks.
- The admin panel still lives in the profile dashboard; a dedicated staff operations surface is recommended before heavy production use.
- Readiness scoring is evidence-based and does not itself authorize monetization. Final legal/product/release sign-off is still required.

## Best Prompt For Phase 22

```text
Please implement Phase 22 of the KIS Profitability 80%+ Roadmap without using git commands. Focus on Staging Monetization Readiness Proof And Evidence Capture. Build on the reviewer-role readiness scoring and evidence admin UI to add safe staging evidence workflows for Flutterwave sandbox payment links, signed webhook replay proof, invoice/receipt proof, refund/support proof, rollback drills, and private media signed-access proof. Store only redacted summaries and private MediaAsset references in revenue launch evidence records. Do not enable live charges, production payment providers, entitlement enforcement, payment instrument collection, or private health/payment/verification data exposure. Preserve existing APIs/UI behavior, run focused validation, update docs/profitability-roadmap/phase-22-staging-monetization-readiness-proof.md and docs/BUILD_STATE.md with risks, validation, and the best prompt for Phase 23.
```
