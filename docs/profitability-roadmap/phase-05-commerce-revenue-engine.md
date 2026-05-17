# KIS Profitability 80%+ Roadmap - Phase 05 Commerce Revenue Engine

Date: 2026-05-16

Status: Completed as a safe locked-preview implementation. No live charges, hard gates, subscriptions, payment provider calls, transaction fees, seller restrictions, or promotion purchases were enabled.

## Phase Objective

Prepare commerce monetization for shops, products, services, marketplace discovery, and buyer-facing product detail pages without changing existing free seller behavior.

This phase uses the disabled Phase 02 pricing catalog and keeps the financial redesign intact:

- commerce remains USD/direct-provider first;
- Seller Pro and Promotion Packages are visible but not live;
- transaction-fee visibility is shown as a future reporting concept only;
- featured listings are preview-only and require review before launch;
- KIS promotional credits remain non-cash reward/subsidy credits only.

## Frontend Changes

Added reusable commerce revenue preview component:

- `/Users/nigel/dev/KIS/src/components/profitability/CommerceRevenuePreviewCard.tsx`

Wired preview states into:

- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/MarketManagementModal.tsx`
- `/Users/nigel/dev/KIS/src/screens/market/ShopDashboardScreen.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/pages/BroadcastMarketPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/market/ProductDetailsPage.tsx`

## Preview Areas

| Area | Preview State | Purpose |
|---|---|---|
| Market management | Seller revenue preview | Shows Seller Pro, featured listings, seller analytics, fee reporting, and promotion readiness. |
| Shop dashboard | Seller Pro revenue preview | Places commerce monetization next to seller KPIs without blocking products/services. |
| Broadcast market page | Marketplace growth preview | Shows buyer-safe promotion language while keeping checkout USD/direct-provider first. |
| Product detail | Seller promotion preview | Shows featured product, seller trust, and USD payment-state readiness without changing checkout. |

## Locked-But-Visible Rules

Every commerce revenue preview uses:

- disabled Seller Pro and Promotion Packages price labels;
- "NOT LIVE" badge;
- explicit copy that current free seller behavior remains available;
- USD/direct-provider-first payment copy;
- promotional-credit safety copy;
- transaction-fee visibility as a reporting preview only.

No component opens checkout, changes plan state, hides seller tools, starts provider calls, or creates promotion purchases.

## Commerce Safety Position

- KIS promotional credits are not cash, not transferable, not withdrawable, and not exchange-rated.
- New commerce payment copy remains USD/direct-provider first.
- Featured placement is labelled as future reviewed/sponsored placement, not automatic ad buying.
- Transaction-fee visibility is shown as future seller reporting, not active fee deduction.
- Existing marketplace/shop/product/service creation and management behavior is preserved.

## Validation

Focused validation command:

```text
cd /Users/nigel/dev/KIS && npx eslint src/components/profitability/CommerceRevenuePreviewCard.tsx src/screens/tabs/profile-screen/MarketManagementModal.tsx src/screens/broadcast/pages/BroadcastMarketPage.tsx src/screens/broadcast/market/ProductDetailsPage.tsx src/screens/market/ShopDashboardScreen.tsx src/services/profitabilityPricing.ts --quiet
```

Result: passed.

## Remaining Risks

- This phase is preview-only. It does not implement paid seller subscriptions, entitlements, transaction-fee deduction, payouts, tax handling, promotion checkout, ad ranking, invoices, refunds, or support workflows.
- Seller Growth is represented through Seller Pro plus Promotion Packages; a dedicated `seller_growth` catalog item can be added later if product approves a separate tier.
- Featured placement needs moderation, sponsored labels, child/youth-safe filtering, seller trust checks, and legal review before launch.
- Fee visibility must be reconciled with Flutterwave fees, KIS platform fees, refund rules, and seller payout timing before activation.
- Backend feature flags and entitlement enforcement are still intentionally deferred.

## Best Prompt For Phase 06

```text
Please implement Phase 06 of the KIS Profitability 80%+ Roadmap without using git commands. Focus on Education Revenue Engine. Using the disabled pricing catalog and previous monetization previews, add safe locked-but-visible Instructor Pro/Education Institution Pro states across education institution/course/module/certificate/cohort surfaces. Prepare course commission visibility, certificate fee preview, paid course readiness, instructor analytics previews, USD-only payment copy, and promotion entry points without enabling live charges or hard-blocking current free education behavior. Keep KIS promotional credits non-cash, non-transferable, non-withdrawable, and not exchange-rated. Preserve existing education APIs/UI behavior, run focused validation, update docs/profitability-roadmap/phase-06-education-revenue-engine.md and docs/BUILD_STATE.md with risks, validation, and the best prompt for Phase 07.
```
