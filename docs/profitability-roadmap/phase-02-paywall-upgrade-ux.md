# KIS Profitability 80%+ Roadmap - Phase 02 Paywall And Upgrade UX

Date: 2026-05-16

Status: Completed as a safe paywall/upgrade UX foundation. No live charges, paywalls, subscriptions, payment provider calls, or hard gates were enabled.

## Phase Objective

Design and prepare KIS upgrade surfaces so users can understand paid value before charges are enabled. The system must show premium value without breaking current free flows or accidentally making promotional credits look like money.

Phase 02 intentionally uses a locked-but-visible strategy:

- show premium features where they naturally belong;
- explain the value clearly;
- avoid blocking existing free behavior;
- avoid live charges;
- avoid cash-like promotional-credit language;
- prepare upgrade copy, plan comparison, usage meters, and enterprise contact flows for later implementation.

## Safe Frontend Foundation Added

Added:

- `/Users/nigel/dev/KIS/src/services/profitabilityPricing.ts`

This file contains:

- disabled-by-default pricing plan catalog;
- Consumer Plus and Family Plus plan copy;
- Creator Pro and Creator Growth plan copy;
- Institution Starter/Growth plan copy;
- Partner Workspace Pro plan copy;
- Seller Pro plan copy;
- Education Institution Pro plan copy;
- Health Provider Pro plan copy;
- Verification Processing copy;
- Promotion Packages copy;
- Enterprise contact copy;
- safe locked premium-state copy helper;
- promotional-credit legal safety copy.

Important:

- All catalog entries are `enabled: false`.
- `KIS_PROFITABILITY_PRICING_ENABLED` is `false`.
- Nothing imports this file into live screens yet.
- No payment, entitlement, or provider behavior was enabled.

## Paywall UX Principles

1. Do not interrupt basic spiritual/community flows.
2. Do not block basic messaging, Bible reading, public content viewing, profile viewing, or ordinary community participation.
3. Place upgrade prompts at moments of high intent.
4. Make premium features visible but clearly marked as not yet enabled until Phase 03+.
5. Use soft CTAs:
   - "View plan"
   - "Unlock when available"
   - "Contact KIS"
   - "Start verification"
   - "Promote when available"
6. Do not use fear or pressure.
7. Do not make promotional credits appear cash-like.
8. For health, avoid medical-diagnosis claims.
9. For child/youth/family controls, keep safety-first defaults even for free users.

## Upgrade Surface Map

| Area | Surface | Prompt Type | Target Plan |
|---|---|---|---|
| Profile | Existing upgrade sheet/profile overview | Plan comparison, Consumer/Family Plus | Consumer Plus, Family Plus |
| Broadcast Channels | Channel Studio, analytics, scheduling, live, embeds | Locked-but-visible studio modules | Creator Pro/Growth |
| Feed/Channel Creation | Channel count, scheduled post, paid content fields | Usage meter and locked field label | Creator Pro/Growth |
| Channel Detail | Membership, paid live, advanced analytics | Creator upgrade card | Creator Growth |
| Partners | Workspace settings, roles, subrooms, events, moderation | Workspace upgrade prompt | Partner Workspace Pro/Network |
| Commerce | Shop dashboard, product/service limits, featured listing | Seller upgrade prompt | Seller Pro/Growth |
| Education | Course manager, certificates, cohorts, instructor analytics | Education upgrade prompt | Instructor Pro, Education Institution Pro |
| Health | Institution dashboard, service catalog, booking reminders, analytics | Provider upgrade prompt | Health Provider Pro/Growth |
| Verification | Institution verification center | Processing fee disclosure | Verification Processing |
| Notifications | Campaign/broadcast reach analytics | Promotion prompt | Promotion Packages |
| Public Web / Embeds | Embed analytics, custom public page, SEO cards | Creator/institution upgrade prompt | Creator Growth, Institution Growth |
| AI | Bible study helper, course tutor, creator drafts, admin insight | Disabled AI add-on prompt | Future AI Add-on |
| Enterprise | Partner/institution profile, KCAN network pages | Contact flow | Enterprise |

## Locked-But-Visible Premium States

Each locked premium feature should show:

- feature title;
- target plan badge;
- one-line value statement;
- one safe CTA;
- current availability state;
- no active payment unless pricing is enabled;
- promotional-credit safety copy when credits are mentioned.

Example copy:

```text
Advanced Channel Analytics
Creator Growth
See subscriber trends, watch history, content performance, and promotion impact.
Pricing is not live yet. This feature will be available after launch approval.
```

## Usage Meter Rules

Usage meters should be friendly, not punitive.

Recommended usage meters:

