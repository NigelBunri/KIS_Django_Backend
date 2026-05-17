# KIS Profitability 80%+ Roadmap - Phase 06 Education Revenue Engine

Date: 2026-05-16

Status: Completed as a safe locked-preview implementation. No live charges, hard gates, subscriptions, payment provider calls, course commissions, certificate fees, instructor payouts, or paid course restrictions were enabled.

## Phase Objective

Prepare education monetization for instructors, education institutions, courses, modules, certificates, cohorts, enrollments, and buyer-facing learning surfaces without changing existing free education behavior.

This phase uses the disabled Phase 02 pricing catalog and keeps the financial redesign intact:

- education payments remain USD/direct-provider first;
- Instructor Pro, Education Institution Pro, and Promotion Packages are visible but not live;
- course commission visibility is shown as a future reporting concept only;
- certificate processing fees are preview-only;
- paid course/cohort readiness does not block current free course creation or enrollment behavior;
- KIS promotional credits remain non-cash reward/subsidy credits only.

## Frontend Changes

Added `Instructor Pro` to the disabled frontend pricing catalog:

- `/Users/nigel/dev/KIS/src/services/profitabilityPricing.ts`

Added reusable education revenue preview component:

- `/Users/nigel/dev/KIS/src/components/profitability/EducationRevenuePreviewCard.tsx`

Wired preview states into:

- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/EducationManagementModal.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/EducationV2DiscoverPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/components/EducationEnrollmentSheet.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/components/EducationDetailSheet.tsx`

## Preview Areas

| Area | Preview State | Purpose |
|---|---|---|
| Education institution dashboard | Education revenue engine preview | Shows Instructor Pro, paid course readiness, certificate processing, course commissions, analytics, and promotion entry points. |
| Program/course detail workspace | Course revenue preview | Shows commission, certificate, cohort, and instructor analytics readiness while preserving course management. |
| Education discovery page | Education payment preview | Gives learner-facing USD/direct-provider-first payment expectations. |
| Enrollment sheet | Paid learning preview | Explains that course commissions, certificate processing, and instructor payouts are not live. |
| Learner detail payment area | Enrollment revenue preview | Shows future fee/refund/payout/access-state visibility without changing enrollment behavior. |
| Certificate area | Certificate processing preview | Shows certificate verification/sharing/processing-fee readiness without charging learners. |

## Locked-But-Visible Rules

Every education revenue preview uses:

- disabled Instructor Pro, Education Institution Pro, and Promotion Packages price labels;
- "NOT LIVE" badge;
- explicit copy that current free education behavior remains available;
- USD/direct-provider-first payment copy;
- promotional-credit safety copy;
- commission/certificate/payout visibility as reporting previews only.

No component opens plan checkout, changes entitlements, hides course tools, starts provider calls, creates course fees, charges certificate fees, or changes enrollment access rules.

## Education Safety Position

- KIS promotional credits are not cash, not transferable, not withdrawable, and not exchange-rated.
- New paid education payment copy remains USD/direct-provider first.
- Course promotion is labelled as future reviewed/sponsored placement, not automatic ad buying.
- Course commission and certificate-fee visibility is shown as future reporting, not active deduction or charge collection.
- Existing education institution, course, module, certificate, enrollment, and learner-detail behavior is preserved.

## Validation

Focused validation command:

```text
cd /Users/nigel/dev/KIS && npx eslint src/components/profitability/EducationRevenuePreviewCard.tsx src/services/profitabilityPricing.ts src/screens/tabs/profile-screen/EducationManagementModal.tsx src/screens/broadcast/education/EducationV2DiscoverPage.tsx src/screens/broadcast/education/components/EducationEnrollmentSheet.tsx src/screens/broadcast/education/components/EducationDetailSheet.tsx --quiet
```

Result: passed.

## Remaining Risks

- This phase is preview-only. It does not implement paid subscriptions, entitlements, course commissions, instructor payouts, certificate fees, taxes, refunds, invoices, promotion checkout, or learner support workflows.
- Course commission percentages and certificate processing fees need product, legal, accounting, provider-cost, and market review before launch.
- Paid course access must be reconciled with direct Flutterwave payment state and refund rules before activation.
- Certificate monetization must not undermine free learning access, verification trust, or child/youth protections.
- Backend feature flags and entitlement enforcement are still intentionally deferred.

## Best Prompt For Phase 07

```text
Please implement Phase 07 of the KIS Profitability 80%+ Roadmap without using git commands. Focus on Health Revenue Engine. Using the disabled pricing catalog and previous monetization previews, add safe locked-but-visible Health Provider Pro/Health Institution Growth states across health provider dashboards, appointment/session/service detail, care-plan, billing, and patient-facing booking surfaces. Prepare provider analytics previews, USD-only payment copy, direct-provider payment state visibility, appointment/service fee visibility, care-plan premium readiness, and promotion entry points without enabling live charges or hard-blocking current free health behavior. Avoid medical-diagnosis claims, keep KIS promotional credits non-cash, non-transferable, non-withdrawable, and not exchange-rated, preserve existing health APIs/UI behavior, run focused validation, update docs/profitability-roadmap/phase-07-health-revenue-engine.md and docs/BUILD_STATE.md with risks, validation, and the best prompt for Phase 08.
```
