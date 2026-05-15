# KIS 120 Percent Differentiation Release Slices

Status: Phase 30 foundation.

This register converts the differentiation strategy into execution slices that can be picked up after the 80% launch cut and 95% parity push have evidence.

| ID | Slice | Builds on | First deliverables | Feature gate | QA and evidence |
| --- | --- | --- | --- | --- | --- |
| D120-SG-001 | Spiritual Growth OS | Bible, notifications, KCAN, family modes, moderation. | Journey templates, family devotional plan model, pastoral review metadata, offline scripture QA checklist. | `KIS_DIFFERENTIATION_120_FEATURES_ENABLED` plus Bible-specific release flag. | Exact verse navigation, offline scripture, child/youth journey review, ministry publisher QA. |
| D120-KI-001 | Kingdom Impact Dashboard | Dashboards, privacy-safe telemetry, partners, education, health, commerce, Bible. | Aggregate impact summary endpoints, dashboard cards, privacy-safe testimony placeholders. | `KIS_DIFFERENTIATION_120_FEATURES_ENABLED` plus dashboard release flag. | No private health/payment/verification data, anti-manipulation copy review, staff-only audit proof. |
| D120-ECO-001 | Creator Institution Ecosystem | Channels, commerce, education, health, partners, verification, USD payments. | Cross-domain journey links, verified institution bundles, creator-to-course/shop/event placeholders. | `KIS_DIFFERENTIATION_120_FEATURES_ENABLED` plus ecosystem release flag. | Payment safety, trust badge proof, media safety, role permission proof. |
| D120-REC-001 | Family-Safe Recommendations | Social graph, age modes, media safety, Christian ranking controls. | Age-aware ranking profiles, blocked/muted/hidden exclusions, safe recommended journeys. | `KIS_DIFFERENTIATION_120_FEATURES_ENABLED` plus recommendation release flag. | Child/youth review, privacy-safe aggregate proof, no sensitive data leakage. |
| D120-LIVE-001 | Live Ministry Learning Commerce Health | Channels, education, commerce, health, partners, media pipeline. | Provider-neutral live adapters disabled by default, replay review state, live event QA runbook. | `KIS_DIFFERENTIATION_120_FEATURES_ENABLED` plus live provider flag. | Live moderation, replay safety, child/youth defaults, rollback proof. |
| D120-AI-001 | Christian AI Companion | AI safety, Bible, education, health admin, commerce, moderation, privacy. | Redacted prompt contracts, role-specific assistant shells, staff audit placeholders. | `KIS_DIFFERENTIATION_120_FEATURES_ENABLED` plus AI provider flag. | No diagnosis/advice, no private data leakage, prompt redaction, safety refusal proof. |
| D120-LB-001 | Global Low-Bandwidth Excellence | Performance/offline, Bible, messaging, channels, education, commerce, health. | Low-data mode policy, offline queues, thumbnail fallback rules, device-lab low-bandwidth scripts. | `KIS_DIFFERENTIATION_120_FEATURES_ENABLED` plus low-bandwidth release flag. | Startup timing, reconnect, cache privacy, stale-while-revalidate proof. |
| D120-UX-001 | Royal UX Memory System | Royal UX 2.0, accessibility, age modes, navigation. | Age-aware presets, simplified mode, calmer notification patterns, contrast-safe components. | `KIS_DIFFERENTIATION_120_FEATURES_ENABLED` plus UX release flag. | Contrast proof, tap-target proof, older-user readability, no dark patterns. |

## Execution Order

1. Complete 80% launch evidence and resolve P0 blockers.
2. Complete 95% parity P0 slices: messaging reliability, channels publish/broadcast, payments/order lifecycle, trust/safety.
3. Start 120-A and 120-G first because spiritual growth and low-bandwidth excellence are core to KIS identity and reduce operational risk.
4. Start 120-D and 120-H once age/accessibility preferences are stable.
5. Start 120-C and 120-B after dashboards and trust summaries are stable.
6. Start 120-E and 120-F only after live provider and AI safety staging evidence exists.

## Pastoral And Safety Review Gate

Before a 120% slice is visible in production, attach evidence that:

- the feature respects Christian principles and family dignity;
- the feature avoids pornography, exploitation, manipulation, predatory content, and unsafe youth exposure;
- the feature does not replace qualified medical, financial, legal, or pastoral care;
- user data is minimized and private by default;
- moderation and reporting paths are visible and tested.

