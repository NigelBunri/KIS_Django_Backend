# KIS Profitability 80%+ Roadmap - Phase 16 Billing Provider Sandbox Readiness And Subscription Lifecycle Planning

Date: 2026-05-16

Status: Completed as a safe sandbox-readiness and lifecycle-planning foundation. No live charges, production provider connections, entitlement enforcement, payment instrument collection, subscriptions, trials, invoices, refunds, or billing actions were enabled.

## Phase Objective

Prepare KIS for future billing activation by documenting and exposing the safest subscription lifecycle and provider sandbox readiness model before any live monetization is allowed.

This phase builds on:

- disabled pricing catalog;
- entitlement metadata;
- usage meters;
- profitability command center;
- pricing launch gate;
- USD-only financial redesign;
- KIS promotional-credit safety model.

## Backend Changes

Added backend subscription lifecycle readiness summary:

- `apps/billing/profitability_subscription_lifecycle.py`

Added authenticated read-only endpoint:

- `GET /api/v1/billing/profitability-subscription-lifecycle/`

Updated:

- `apps/billing/views.py`
- `apps/billing/urls.py`
- `apps/billing/tests.py`

### Backend Summary Includes

- subscription lifecycle states:
  - trial readiness;
  - active subscription;
  - grace period;
  - cancellation;
  - refunded;
- Flutterwave/provider sandbox checks:
  - sandbox credentials;
  - payment link generation;
  - webhook signature verification;
  - reconciliation;
- one-time billing readiness:
  - promotion campaign billing;
  - verification processing fee;
  - enterprise annual contracts;
- invoice/receipt readiness;
- refund/cancellation/grace/trial control requirements;
- support escalation queues;
- launch-gate linkage;
- entitlement catalog snapshot;
- explicit guardrails proving no live billing is enabled.

## Frontend Changes

Added subscription lifecycle readiness service:

- `/Users/nigel/dev/KIS/src/services/profitabilitySubscriptionLifecycleService.ts`

Added dashboard card:

- `/Users/nigel/dev/KIS/src/components/dashboard/ProfitabilitySubscriptionLifecycleCard.tsx`

Updated billing route map:

- `/Users/nigel/dev/KIS/src/network/routes/billingRoutes.ts`

Wired the card into:

- `/Users/nigel/dev/KIS/src/screens/tabs/ProfileScreen.tsx`

## Required Future Runbooks

Before any monetization goes live, create and validate these runbooks with evidence:

### Subscription Lifecycle Runbook

- create trial;
- trial reminder;
- trial cancellation;
- activate subscription after provider confirmation;
- renewal visibility;
- failed payment;
- grace period;
- downgrade without destroying user content;
- cancel at period end;
- immediate cancellation rules;
- reactivation;
- refund and entitlement rollback.

### Flutterwave Sandbox Runbook

- create sandbox subscription-style payment link;
- create sandbox one-time verification fee payment;
- create sandbox promotion fee payment;
- verify signed success callback;
- verify signed failed callback;
- verify cancelled payment behavior;
- verify duplicate callback idempotency;
- verify unmatched callback quarantine;
- verify provider dashboard callback URL.

### Invoice And Receipt Runbook

- USD-only invoice template;
- receipt numbering policy;
- tax display policy;
- payment status display;
- refund receipt;
- customer download path;
- staff audit path.

### Support Escalation Runbook

- failed-payment support queue;
- refund request queue;
- cancellation assistance;
- verification fee dispute;
- promotion campaign billing issue;
- enterprise contract support;
- response time and escalation owners.

## Safety Guardrails

This phase intentionally keeps:

- live charges disabled;
- production payment provider connection disabled;
- payment instrument collection disabled;
- entitlement enforcement disabled;
- KIS promotional credits non-cash, non-transferable, non-withdrawable, and not exchange-rated;
- USD/direct-provider-first billing model intact.

## Validation

Backend compile:

```text
cd /Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis && python3 -m py_compile apps/billing/profitability_subscription_lifecycle.py apps/billing/views.py apps/billing/urls.py apps/billing/tests.py
```

Result: passed.

Django check:

```text
cd /Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis && python3 manage.py check
```

Result: passed.

Focused backend test:

```text
cd /Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis && python3 manage.py test apps.billing.tests.BillingWalletFlowTests.test_profitability_subscription_lifecycle_is_sandbox_readiness_only --keepdb --noinput
```

Result: passed.

Focused frontend lint:

```text
cd /Users/nigel/dev/KIS && npx eslint src/components/dashboard/ProfitabilitySubscriptionLifecycleCard.tsx src/services/profitabilitySubscriptionLifecycleService.ts src/network/routes/billingRoutes.ts src/screens/tabs/ProfileScreen.tsx --quiet
```

Result: passed.

## Remaining Risks

- This phase is still planning/readiness only. No actual subscription state machine, invoice generator, refund processor, provider subscription adapter, or support queue workflow was activated.
- Flutterwave subscription-style behavior still needs staging proof and legal/finance approval.
- Tax handling, country-specific receipt rules, and accounting policy are not implemented.
- Entitlement downgrade and grace-period behavior must be carefully designed before enforcement.
- Enterprise annual contracts still require manual legal/commercial process design.

## Best Prompt For Phase 17

```text
Please implement Phase 17 of the KIS Profitability 80%+ Roadmap without using git commands. Focus on Revenue Operations Admin Evidence Console. Build on the pricing launch gate and billing sandbox lifecycle readiness to add safe staff-only/read-only evidence tracking foundations for legal review, pastoral/child-safety review, tax/accounting review, Flutterwave sandbox proof, invoice/receipt proof, refund/support proof, entitlement grace policy, promotion sponsored-label policy, verification fee policy, enterprise contract policy, privacy analytics policy, and rollback proof. Do not enable live charges, do not connect production payment providers, do not enforce entitlements, do not collect payment instruments, and do not expose private health/payment/verification data. Preserve existing APIs/UI behavior, run focused validation, update docs/profitability-roadmap/phase-17-revenue-operations-evidence-console.md and docs/BUILD_STATE.md with risks, validation, and the best prompt for Phase 18.
```
