# KIS Profitability 80%+ Roadmap - Phase 01 Pricing Architecture

Date: 2026-05-16

Status: Completed as a pricing architecture blueprint. No live charges, paywalls, subscriptions, or provider calls were enabled.

## Pricing Principles

KIS should price growth, trust, management, distribution, and business value. It should not price basic Christian community participation too aggressively.

Core principles:

- Keep basic account, Bible access, messaging, public content viewing, and basic community participation free.
- Charge creators and institutions for tools that help them grow, manage, sell, verify, analyze, and reach people.
- Keep all paid plans in USD or approved local currency through direct payment providers.
- Keep KIS promotional credits legally safe:
  - not cash;
  - not transferable;
  - not withdrawable;
  - not exchange-rated;
  - not sold as money;
  - usable only as promotional/reward/subsidy credit where explicitly allowed.
- Use feature flags so paid gates can be staged gradually.
- Use annual plans to improve cash flow.
- Offer founder pricing for early institutions and creators, but avoid permanent underpricing.

## Global Feature Flags

Recommended disabled-by-default flags:

| Flag | Default | Purpose |
|---|---:|---|
| `KIS_PROFITABILITY_PRICING_ENABLED` | `False` | Master switch for paid pricing surfaces. |
| `KIS_CONSUMER_PLUS_ENABLED` | `False` | Enables Consumer Plus plan UI/gates. |
| `KIS_CREATOR_PRO_ENABLED` | `False` | Enables creator/channel paid plans. |
| `KIS_INSTITUTION_PLANS_ENABLED` | `False` | Enables institution subscription plans. |
| `KIS_PARTNER_WORKSPACE_PLANS_ENABLED` | `False` | Enables partner paid workspace plans. |
| `KIS_SELLER_PRO_ENABLED` | `False` | Enables seller/shop paid plans. |
| `KIS_EDUCATION_PRO_ENABLED` | `False` | Enables education institution/instructor plans. |
| `KIS_HEALTH_PROVIDER_PRO_ENABLED` | `False` | Enables health provider paid plans. |
| `KIS_VERIFICATION_FEES_ENABLED` | `False` | Enables paid verification processing surfaces. |
| `KIS_PROMOTION_PACKAGES_ENABLED` | `False` | Enables featured placement/promotion packages. |
| `KIS_ENTERPRISE_PLANS_ENABLED` | `False` | Enables enterprise/custom plan lead capture. |
| `KIS_PROMOTIONAL_CREDITS_DISCOUNT_ONLY` | `True` | Ensures credits stay promotional and non-cash. |

Do not use these flags to silently charge users. They should only expose pricing/paywall UX after payment, legal, refund, provider, and support processes are ready.

## Tier Matrix

### Consumer Plans

| Plan | Monthly | Annual | Intended User | Included |
|---|---:|---:|---|---|
| Free | USD 0 | USD 0 | Everyone | Basic profile, Bible, messaging, public content, following, basic notifications. |
| Consumer Plus | USD 4.99 | USD 49 | Heavy personal users/families | Family-safe controls, advanced Bible journeys, expanded saved content, priority reminders, richer spiritual progress. |
| Family Plus | USD 7.99 | USD 79 | Families | Household profiles, child/youth mode controls, family Bible journeys, family progress reports, family-safe recommendations. |

Notes:

- Consumer monetization should be secondary at launch.
- Keep core Bible and messaging free to protect trust and growth.

### Creator / Channel Plans

| Plan | Monthly | Annual | Limits | Included |
|---|---:|---:|---|---|
| Creator Free | USD 0 | USD 0 | 1 channel, limited analytics, basic posts | Basic channel, public posts, basic subscribers. |
| Creator Pro | USD 9.99 | USD 99 | Up to 3 channels, moderate storage, scheduled posts | Studio tools, scheduled content, basic analytics, custom channel profile, subscriber tools. |
| Creator Growth | USD 29.99 | USD 299 | Up to 10 channels, higher storage, advanced tools | Advanced analytics, playlists, content series, embed analytics, promotion discounts, paid content readiness. |

Recommended add-ons:

- Extra channel: USD 3/month.
- Extra storage/media processing: usage-based.
- Paid live event: platform commission, not flat subscription only.

### Institution Plans

