# KIS 80 Percent Launch Cut

Status: Phase 28 foundation.

This document defines the minimum safe launch scope. The goal is to launch KIS at 80% safely, with a controlled path to 95% category parity and 120% differentiation.

## Launch Principle

Launch only what is stable, safe, and proven by staging evidence. Keep optional or high-risk features behind flags until they have provider, safety, legal, and device-lab proof.

## Required 80% Launch Scope

| Area | 80% Launch Requirement | Evidence |
| --- | --- | --- |
| Auth/profile | Login, profile, session/device handling, settings, Christian principles page | Phase 27 evidence template |
| Messaging | Direct messages, conversation list, sender alignment after restart, invisible retry, unread badges | Device-lab evidence |
| Safe messaging media | Upload validation, unsafe media blocked/reviewed, report/block controls | Media safety evidence |
| Broadcast/channels | Channel creation, channel-scoped content, list/detail, subscribe/bell placeholders, comments/saves/playlists basic UX | Broadcast QA evidence |
| Bible | Reader, highlights, notes, reminders, daily meditation/missed schedule badges | Bible QA evidence |
| Commerce | USD product/service browse, cart/order, direct Flutterwave payment state UX | Payment QA evidence |
| Education | Course/institution discovery, enrollment/payment state, progress placeholders, trust badges | Education QA evidence |
| Health | Dashboard, appointments/sessions/payment state, provider trust badges, patient-safe privacy boundaries | Health QA evidence |
| Partners | Workspace, group/subroom messaging, roles, unread counts, moderation entry points | Partner QA evidence |
| Notifications | Main-tab badges, profile notifications, message unread, Bible/broadcast/partner badge decrement | Notification QA evidence |
| Verification/trust | User/shop/partner/health/education badge summaries, manual review paths, revoke/expiry visibility | Verification QA evidence |
| Media safety | Anti-pornography gate across uploads, quarantine/review, staff queue | Safety QA evidence |
| Child/youth safety | Age mode defaults, safe recommendations, no child-targeted ads/growth | Child safety evidence |
| Security | Production settings, strong secrets, origins, throttling, private media, audit logs | Security launch gate |
| Payments | Wallet-as-money disabled, USD/direct-provider first, Flutterwave callback proof | Payment sign-off |
| Backup/rollback | Backup, restore test, app/env/media rollback, incident response | Recovery evidence |

## Must Stay Deferred Or Flagged For 80%

These features must not block 80% launch if they are disabled or clearly marked as placeholders:

- Public web indexing.
- Public referral/growth loops.
- Embeds on third-party domains.
- Live AI provider calls.
- Verification live provider calls beyond approved staging/manual review.
- Creator payouts and advanced creator monetization.
- Ads and sponsorship automation.
- Live streaming provider network calls.
- Advanced recommendation personalization beyond privacy-safe placeholders.
- Full standalone public web renderer.
- Full analytics drill-down dashboards.

## Required 80% Flags

These must be configured this way unless release leadership explicitly approves otherwise:

```text
KIS_LAUNCH_CUT_MODE=80
KIS_EXPERIMENTAL_120_FEATURES_ENABLED=False
KIS_LEGACY_WALLET_DEPOSIT_ENABLED=False
KIS_LEGACY_WALLET_TRANSFER_ENABLED=False
KIS_LEGACY_CASH_CREDIT_CONVERSION_ENABLED=False
KIS_LEGACY_COMMERCE_WALLET_CHECKOUT_ENABLED=False
KIS_LEGACY_EDUCATION_WALLET_CHECKOUT_ENABLED=False
KIS_LEGACY_HEALTH_WALLET_CHECKOUT_ENABLED=False
KIS_AI_LIVE_PROVIDER_CALLS_ENABLED=False
KIS_PUBLIC_WEB_INDEXING_ENABLED=False
KIS_PUBLIC_REFERRALS_ENABLED=False
KIS_EMBEDS_ENABLED=False
VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED=False
MEDIA_SAFETY_LIVE_PROVIDER_CALLS_ENABLED=False
```

`KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED` may be enabled in staging/production only after Flutterwave sandbox callback proof is attached.

`MEDIA_EXPLICIT_SCAN_REQUIRED` should be true in production if a live explicit-content provider or approved quarantine/manual-review replacement is active.

## 80% Blocker Triage

| Severity | Definition | Example | Launch Decision |
| --- | --- | --- | --- |
| P0 | Unsafe, legal, security, payment, child-safety, data exposure, app cannot function | Private media public, wallet cash-out enabled, messaging broken both ways | NO-GO |
| P1 | Core launch flow broken, but no immediate data/legal exposure | Commerce checkout fails, verification status unavailable, main tab broken | NO-GO unless feature disabled |
| P2 | Important UX issue with workaround | Minor layout issue on one screen, noncritical badge delay | Conditional go with owner/date |
| P3 | Polish/future improvement | Advanced analytics missing, public SEO still disabled | Does not block 80% |

## Minimum Go/No-Go Criteria

Mark GO only if:

- All P0/P1 blockers are closed or the affected feature is disabled by flag.
- Phase 27 evidence template is filled for enabled launch surfaces.
- `scripts/security/kis_80_launch_cut_check.py` passes.
- `scripts/security/kis_120_launch_evidence_check.py` passes.
- `verify_deployment_security --target-production` has no unresolved critical production blocker.
- iOS and Android device-lab evidence is attached.
- Flutterwave evidence is attached for enabled payment flows.
- Backup/restore and rollback evidence are attached.

## Path After 80%

For 95%:

- Complete public web renderer, embeds QA, richer analytics, provider live verification, full recommendation tuning, broader device coverage, and deeper CI/regression coverage.

For 120%:

- Enable differentiated AI assistance, creator monetization, live streaming provider integrations, growth loops, and advanced family/spiritual journeys only after safety, provider, legal, and pastoral evidence is complete.
