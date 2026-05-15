# KIS 95 Percent Category Parity Gap Register

Status: Phase 29 foundation.

Purpose: keep the post-80% release train focused on parity work that can be shipped safely without destabilizing the 80% launch cut. Every item below must stay behind owner approval, staging evidence, rollback proof, and the relevant feature flags until it is ready.

| ID | Category | Target parity | Current gap | Priority | Release slice | Risk controls | Evidence needed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| MSG-95-001 | WhatsApp/Telegram Messaging | Fast, reliable direct/group messaging with media, calls, search, delivery state, and privacy controls. | Search jump/highlight, call evidence, media attachment polish, group admin controls, invisible retry evidence, and restart/cross-device proof need stronger QA. | P0 | 95-A | No duplicate conversations/subrooms; no decrypted payload logging; child-safe media gate; unread state must be exact. | iOS/Android device-lab proof for A-to-B and B-to-A messaging, restart sender alignment, search jump, calls, and unsafe media blocking. |
| CHN-95-001 | YouTube Channels | Channel creation, channel-scoped publishing, playlists, comments, saves, history, subscriptions, public pages, embeds, live/replays, studio analytics. | Live provider, public renderer, full embed QA, analytics depth, recommendation quality, and creator moderation evidence remain incomplete. | P0 | 95-B | Media safety before publish; public/private/unlisted enforcement; embeds disabled until QA; no raw storage paths. | Channel creation/publish/broadcast/unbroadcast QA, public/private exposure tests, embed tests, media safety proof, analytics smoke evidence. |
| EDU-95-001 | Coursera Education | Trusted course discovery, syllabus, instructor, progress, certificates, reviews, Q&A, payments, offline learning. | Progress/certificate depth, offline lessons, reviews/Q&A moderation, and instructor analytics need implementation/evidence. | P1 | 95-D | USD-only direct payment; verification badges; moderation-safe course media; no certificate private-data leak. | Enrollment/payment/progress/certificate QA, unsafe education media tests, institution trust badge proof. |
| COM-95-001 | Amazon Commerce | Product discovery, detail quality, cart/order reliability, fulfillment, reviews/questions, seller trust, safe payments. | Returns/disputes, delivery tracking, reviews/questions moderation, recommendation quality, and provider callback proof need completion. | P0 | 95-C | No KISC/wallet-as-money checkout; seller media safety; Flutterwave callback reconciliation; complaint window evidence. | Marketplace order lifecycle, service booking lifecycle, payment callback, duplicate callback, fulfillment/complaint proof. |
| HLT-95-001 | Apple Health-Style Health | Privacy-first dashboard, appointments, care plans, records summaries, reminders, provider messaging, payment UX. | Care plans, records summaries, medication/vitals reminders, and privacy-safe provider messaging need stronger implementation/evidence. | P1 | 95-E | No health data in public/search/AI/telemetry; provider trust badges; no diagnosis by AI; USD-only payments. | Appointment/session QA, health privacy review, provider trust display proof, reminders smoke test, payment-state proof. |
| PRT-95-001 | Discord Partners | Workspaces, roles, channels/subrooms, announcements, events, onboarding, moderation/audit, unread counts. | Role permission depth, onboarding/events, moderation dashboard depth, and partner unread evidence need completion. | P1 | 95-F | Role/permission checks; safe partner media; exact unread counts; audit logs for staff actions. | Partner workspace/subroom QA, role denial tests, unread badge evidence, moderation/audit proof. |
| BIB-95-001 | Bible And Spiritual Growth | Reader, plans, streaks, reminders, highlights, notes, devotionals, prayer groups, family journeys, offline scripture. | Offline scripture, audio/video devotionals, family journeys, prayer group workflows, and ministry publishing QA need completion. | P1 | 95-G | Christian content policy; child/youth-safe defaults; exact verse navigation; readable dark-theme highlights. | Exact verse navigation proof, plan/streak/reminder proof, offline scripture smoke test, child-safety review. |
| TRU-95-001 | Trust, Safety, And Verification | Unified badges, revocation/expiry visibility, staff risk views, moderation SLA, provider evidence. | Live provider evidence, moderation SLA reporting, and badge expiry QA need staging proof. | P0 | 95-All | No raw documents/secrets; private media only; staff-only evidence; provider calls disabled until staging approved. | Verification sandbox evidence, badge issue/revoke/expiry proof, staff audit export proof. |
| PERF-95-001 | Performance And Offline | Fast startup, resilient cache, low-bandwidth mode, pagination, retry/backoff, safe telemetry. | Broad device-lab performance evidence and offline cache QA need completion across major tabs. | P1 | 95-All | No private data in telemetry; request dedupe; cursor discipline; graceful stale-while-revalidate. | Startup timing, offline/reconnect proof, low-bandwidth media fallback proof, telemetry redaction proof. |

## Prioritization Rule

The 95% release train starts only after the 80% launch cut has no open P0 blocker. P0 parity items may be implemented in code earlier, but they remain disabled or hidden until their QA evidence is attached.

## Required Evidence Per Item

- owner and release slice;
- feature flag and rollback path;
- backend validation;
- React Native iOS and Android smoke proof where user-facing;
- child/media/security/privacy review where applicable;
- no P0/P1 launch blocker after the slice is enabled.
