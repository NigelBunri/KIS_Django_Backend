# KIS Profitability 80%+ Roadmap - Phase 08 Partner And Ministry Workspace Revenue Engine

Date: 2026-05-16

Status: Completed as a safe locked-preview implementation. No live charges, hard gates, subscriptions, payment provider calls, workspace seat limits, premium moderation gates, enterprise contracts, or partner restrictions were enabled.

## Phase Objective

Prepare partner/ministry workspace monetization across partner workspaces, subrooms/channels, member roles, announcements, events, moderation, analytics, organization apps, reports, audit logs, and group messaging without changing existing free partner behavior.

This phase uses the disabled pricing catalog and keeps the financial redesign intact:

- partner upgrades remain USD/direct-provider first;
- Partner Workspace Pro, Enterprise, and Promotion Packages are visible but not live;
- workspace seat visibility is shown as future reporting only;
- premium moderation/audit tools are preview-only;
- event/promotion entry points are visible without campaign checkout;
- enterprise contact readiness is preview-only;
- KIS promotional credits remain non-cash reward/subsidy credits only.

## Frontend Changes

Added reusable partner revenue preview component:

- `/Users/nigel/dev/KIS/src/components/profitability/PartnerRevenuePreviewCard.tsx`

Wired preview states into:

- `/Users/nigel/dev/KIS/src/components/partners/PartnersCenterPane.tsx`
- `/Users/nigel/dev/KIS/src/components/partners/PartnerSheet.tsx`
- `/Users/nigel/dev/KIS/src/components/partners/PartnerReportsPanel.tsx`
- `/Users/nigel/dev/KIS/src/components/partners/PartnerAuditPanel.tsx`
- `/Users/nigel/dev/KIS/src/components/partners/PartnersMessagesPane.tsx`
- `/Users/nigel/dev/KIS/src/components/partners/PartnerOrganizationAppsPanel.tsx`

## Preview Areas

| Area | Preview State | Purpose |
|---|---|---|
| Partner center pane | Partner revenue engine preview | Shows workspace seats, premium moderation, events, analytics, and promotion readiness. |
| Partner settings sheet | Workspace upgrade preview | Shows Partner Workspace Pro and Enterprise controls without limiting current communities/groups/channels. |
| Reports panel | Partner analytics revenue preview | Shows advanced analytics, scheduled exports, enterprise evidence, and USD billing readiness. |
| Audit panel | Moderation and audit revenue preview | Shows premium moderation, searchable audit history, escalation, and governance reporting. |
| Messaging pane empty state | Partner messaging revenue preview | Shows unread analytics, premium moderation, subroom/channel scale, and family-safe media controls. |
| Organization apps panel | Organization apps revenue preview | Shows app launchers, role visibility, data scopes, and audit-ready access logs. |

## Locked-But-Visible Rules

Every partner revenue preview uses:

- disabled Partner Workspace Pro, Enterprise, and Promotion Packages price labels;
- "NOT LIVE" badge;
- explicit copy that current free partner behavior remains available;
- USD/direct-provider-first upgrade copy;
- promotional-credit safety copy;
- workspace seat, moderation, audit, analytics, event, and enterprise visibility as preview-only.

No component opens plan checkout, changes entitlements, limits seats, blocks subrooms/channels/groups, starts provider calls, creates promotion campaigns, or changes partner messaging permissions.

## Partner Safety Position

- KIS promotional credits are not cash, not transferable, not withdrawable, and not exchange-rated.
- New paid partner upgrade copy remains USD/direct-provider first.
- Partner promotions are described as future reviewed/sponsored placement, not automatic ad buying.
- Enterprise contact readiness does not create contracts or billing obligations.
- Premium moderation and audit copy is shown as future operations visibility, not active paid gating.
- Existing partner workspace, roles, subrooms/channels, reports, audit, organization apps, and messaging behavior is preserved.

## Validation

Focused validation command:

```text
cd /Users/nigel/dev/KIS && npx eslint src/components/profitability/PartnerRevenuePreviewCard.tsx src/components/partners/PartnersCenterPane.tsx src/components/partners/PartnerSheet.tsx src/components/partners/PartnerReportsPanel.tsx src/components/partners/PartnerAuditPanel.tsx src/components/partners/PartnersMessagesPane.tsx src/components/partners/PartnerOrganizationAppsPanel.tsx src/services/profitabilityPricing.ts --quiet
```

Result: passed.

## Remaining Risks

- This phase is preview-only. It does not implement partner subscriptions, entitlements, seat enforcement, enterprise sales workflow, promotion checkout, invoices, refunds, support workflows, or billing provider calls.
- Workspace seat policy needs product/legal review to avoid locking out existing partner admins or ministry teams.
- Premium moderation/audit features require privacy review because partner workspaces can include sensitive member activity.
- Promotion packages require sponsored labels, moderation review, child/youth filtering, and abuse controls before launch.
- Enterprise contact flow needs a real sales/support process before activation.
- Backend feature flags and entitlement enforcement are still intentionally deferred.

## Best Prompt For Phase 09

```text
Please implement Phase 09 of the KIS Profitability 80%+ Roadmap without using git commands. Focus on Verification, Trust, And Promotion Revenue Engine. Using the disabled pricing catalog and previous monetization previews, add safe locked-but-visible verification processing fee, badge renewal, trust boost, and promotion package states across user/profile verification, shop verification, partner verification, health verification, education verification, channel/creator trust surfaces, and promotion entry points. Prepare USD-only fee copy, provider/manual-review cost visibility, sponsored-label readiness, campaign review states, and trust-badge renewal reminders without enabling live charges or hard-blocking current verification/promotion behavior. Keep KIS promotional credits non-cash, non-transferable, non-withdrawable, and not exchange-rated, preserve existing APIs/UI behavior, run focused validation, update docs/profitability-roadmap/phase-09-verification-trust-promotion-revenue-engine.md and docs/BUILD_STATE.md with risks, validation, and the best prompt for Phase 10.
```
