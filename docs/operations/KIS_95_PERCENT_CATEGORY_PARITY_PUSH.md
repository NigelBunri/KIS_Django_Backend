# KIS 95 Percent Category Parity Push

Status: Phase 29 foundation.

This document defines the post-80% release train for reaching strong parity with the major products KIS combines: WhatsApp/Telegram, YouTube, Coursera, Amazon, Apple Health, Discord, and Bible/spiritual growth apps.

The 95% push must not destabilize the 80% launch cut. New parity work stays behind `KIS_PARITY_95_FEATURES_ENABLED=False` until a specific slice has evidence, QA, and owner approval.

## Global Rules

- Preserve the 80% launch cut.
- Ship parity slices one at a time.
- Keep high-risk systems behind flags until staging evidence exists.
- Do not weaken Christian principles, media safety, child/youth safety, privacy, monetization safety, or rollback controls.
- Every slice must include QA criteria and rollback notes.

## Messaging Parity: WhatsApp / Telegram

Target: reliable, fast, familiar messaging with KIS safety and partner/subroom advantages.

Priority slices:

1. Conversation reliability hardening: bidirectional delivery, sender alignment, cache recovery, exact unread counts.
2. Media messaging: camera picker, safe image/video/document/audio attachments, blocked/review state, retry.
3. Subrooms and groups: no duplicate subrooms, deep-link to exact subroom, roles/permissions, unread badges.
4. Calls: voice/video call state, missed call badges, call history, safe attachment handoff.
5. Search: global messaging search with contacts, groups, channels, communities, and message jump/highlight.
6. Privacy controls: mute, block, report, disappearing messages placeholder, export/delete request planning.

QA criteria:

- A-to-B and B-to-A messages arrive fast.
- Restart preserves sender alignment and conversation list.
- Unsafe media cannot bypass moderation.
- Search opens the exact message and highlights briefly.

## Channels Parity: YouTube

Target: channels that feel complete for multi-format content, not only video.

Priority slices:

1. Channel creation and studio polish.
2. Channel-scoped content creation for all feed types.
3. Video/short/image/audio/document/detail rendering.
4. Playlists, comments, saves, history, subscribe/bell behavior.
5. Live streaming provider readiness behind flags.
6. Public web renderer, embeds, SEO, share cards, abuse reports.
7. Creator analytics and moderation dashboard.

QA criteria:

- Every content item belongs to a channel.
- Public/private/unlisted rules are respected.
- Embeds and public SEO never expose private data.
- Live provider calls remain disabled until approved.

## Education Parity: Coursera

Target: credible institution/course experience with trust, progress, and certificates.

Priority slices:

1. Course discovery and institution trust badges.
2. Course detail quality: syllabus, instructors, reviews, Q&A.
3. Enrollment/payment state with USD direct-provider flow.
4. Learning paths and progress tracking.
5. Certificates with verification/share proof.
6. Instructor dashboard and moderation-safe materials.
7. Offline/low-bandwidth lessons.

QA criteria:

- Course enrollment and payment states are clear.
- Progress resumes correctly.
- Certificates do not expose private data.
- Unsafe education media is blocked or reviewed.

## Commerce Parity: Amazon

Target: trusted USD marketplace with reliable sellers, orders, reviews, and fulfillment.

Priority slices:

1. Product/service discovery and detail quality.
2. Cart/order reliability.
3. Flutterwave payment handoff, callback, and audit proof.
4. Seller trust badges and verification summaries.
5. Reviews, questions, report abuse.
6. Delivery/fulfillment state and complaint window.
7. Recommendation placeholders without private data leakage.

QA criteria:

- No KISC/wallet-as-money checkout.
- Payment states reconcile correctly.
- Seller/product media passes safety gate.
- Reviews/questions can be moderated.

## Health Parity: Apple Health Plus

Target: privacy-first care dashboard with appointments, care plans, records summaries, and reminders.

Priority slices:

1. Health dashboard clarity and provider trust badges.
2. Appointment/session reliability.
3. Care plans and health records summaries.
4. Medication/vitals/reminder placeholders.
5. Patient/provider messaging hooks.
6. Payment state UX.
7. Low-bandwidth patient access.

QA criteria:

- No private health data leaks into public/search/AI/telemetry.
- Payment state does not use wallet/KISC.
- Provider trust badge is visible.
- AI does not diagnose or replace care.

## Partners Parity: Discord

Target: partner workspaces with servers, channels/subrooms, roles, events, announcements, and moderation.

Priority slices:

1. Workspace/channel/subroom navigation polish.
2. Roles/permissions clarity.
3. Group messaging, announcements, and unread counts.
4. Events and onboarding flows.
5. Moderation/audit tools.
6. Partner dashboards and member analytics.
7. Family-safe partner media.

QA criteria:

- Partner messages and unread badges are accurate.
- Subrooms open to the correct context.
- Roles prevent unauthorized actions.
- Unsafe partner media is blocked/reviewed.

## Bible And Spiritual Growth Parity

Target: Bible reading, study, prayer, meditation, courses, community, and family journeys.

Priority slices:

1. Bible reader UX and exact verse navigation.
2. Reading plans, streaks, reminders, daily meditations.
3. Highlights, notes, comments, and sharing.
4. Audio/video devotionals.
5. Prayer groups and family-safe journeys.
6. Offline scripture and low-bandwidth mode.
7. KCAN/ministry publisher tools.

QA criteria:

- Highlight/comment navigation opens the exact verse.
- Dark-theme highlight contrast is readable.
- Child/youth spiritual journeys are safe.
- Ministry content passes moderation.

## 95% Release Train

| Train | Scope | Must Have Evidence |
| --- | --- | --- |
| 95-A | Messaging reliability/search/media | Device lab, Nest/Django/RN validation |
| 95-B | Channels studio/public web/analytics | Public/private exposure tests, media safety |
| 95-C | Commerce + payments fulfillment | Flutterwave callback, order lifecycle |
| 95-D | Education + certificates + progress | Enrollment/payment/progress/certificate QA |
| 95-E | Health dashboard + appointments + records summaries | Privacy/security and provider QA |
| 95-F | Partners roles/subrooms/events/moderation | Partner QA and permission tests |
| 95-G | Bible spiritual growth/offline/family journeys | Bible QA and child-safety review |

## Risk Controls

- The 80% launch cut remains the default production position until staging evidence proves a 95% slice is safe.
- 95% features must be backward compatible with current APIs, serializers, routes, and React Native screens.
- Database changes must be additive, reversible, and migration-tested before a slice is enabled.
- Live provider calls for AI, verification, media safety, payments beyond approved Flutterwave flows, live streaming, embeds, indexing, and referrals stay disabled until the relevant runbook is complete.
- No parity slice may weaken Christian content rules, child/youth safety, private media access, USD-only payment safety, promotional-credit restrictions, or staff-only admin boundaries.
- Every slice needs a rollback plan, owner, QA evidence, and no open P0/P1 blocker before production enablement.

## Feature Flags

Required default:

```text
KIS_PARITY_95_FEATURES_ENABLED=False
KIS_EXPERIMENTAL_120_FEATURES_ENABLED=False
```

Enable a 95% slice only after:

- the slice has an owner;
- staging evidence is attached;
- rollback path exists;
- no P0/P1 blocker remains;
- child/media/security/privacy review passes.
