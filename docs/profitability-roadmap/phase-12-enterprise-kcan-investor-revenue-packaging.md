# KIS Profitability 80%+ Roadmap - Phase 12 Enterprise, KCAN, And Investor-Ready Revenue Packaging

Date: 2026-05-16

Status: Completed as a safe locked-preview implementation. No live charges, contracts, investment offers, lead capture, enterprise checkout, annual billing, sales workflow, or hard feature gates were enabled.

## Phase Objective

Prepare Enterprise, KCAN network, ministry/organization, regional chapter, school/clinic/shop network, and partner ecosystem revenue packaging across high-context leadership surfaces without changing existing free behavior.

This phase keeps the financial redesign and legal posture intact:

- enterprise/KCAN packaging remains annual-contract or approved USD/direct-provider first;
- `Enterprise`, `Institution Growth`, and `Partner Workspace Pro` plans are visible but not live;
- enterprise contact readiness, multi-branch/member-seat value, verified network trust, implementation/support tiers, launch evidence, and investor-facing revenue narrative are preview-only;
- no investment solicitation, lead capture spam, contract creation, or enterprise checkout is introduced;
- KIS promotional credits remain non-cash reward/subsidy credits only.

## Frontend Changes

Added reusable enterprise/KCAN revenue packaging preview component:

- `/Users/nigel/dev/KIS/src/components/profitability/EnterpriseKcanRevenuePreviewCard.tsx`

Wired preview states into:

- `/Users/nigel/dev/KIS/src/screens/tabs/ProfileScreen.tsx`
- `/Users/nigel/dev/KIS/src/components/partners/PartnersCenterPane.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/studio/ChannelStudioScreen.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/MarketManagementModal.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/EducationManagementModal.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/HealthManagementModal.tsx`
- `/Users/nigel/dev/KIS/src/components/broadcast/KcanVisionModal.tsx`

## Preview Areas

| Area | Preview State | Purpose |
|---|---|---|
| Profile overview | KCAN enterprise packaging preview | Shows KIS/KCAN investor-ready revenue narrative, enterprise contact readiness, and launch evidence value. |
| Partner center | Partner enterprise packaging preview | Shows regional chapters, member seats, branch operations, support tiers, and verified network trust. |
| Channel Studio | Channel network enterprise preview | Shows creator, ministry, education, and institutional media networks as future annual-contract value. |
| Shop management | Commerce network packaging preview | Shows verified seller networks, regional commerce chapters, support tiers, and operational evidence. |
| Education management | Education network packaging preview | Shows school networks, cohorts, certificate trust, implementation support, and launch evidence. |
| Health management | Health network packaging preview | Shows clinic networks, provider roles, care workflow support, and safety evidence. |
| KCAN vision page | KCAN enterprise and investor packaging preview | Connects vision, lawful structure, implementation tiers, verified trust, and investor-readiness. |

## Locked-But-Visible Rules

Every enterprise/KCAN preview uses:

- disabled `Enterprise`, `Institution Growth`, and `Partner Workspace Pro` pricing labels;
- explicit `NOT LIVE` badge;
- annual-contract readiness copy;
- multi-branch/member-seat value copy as preview-only;
- verified network trust and launch evidence copy as preview-only;
- implementation/support tiers as preview-only;
- promotional-credit safety copy.

No component opens checkout, captures leads, creates a contract, creates an invoice, starts provider payment, offers securities/investment terms, changes institution limits, blocks existing free behavior, or changes any backend entitlement.

## Enterprise Safety Position

- KIS promotional credits are not cash, not transferable, not withdrawable, and not exchange-rated.
- Enterprise and KCAN copy is packaging/readiness copy, not an offer to sell securities or solicit investment.
- Investor-facing narrative must stay grounded in product, operations, trust, launch evidence, and lawful structures.
- Annual contracts require legal, tax, finance, support, procurement, data processing, and implementation review before launch.
- KCAN public vision remains lawful, non-sovereign, accountable, and institutionally clear.

## Validation

Focused validation command:

```text
cd /Users/nigel/dev/KIS && npx eslint src/components/profitability/EnterpriseKcanRevenuePreviewCard.tsx src/screens/tabs/ProfileScreen.tsx src/components/partners/PartnersCenterPane.tsx src/screens/broadcast/channels/studio/ChannelStudioScreen.tsx src/screens/tabs/profile-screen/MarketManagementModal.tsx src/screens/tabs/profile-screen/EducationManagementModal.tsx src/screens/tabs/profile-screen/HealthManagementModal.tsx src/components/broadcast/KcanVisionModal.tsx src/services/profitabilityPricing.ts --quiet
```

Result: passed.

## Remaining Risks

- This phase is preview-only. It does not implement enterprise contracts, lead routing, CRM, sales pipeline, invoices, annual billing, procurement flows, data processing agreements, refunds, tax handling, support SLAs, or implementation delivery.
- Investor-facing copy needs legal review before public use in fundraising or investor communications.
- Enterprise pricing and annual contracts need finance/legal approval and country-specific tax review.
- Multi-branch/member-seat packaging needs backend entitlement design before enforcement.
- KCAN network packaging must remain legally precise and avoid implying sovereignty, guaranteed returns, or unapproved investment products.

## Best Prompt For Phase 13

```text
Please implement Phase 13 of the KIS Profitability 80%+ Roadmap without using git commands. Focus on Billing Readiness, Entitlements, And Feature Flag Architecture. Build on the disabled pricing catalog and all locked-preview monetization surfaces to add safe backend/frontend foundations for plan identifiers, entitlement checks, usage meters, free-plan limits, trial-readiness metadata, billing status placeholders, and feature flags across Consumer Plus, Family Plus, Creator Pro/Growth, Institution Growth, Partner Workspace Pro, Seller Pro, Education Institution Pro, Health Provider/Growth, Verification Processing, Promotion Packages, and Enterprise. Do not enable live charges, do not hard-block existing free behavior, do not re-enable KIS promotional credits as money, and do not connect live payment providers. Preserve existing APIs/UI behavior, run focused Django/React Native validation where safe, update docs/profitability-roadmap/phase-13-billing-entitlements-feature-flags.md and docs/BUILD_STATE.md with risks, validation, and the best prompt for Phase 14.
```