| Plan | Monthly | Annual | Intended User | Included |
|---|---:|---:|---|---|
| Institution Free | USD 0 | USD 0 | Trial/small organization | Basic profile, limited public listing, limited team seats. |
| Institution Starter | USD 19.99 | USD 199 | Small shop/ministry/school/clinic | Verified-ready profile, landing page basics, 3 staff seats, basic dashboard, member/customer messages. |
| Institution Growth | USD 59.99 | USD 599 | Growing institution | 10 staff seats, analytics, broadcast to followers, advanced landing page, promoted listing eligibility, workflow tools. |
| Institution Network | USD 149.99 | USD 1,499 | Multi-branch organizations | 25 staff seats, branch support, advanced reporting, priority review, audit exports, custom onboarding. |

This is the most important revenue family for KIS.

### Partner Workspace Plans

| Plan | Monthly | Annual | Limits | Included |
|---|---:|---:|---|---|
| Partner Free | USD 0 | USD 0 | 1 workspace, limited channels | Basic partner profile and community. |
| Partner Workspace Pro | USD 29.99 | USD 299 | Up to 10 subrooms/channels, 5 admins | Roles, announcements, events, moderation tools, group messaging, unread analytics. |
| Partner Network | USD 99.99 | USD 999 | Larger communities | Advanced permissions, audit logs, member onboarding, event monetization readiness, priority support. |

### Seller / Commerce Plans

| Plan | Monthly | Annual | Included |
|---|---:|---:|---|
| Seller Free | USD 0 | USD 0 | Basic shop, limited active listings, USD checkout. |
| Seller Pro | USD 14.99 | USD 149 | More listings, services, shop analytics, trust badges, featured eligibility. |
| Seller Growth | USD 39.99 | USD 399 | Product video, advanced analytics, promotion bundles, customer messaging tools. |

Transaction fees:

- Marketplace products/services: 3-8%.
- Digital/creator products: 5-15%.
- Featured listing fee: separate package.

### Education Plans

| Plan | Monthly | Annual | Included |
|---|---:|---:|---|
| Instructor Free | USD 0 | USD 0 | Limited courses, free courses, basic profile. |
| Instructor Pro | USD 14.99 | USD 149 | Paid courses, certificates, analytics, cohorts. |
| Education Institution Pro | USD 49.99 | USD 499 | Institution dashboard, multiple instructors, trust badges, course bundles, student progress. |
| Education Network | USD 149.99 | USD 1,499 | Branches/departments, advanced analytics, certificate governance, enterprise onboarding. |

Revenue share:

- Course sales: 10-15% commission early.
- Certificates: fixed fee or bundled.
- Cohorts/live classes: 5-12% commission.

### Health Provider Plans

| Plan | Monthly | Annual | Included |
|---|---:|---:|---|
| Provider Free | USD 0 | USD 0 | Basic provider profile, limited service listing. |
| Health Provider Pro | USD 39.99 | USD 399 | Verified provider profile, bookings, appointment reminders, provider dashboard, patient messaging hooks. |
| Health Institution Growth | USD 99.99 | USD 999 | Staff roles, care coordination, analytics, service catalog, verified provider promotion eligibility. |
| Health Network | Custom | Custom | Multi-branch, audit exports, custom onboarding, enterprise support. |

Booking fee:

- 3-8% where legally allowed.

Safety note:

- KIS should monetize booking/admin/trust workflows, not medical diagnosis.

### Verification Fees

| Subject | Recommended Fee | Renewal |
|---|---:|---:|
| User identity | Free to USD 2.99 | Optional/free |
| Creator trust badge | USD 4.99-9.99 | Annual |
| Shop verification | USD 9.99-24.99 | Annual |
| Partner/company verification | USD 19.99-49.99 | Annual |
| Education institution verification | USD 29.99-99.99 | Annual/expiry-based |
| Health institution verification | USD 49.99-149.99 | Annual/expiry-based |

Provider costs must be known before final pricing.

### Promotion Packages

| Package | Price | Use |
|---|---:|---|
| Starter Boost | USD 5-15 | Small featured placement test. |
| Local Boost | USD 25-50 | Local channel/shop/course/provider visibility. |
| Growth Boost | USD 99-199 | Multi-day campaign with analytics. |
| Kingdom Launch Campaign | USD 299-999 | Curated launch campaign for verified institutions/partners. |

Promotion guardrails:

- Sponsored labels required.
- Staff/moderation approval for sensitive categories.
- Christian-safe ad policy required.
- Child/youth-safe filtering required.

### Enterprise Plans

Use custom annual contracts for:

- church networks;
- ministries;
- school networks;
- clinic/health networks;
- Christian business networks;
- NGOs;
- diaspora/community networks;
- KCAN regional structures.

