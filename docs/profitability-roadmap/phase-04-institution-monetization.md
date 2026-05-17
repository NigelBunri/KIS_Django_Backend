# KIS Profitability 80%+ Roadmap - Phase 04 Institution Monetization

Date: 2026-05-16

Status: Completed as a safe locked-preview implementation. No live charges, hard gates, subscriptions, payment provider calls, or free institution restrictions were enabled.

## Phase Objective

Prepare institution monetization across shops, education institutions, health providers, and partner/ministry workspaces without changing current free behavior.

The implementation follows the Phase 02 disabled pricing catalog:

- show Institution/Seller/Education/Health/Partner paid-plan value in context;
- mark all paid-plan messaging as preview-only;
- preserve existing management surfaces;
- avoid blocking existing creation, dashboards, landing pages, verification, or management actions;
- keep KIS promotional credits legally safe.

## Frontend Changes

Added reusable component:

- `/Users/nigel/dev/KIS/src/components/profitability/InstitutionMonetizationPreviewCard.tsx`

Wired into:

- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/MarketManagementModal.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/HealthManagementModal.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/EducationManagementModal.tsx`
- `/Users/nigel/dev/KIS/src/components/partners/PartnersCenterPane.tsx`

## Preview Areas

| Area | Preview Plan Path | Preview Features |
|---|---|---|
| Shops / Market | Seller Pro + Institution Growth | Seller analytics, featured listings, trust signals |
| Education | Education Institution Pro + Institution Growth | Learner analytics, institution growth, course/event broadcast reach |
| Health | Health Provider Pro + Institution Growth | Provider dashboard, verified care profile, booking/reminder/service growth |
| Partners / Ministries | Partner Workspace Pro + Institution Growth | Workspace roles, community analytics, partner network growth |

## Locked-But-Visible Rules

Every monetization preview card includes:

- plan price labels from disabled pricing catalog;
- "NOT LIVE" badge;
- value features;
- explanation that existing free behavior remains available;
- promotional-credit safety copy.

No CTA starts checkout or changes entitlements.

## Safety And Legal Position

- KIS promotional credits remain non-cash, non-transferable, non-withdrawable, and not exchange-rated.
- Health monetization copy focuses on booking, trust, reminders, and operations, not diagnosis.
- Promotions are described as reviewed/sponsored/Christian-safe, not automatic ad buying.
- Verification remains private-media-reference based and does not expose documents.
- No staff seat limits or paid restrictions are enforced yet.

## Validation

Focused validation command:

```text
cd /Users/nigel/dev/KIS && npx eslint src/components/profitability/InstitutionMonetizationPreviewCard.tsx src/screens/tabs/profile-screen/MarketManagementModal.tsx src/screens/tabs/profile-screen/HealthManagementModal.tsx src/screens/tabs/profile-screen/EducationManagementModal.tsx src/components/partners/PartnersCenterPane.tsx src/services/profitabilityPricing.ts --quiet
```

Result: passed.

## Remaining Risks

- This phase is preview-only. It does not implement subscriptions, entitlements, payment provider flows, usage enforcement, receipts, refunds, or support workflows.
- Backend pricing feature flags remain documented but not runtime-enforced.
- Future paid staff-seat limits must be designed carefully so current institution owners do not lose access.
- Institution pricing needs product, legal, accounting, and market review before launch.
- Promotion eligibility needs moderation and sponsored-label enforcement before any paid campaigns go live.

## Best Prompt For Phase 05

```text
Please implement Phase 05 of the KIS Profitability 80%+ Roadmap without using git commands. Focus on Commerce Revenue Engine. Using the disabled pricing catalog and institution monetization previews, add safe locked-but-visible Seller Pro/Growth and promotion states across marketplace/shop/product/service management and buyer-facing commerce where appropriate. Prepare transaction-fee visibility, featured listing previews, seller analytics previews, USD-only payment copy, and promotion package entry points without enabling live charges or hard-blocking current free seller behavior. Keep KIS promotional credits non-cash, non-transferable, non-withdrawable, and not exchange-rated. Preserve existing commerce APIs/UI behavior, run focused validation, update docs/profitability-roadmap/phase-05-commerce-revenue-engine.md and docs/BUILD_STATE.md with risks, validation, and the best prompt for Phase 06.
```
