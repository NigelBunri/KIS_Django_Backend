# KIS Profitability 80%+ Roadmap - Phase 27 Final Close-Out

Date: 2026-05-17

Status: Completed. This closes the KIS Profitability 80%+ Roadmap.

## Final Decision

Normal users should not see beta, profitability, monetization-preview, revenue-preview, pricing-readiness, launch-readiness, or roadmap explanation cards.

Normal app flows should remain focused on working features and short actions:

- `Upgrade`;
- `Locked`;
- `Coming soon`;
- `Requires review`.

Detailed monetization/readiness information belongs only in:

- staff/admin revenue evidence pages;
- internal launch docs;
- operational checklists.

## Frontend Close-Out

Removed or neutralized normal-user visibility for profitability preview surfaces across:

- Profile;
- Bible;
- Broadcast/Channels;
- Partners;
- Commerce/Market;
- Education;
- Health.

The reusable profitability preview components now render no visible content, preventing roadmap cards from appearing in normal flows even where older screens still import them.

Updated Profile so normal users no longer see:

- profitability preview cards;
- monetization safety card;
- profitability command center;
- profitability launch gate;
- subscription lifecycle card;
- revenue operations evidence card;
- evidence workflow plan card;
- revenue evidence admin panel.

The staff/admin branch still exposes the relevant staff tools:

- `RevenueEvidenceAdminPanel`;
- `MonetizationSafetyCard`;
- `ProfitabilityCommandCenterCard`;
- `ProfitabilityLaunchGateCard`;
- `ProfitabilitySubscriptionLifecycleCard`.

## What Remains Disabled

The following remain disabled and must not be turned on without a separate explicit approval:

- live charges;
- production payment providers;
- entitlement enforcement;
- payment instrument collection;
- promotion checkout;
- enterprise lead capture;
- wallet/KISC money behavior;
- KIS promotional-credit purchase, transfer, withdrawal, or exchange behavior.

## What Can Be Enabled Later

Only after legal, pastoral/child-safety, tax/accounting, privacy, payment, support, rollback, and production sign-off:

- one limited beta cohort;
- one low-risk paid module;
- staging-proven Flutterwave payment flow;
- support-owned rollback plan;
- staff-reviewed upgrade copy.

## Validation

Frontend lint:

```text
cd /Users/nigel/dev/KIS && npx eslint src/screens/tabs/ProfileScreen.tsx src/components/profitability/CommerceRevenuePreviewCard.tsx src/components/profitability/ConsumerSpiritualRevenuePreviewCard.tsx src/components/profitability/EducationRevenuePreviewCard.tsx src/components/profitability/EnterpriseKcanRevenuePreviewCard.tsx src/components/profitability/HealthRevenuePreviewCard.tsx src/components/profitability/InstitutionMonetizationPreviewCard.tsx src/components/profitability/NotificationRetentionPreviewCard.tsx src/components/profitability/PartnerRevenuePreviewCard.tsx src/components/profitability/TrustPromotionRevenuePreviewCard.tsx src/components/profitability/CompactProfitabilityPreviewCard.tsx --quiet
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

## Remaining Risks

- Some old translation strings may still contain profitability wording, but the visible reusable preview components no longer render.
- Some screens still import profitability preview components for compatibility. They render `null` and can be removed gradually during normal cleanup.
- Staff/admin readiness cards should remain staff-only; future work should avoid placing them back in normal flows.

## Final Maintenance Prompt

```text
Please perform a light maintenance sweep without using git commands. Check that normal users do not see profitability, beta, monetization-preview, revenue-preview, pricing-readiness, or launch-readiness cards in Profile, Bible, Broadcast/Channels, Partners, Commerce/Market, Education, or Health. Keep staff/admin revenue evidence tools available only to staff/admin users. Do not enable live charges or entitlement enforcement. Run focused frontend lint and record any remaining visible copy issues in docs/BUILD_STATE.md.
```
