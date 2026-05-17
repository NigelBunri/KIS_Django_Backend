# KIS Profitability 80%+ Roadmap - Phase 09 Verification, Trust, And Promotion Revenue Engine

Date: 2026-05-16

Status: Completed as a safe locked-preview implementation. No live charges, hard gates, payment provider calls, verification fee collection, badge renewal billing, trust boost purchase, campaign checkout, or promotion delivery was enabled.

## Phase Objective

Prepare verification, trust, badge renewal, trust boost, and promotion revenue paths across user/profile verification, shops, partners, health institutions, education institutions, creator/channel trust surfaces, and promotion entry points without changing existing verification or promotion behavior.

This phase keeps the financial redesign intact:

- all new trust and promotion fee copy is USD/direct-provider first;
- KIS promotional credits remain reward/subsidy credits only;
- verification processing, badge renewal, trust boost, and promotion package states are visible but not chargeable;
- provider/manual-review cost visibility is preview-only;
- sponsored labels and campaign review states are introduced as readiness copy only;
- existing verification, badge display, profile, shop, partner, health, education, and channel behavior remains available.

## Frontend Changes

Added reusable trust and promotion revenue preview component:

- `/Users/nigel/dev/KIS/src/components/profitability/TrustPromotionRevenuePreviewCard.tsx`

Wired preview states into:

- `/Users/nigel/dev/KIS/src/screens/tabs/ProfileScreen.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/MarketManagementModal.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/HealthManagementModal.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/EducationManagementModal.tsx`
- `/Users/nigel/dev/KIS/src/components/partners/PartnersCenterPane.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/channels/studio/ChannelStudioScreen.tsx`

## Preview Areas

| Area | Preview State | Purpose |
|---|---|---|
| Profile overview | Profile verification and trust preview | Shows identity review fee visibility, badge renewal, and trusted profile visibility as preview-only. |
| Shop management | Shop verification and promotion preview | Shows seller trust review, sponsored seller readiness, and renewal reminders. |
| Partner center | Partner verification and trust preview | Shows KYB review, official partner badges, enterprise evidence, and renewals. |
| Health management | Health verification and trust preview | Shows license review, provider trust, and renewal safety without medical-diagnosis claims. |
| Education management | Education verification and trust preview | Shows accreditation review, certificate trust, renewal, and sponsored learning readiness. |
| Channel Studio dashboard | Channel trust and promotion preview | Shows creator/channel trust badges, sponsored labels, and campaign review state readiness. |
| Channel Studio settings | Promotion campaign preview | Shows campaign packages, sponsored labels, and review states as planning-only. |

## Locked-But-Visible Rules

Every trust and promotion preview uses:

- disabled `Verification Processing`, `Promotion Packages`, and `Enterprise` pricing labels;
- explicit `NOT LIVE` badge;
- USD/direct-provider-first fee copy;
- provider/manual-review cost visibility as preview-only;
- badge renewal reminders as preview-only;
- sponsored-label and campaign review states as preview-only;
- promotional-credit safety copy.

No component opens checkout, creates an invoice, starts a Flutterwave/provider flow, changes verification approval, changes badge issuance/revocation, gates existing verification flows, blocks current free behavior, or promotes content automatically.

## Safety Position

- KIS promotional credits are not cash, not transferable, not withdrawable, and not exchange-rated.
- Trust revenue copy avoids suggesting that badges can be bought; verification and badge renewal remain review-based.
- Promotion copy requires sponsored labels, Christian-safe campaign review, child/youth-safe filtering, and moderation before launch.
- Health trust copy avoids medical-diagnosis claims.
- Existing verification and promotion behavior is preserved.

## Validation

Focused validation command:

```text
cd /Users/nigel/dev/KIS && npx eslint src/components/profitability/TrustPromotionRevenuePreviewCard.tsx src/screens/tabs/ProfileScreen.tsx src/screens/tabs/profile-screen/MarketManagementModal.tsx src/screens/tabs/profile-screen/HealthManagementModal.tsx src/screens/tabs/profile-screen/EducationManagementModal.tsx src/components/partners/PartnersCenterPane.tsx src/screens/broadcast/channels/studio/ChannelStudioScreen.tsx src/services/profitabilityPricing.ts --quiet
```

Result: passed.

## Remaining Risks

- This phase is preview-only. It does not implement real verification fee checkout, badge renewal billing, trust boost entitlements, promotion campaign creation, invoices, refunds, tax handling, provider settlement, or admin billing dashboards.
- Legal/product review is required before any trust boost can affect ranking so KIS does not appear to sell trust or verification.
- Sponsored labels, moderation review, child/youth filtering, campaign rejection reasons, and audit trails must be complete before paid promotion launch.
- Badge renewal billing must never automatically imply badge approval; renewal should cover review processing only.
- Backend entitlement and campaign state enforcement remains intentionally deferred.

## Best Prompt For Phase 10

```text
Please implement Phase 10 of the KIS Profitability 80%+ Roadmap without using git commands. Focus on Consumer Plus, Family Plus, And Bible/Spiritual Growth Revenue Engine. Using the disabled pricing catalog and previous monetization previews, add safe locked-but-visible Consumer Plus and Family Plus states across profile, Bible reading, daily meditations, reading plans, prayer groups, family/child-safe modes, saved content, reminders, and spiritual journey surfaces. Prepare USD-only upgrade copy, trial readiness, family-safe premium value, usage meters, and locked-but-visible spiritual growth prompts without enabling live charges or hard-blocking current free Bible/profile behavior. Keep KIS promotional credits non-cash, non-transferable, non-withdrawable, and not exchange-rated, preserve existing APIs/UI behavior, run focused validation, update docs/profitability-roadmap/phase-10-consumer-family-spiritual-growth-revenue-engine.md and docs/BUILD_STATE.md with risks, validation, and the best prompt for Phase 11.
```
