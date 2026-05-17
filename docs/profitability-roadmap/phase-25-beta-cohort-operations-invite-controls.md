# KIS Profitability 80%+ Roadmap - Phase 25 Beta Cohort Operations, Invite Controls, And Support Readiness

Date: 2026-05-17

Status: Completed as a staff-only beta cohort operations plan. No live charges, production payment provider connection, entitlement enforcement, payment instrument collection, promotion checkout, enterprise lead capture, wallet/KISC money behavior, or private health/payment/verification data exposure was enabled.

## Phase Objective

Build on Phase 24 limited beta planning to add operational readiness for selected beta cohorts while keeping all monetization execution gated.

This phase adds:

- beta cohort planning;
- invite eligibility summaries;
- support owner role tracking;
- rollback owner role tracking;
- incident escalation templates;
- staff-only beta operations checklists;
- admin indicators for `ready`, `paused`, and `blocked` cohorts.

## Backend Changes

Added beta operations planning module:

- `apps/billing/profitability_beta_operations.py`

Added staff-only endpoint:

- `GET /api/v1/billing/profitability-beta-operations/`

Updated:

- `apps/billing/views.py`
- `apps/billing/urls.py`
- `apps/billing/tests.py`

## Cohort Operations Model

The endpoint derives cohorts from Phase 24 beta modules and reports:

- cohort state;
- module state;
- audience;
- manual invite policy;
- max initial cohort size;
- support owner role;
- rollback owner role;
- incident owner role;
- support readiness checklist;
- rollback readiness checklist;
- incident escalation levels;
- missing evidence areas;
- frontend indicator tone.

## Invite Controls

Current rules:

- invite-only;
- no public beta signup;
- no public waitlist;
- staff approval required for every participant;
- no child/youth monetization beta;
- no payment instrument collection;
- no live provider charges;
- no entitlement enforcement;
- no hard-blocking existing free behavior.

## Frontend Changes

Added beta operations service:

- `/Users/nigel/dev/KIS/src/services/profitabilityBetaOperationsService.ts`

Updated:

- `/Users/nigel/dev/KIS/src/components/dashboard/RevenueEvidenceAdminPanel.tsx`
- `/Users/nigel/dev/KIS/src/network/routes/billingRoutes.ts`

### UI Behavior

The revenue evidence admin panel now shows:

- beta cohort operations go/no-go;
- ready, paused, and blocked cohort counts;
- per-cohort status chips;
- support owner role;
- rollback owner role;
- clear copy that invites are manual and gated.

## Safety Guardrails

This phase keeps:

- endpoint staff-only;
- summaries read-only;
- live charges disabled;
- production provider calls disabled;
- entitlement enforcement disabled;
- payment instrument collection disabled;
- promotion checkout disabled;
- enterprise lead capture disabled;
- private health/payment/verification data excluded;
- KIS promotional credits non-cash, non-transferable, non-withdrawable, and not exchange-rated.

## Validation

Backend compile:

```text
cd /Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis && python3 -m py_compile apps/billing/profitability_beta_operations.py apps/billing/views.py apps/billing/urls.py apps/billing/tests.py
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

Focused backend test:

```text
cd /Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis && python3 manage.py test apps.billing.tests.BillingWalletFlowTests.test_beta_operations_are_staff_only_and_invites_remain_gated --keepdb --noinput
```

Result: passed.

Focused frontend lint:

```text
cd /Users/nigel/dev/KIS && npx eslint src/components/dashboard/RevenueEvidenceAdminPanel.tsx src/services/profitabilityBetaOperationsService.ts src/network/routes/billingRoutes.ts --quiet
```

Result: passed.

## Remaining Risks

- This phase does not create real beta invites or cohorts in the database.
- Named human owners still need operational assignment before beta.
- Real support inboxes, escalation contacts, and rollback drills still need staging evidence.
- Cohorts remain blocked or paused until evidence, owner assignment, and production go/no-go are complete.
- Live monetization remains unauthorized until explicit future approval.

## Best Prompt For Phase 26

```text
Please implement Phase 26 of the KIS Profitability 80%+ Roadmap without using git commands. Focus on Beta Incident Drill, Support Runbook Evidence, And Rollback Simulation. Build on beta cohort operations, limited beta launch planning, production go/no-go, staging proof workflows, reviewer-role readiness scoring, and revenue evidence admin UI to add safe staff-only incident drill templates, support runbook evidence capture guidance, rollback simulation checklists, cohort freeze criteria, user-safe beta pause messaging, and admin indicators for drill-missing, drill-ready, and rollback-ready states. Do not enable live charges, production payment providers, entitlement enforcement, payment instrument collection, promotion checkout, enterprise lead capture, or private health/payment/verification data exposure. Preserve existing APIs/UI behavior, run focused validation, update docs/profitability-roadmap/phase-26-beta-incident-drill-rollback-simulation.md and docs/BUILD_STATE.md with risks, validation, and the best prompt for Phase 27.
```