Enterprise feature pool:

- multi-branch;
- custom onboarding;
- custom branding;
- audit exports;
- private partner network;
- priority verification;
- data processing agreement;
- dedicated support;
- advanced analytics;
- incident/rollback support.

Target minimum:

- USD 2,500/year for small networks.
- USD 10,000+/year for larger networks.

## Feature Limit Recommendations

| Capability | Free | Paid |
|---|---:|---:|
| Creator channels | 1 | 3-10+ |
| Scheduled posts | No/limited | Yes |
| Channel analytics | Basic | Advanced |
| Institution staff seats | 1-2 | 3-25+ |
| Shop active listings | Limited | Higher/unlimited by plan |
| Courses | Limited/free only | Paid courses and certificates |
| Health services | Limited listing | Full booking/service catalog |
| Partner subrooms/channels | Limited | Expanded |
| Public embeds | Basic public only | Advanced analytics/private embeds |
| Verification | Status display | Paid processing for institutions |
| Promotions | Not available | Campaign packages |
| AI help | Disabled/basic | Usage-tiered add-on |

## Trial Rules

Recommended:

- 14-day Creator Pro trial.
- 14-day Institution Starter trial.
- 30-day founder trial for selected partners/institutions.
- No trial for verification processing fees.
- No trial for transaction fees.
- Promotions paid upfront.

Trial controls:

- Require payment method only after initial launch confidence improves.
- Send reminders before trial ends.
- Keep trial cancellation easy.
- Record conversion and churn.

## Rollout Order

### Wave 1 - Fastest Revenue

1. Creator Pro/Growth.
2. Institution Starter/Growth.
3. Seller Pro.
4. Featured placement.
5. Verification fees.

### Wave 2 - Category Revenue

1. Education Institution Pro and course commission.
2. Health Provider Pro and booking fees.
3. Partner Workspace Pro.
4. Paid events/cohorts.

### Wave 3 - Scale Revenue

1. Enterprise contracts.
2. AI add-ons.
3. Advanced analytics.
4. Public web growth/SEO premium.
5. Advanced promotion marketplace.

## Required Product Surfaces

Before pricing goes live:

- Pricing page/sheet.
- Upgrade modal.
- Plan comparison component.
- Feature limit messages.
- Trial state UI.
- Payment success/failure/pending states.
- Cancel/downgrade state.
- Admin override for staff support.
- Receipt/invoice history.
- Refund policy link.
- Terms and privacy links.
- Support contact.

## Required Backend Foundations

Before pricing goes live:

- Plan catalog model or config source.
- Feature entitlement resolver.
- Subscription/payment status state.
- Transaction fee record.
- Payment audit log.
- Idempotent provider callback handling.
- Admin plan override/audit.
- Trial expiry job/check.
- Provider reconciliation.
- Refund/cancel tracking.

## Risks And Controls

| Risk | Control |
|---|---|
| Prices are too high for early markets | Founder pricing and annual discounts. |
| Prices are too low for sustainability | Keep growth/enterprise tiers higher. |
| Users think promotional credits are money | Continue strict copy guard and no cash-like behavior. |
| Institutions do not see enough value | Add analytics and clear ROI dashboards. |
| Paid gates harm growth | Keep basic use free and gate business/growth tools. |
| Legal/payment disputes | Refund policy, terms, receipts, support workflow. |
| Health monetization risk | Legal review, disclaimers, booking/admin focus only. |
| Promotion damages trust | Christian-safe ad policy, labels, staff review. |

## Phase 01 Validation

This phase is documentation-only.

No runtime code changed and no paid behavior was enabled.

## Best Prompt For Phase 02

```text
Please implement Phase 02 of the KIS Profitability 80%+ Roadmap without using git commands. Focus on Paywall And Upgrade UX. Add or design safe pricing and upgrade surfaces for Consumer Plus, Creator Pro/Growth, Institution Starter/Growth, Partner Workspace Pro, Seller Pro, Education Institution Pro, Health Provider Pro, Verification fees, Promotion packages, and Enterprise contact flows. Do not enable live charges. Keep KIS promotional credits non-cash, non-transferable, non-withdrawable, and not exchange-rated. Add locked-but-visible premium states, usage meters, plan comparison copy, and clear upgrade prompts in the relevant frontend areas where safe. Preserve existing APIs/UI behavior, update docs/profitability-roadmap/phase-02-paywall-upgrade-ux.md and docs/BUILD_STATE.md with validation, risks, and the best prompt for Phase 03.
```