| Resource | Free Limit | Paid Limit |
|---|---:|---:|
| Creator channels | 1 | 3-10+ |
| Scheduled posts | 0 or limited | Enabled |
| Institution staff seats | 1-2 | 3-25+ |
| Partner subrooms/channels | Limited | 10+ |
| Shop active listings | Limited | Expanded |
| Courses | Limited/free-only | Paid courses/certificates |
| Health services | Limited | Full catalog |
| Promotion campaigns | Unavailable | Package-based |
| Public embed analytics | Basic | Advanced |

Usage meter copy:

```text
You are using 1 of 1 free channels.
Creator Pro will allow up to 3 channels after pricing is enabled.
```

## Plan Comparison Copy

The plan comparison screen should group pricing by audience, not show every plan at once.

Audience tabs:

- Personal and Family;
- Creators and Channels;
- Institutions;
- Shops and Sellers;
- Education;
- Health;
- Partners;
- Verification and Promotion;
- Enterprise.

Each plan card should show:

- price;
- best-for label;
- top 3 benefits;
- current availability;
- safe CTA.

CTA states:

- `View details` when pricing is disabled.
- `Contact KIS` for Enterprise.
- `Start verification` only when verification fee flow is approved.
- `Upgrade` only after live pricing, payment, refunds, and entitlement checks are ready.

## Promotional Credit Copy Guard

Approved language:

- "Promotional credits"
- "Reward credits"
- "Subsidy credits"
- "Can reduce eligible upgrade cost"
- "Not cash"
- "Not transferable"
- "Not withdrawable"
- "Not exchange-rated"

Forbidden language:

- "coin balance value"
- "cash out"
- "withdraw"
- "convert to USD"
- "exchange rate"
- "buy KIS Coins"
- "send credits to another user"
- "wallet money"

## Screen-Specific Design Notes

### Profile

Use the existing upgrade sheet as the central personal upgrade entry point.

Add later:

- audience-grouped plan tabs;
- Consumer Plus and Family Plus cards;
- non-cash promotional-credit explanation;
- usage and trial state.

### Broadcast / Channels

Premium states should appear inside Channel Studio:

- analytics panel;
- scheduled publish;
- live streaming;
- embeds;
- paid content;
- promotion packages.

The user should see what they are missing, but creation of basic free channel/content must continue working.

### Commerce

Seller Pro prompts should appear:

- when active listing limits are reached;
- when seller opens analytics;
- when seller opens featured listing;
- when seller wants product video/live selling tools.

### Education

Education upgrade prompts should appear:

- when publishing paid courses;
- when issuing certificates;
- when using cohorts;
- when viewing learner analytics.

### Health

Health Provider Pro prompts should appear:

- when using provider dashboard analytics;
- when adding many services;
- when using booking reminder automation;
- when requesting verified provider promotion.

Health copy must avoid diagnosis or treatment guarantees.

### Partners

Partner Workspace Pro prompts should appear:

- when roles/subrooms exceed free limits;
- when moderation/audit tools are opened;
- when event monetization is opened;
- when partner analytics is opened.

### Verification

Verification fee surfaces should not collect payment until provider cost, refund policy, and staff review workflow are approved.

Copy should say:

```text
Processing fees help cover provider checks and staff review. Raw documents remain private and are never shown publicly.
```

### Promotion

Promotion cards should always show:

- sponsored/featured label requirement;
- Christian-safe policy;
- staff review requirement;
- child/youth-safe filtering.

## Phase 02 Validation

- Added frontend pricing catalog foundation with all pricing disabled.
- No live charges enabled.
- No existing screens were gated.
- No promotional-credit cash-like behavior added.
- No provider calls added.
- No backend migrations added.

Focused validation command:

```text
cd /Users/nigel/dev/KIS && npx eslint src/services/profitabilityPricing.ts --quiet
```

Result: passed.

## Remaining Risks

- UX surfaces are not wired into screens yet.
- Backend feature flags and entitlement checks are not implemented yet.
- Payment provider flows are not connected to these plan definitions.
- Exact pricing still needs product, legal, accounting, provider-cost, and market review.
- Future wiring must not block existing free behavior until the pricing launch gate is approved.

## Best Prompt For Phase 03

```text
Please implement Phase 03 of the KIS Profitability 80%+ Roadmap without using git commands. Focus on Creator And Channel Monetization. Using the disabled pricing catalog from Phase 02, add safe locked-but-visible Creator Pro/Growth states in the Channel Studio and Broadcast/Channels UX for channel limits, scheduled posts, analytics, embeds, live placeholders, paid content readiness, and promotion entry points. Do not enable live charges or hard-block existing free channel/feed behavior. Keep KIS promotional credits non-cash, non-transferable, non-withdrawable, and not exchange-rated. Preserve legacy broadcast APIs/UI behavior, run focused frontend validation, update docs/profitability-roadmap/phase-03-creator-channel-monetization.md and docs/BUILD_STATE.md with risks, validation, and the best prompt for Phase 04.
```
