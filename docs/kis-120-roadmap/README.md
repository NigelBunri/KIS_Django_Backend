# KIS 120 Percent Platform Roadmap

Date created: 2026-05-14

This folder is the durable execution plan for taking KIS from the current approximately 57% practical maturity score toward a 120% differentiated platform.

The 120% goal does not mean copying WhatsApp, Discord, YouTube, Coursera, Apple Health, care platforms, Amazon, and Facebook feature-for-feature. It means:

- reach category-parity where users expect it;
- exceed each benchmark through KIS's unique integration of Christian identity, messaging, channels, commerce, education, health, partners, verification, safety, and payments;
- preserve the foundations already built instead of rewriting working systems;
- make the app beautiful, trustworthy, safe, fast, accessible, and spiritually coherent.

## Core Product Identity

KIS should become:

> A Christian, verified social operating system where people, families, creators, churches, educators, health providers, shops, companies, and communities communicate, publish, learn, care, sell, serve, and grow under clear Christian principles.

## Non-Negotiable Principles

1. KIS is a Christian app.
2. Pornographic, sexually explicit, exploitative, predatory, abusive, or degrading content must not be uploadable anywhere: feeds, channels, comments, DMs, groups, partner spaces, education, health, commerce, profile media, documents, live streams, or embeds.
3. Safety must be enforced technically, not only by policy text.
4. Children, youth, adults, and older people must all find the app understandable and emotionally safe.
5. The app must look luxurious and royal without sacrificing readability, speed, accessibility, or simplicity.
6. Every major feature must have backend truth, frontend UX, permissions, auditability, tests, and launch evidence.

## Measurement Target

Starting point from `docs/COMPETITIVE_PLATFORM_ANALYSIS_2026-05-14.md`:

- Overall KIS maturity: about 57%.
- WhatsApp parity: about 54%.
- Discord parity: about 58%.
- YouTube parity: about 64%.
- Coursera parity: about 53%.
- Apple Health parity: about 58%.
- Care coordination parity: about 61%.
- Amazon Shopping parity: about 52%.
- Facebook parity: about 57%.

Target:

- 80%: launchable strong platform.
- 95%: category-parity on the main pillars.
- 120%: category-parity plus KIS-specific advantages that benchmark apps do not combine.

## Execution Rules

- Do not use git commands unless explicitly allowed.
- Preserve existing systems and compatibility paths while improving them.
- Each phase must update:
  - `docs/kis-120-roadmap/status.md`
  - `docs/BUILD_STATE.md`
- Each phase must end with:
  - files changed;
  - validation run;
  - blockers;
  - remaining risk;
  - best prompt for the next phase.
- If tests are blocked by environment or unrelated baseline errors, record the exact blocker and continue safely.
- Safety and Christian-principle enforcement must be treated as platform infrastructure, not decoration.

## Phase Map

### Phase 00 - Roadmap And Operating Model

Goal:

- Create this durable roadmap and define the 120% execution strategy.
- Establish that Christian safety, age-aware UX, and global-quality foundations are first-class platform requirements.

Status:

- Created.

### Phase 01 - Christian Principles, Community Covenant, And Profile Entry Point

Goal:

- Add a visible page from the Profile section where users can read KIS Christian principles, content rules, safety standards, and community expectations.
- Make the page warm, clear, firm, and non-confusing for children, youth, adults, and older users.

Backend work:

- Add a safe static principles endpoint or content config if needed:
  - `apps/core/views.py`
  - `apps/core/urls.py`
  - or a new lightweight `apps/policy` module if existing patterns support it.
- Add admin-editable principles only if safe; otherwise start as versioned static content in code/docs.
- Add `principles_version` and optional acceptance tracking later if needed.

Frontend work:

- Add Profile entry:
  - `/Users/nigel/dev/KIS/src/screens/tabs/ProfileScreen.tsx`
  - profile sheet/menu components under `/Users/nigel/dev/KIS/src/screens/tabs/profile*`
- Add page/sheet:
  - `KIS Principles`
  - `Community Covenant`
  - `Safety And Family Standards`
  - `What Is Not Allowed`
  - `How To Report`
- Style using the royal gold and deep purple design system with high readability.

Content requirements:

- Respect God, human dignity, family, truth, purity, service, kindness, justice, humility, stewardship, and care for the vulnerable.
- No pornography or sexualized content.
- No exploitation, grooming, harassment, hate, threats, scams, fraud, self-harm encouragement, or abuse.
- No manipulation of children.
- No fake verified identity or impersonation.
- No health, financial, or spiritual abuse.
- Use clear wording, not legal-heavy text only.

Validation:

- `python3 manage.py check`
- `cd /Users/nigel/dev/KIS && npm run typecheck -- --pretty false`
- focused lint for touched profile/principles files.

Best next prompt:

```text
Please implement Phase 01 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on Christian principles and the Profile entry point. Add a beautiful, readable KIS Principles / Community Covenant page from the Profile section, with clear Christian content standards, anti-pornography rules, child/youth/adult/elder-safe wording, reporting guidance, and royal gold/deep purple styling. Preserve existing profile behavior, run safe validation, update docs/kis-120-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 02.
```

### Phase 02 - Platform-Wide Anti-Pornography And Media Safety Architecture

Goal:

- Make it technically difficult or impossible to upload pornographic/explicit content anywhere.
- Build a single media safety gate for all upload surfaces.

Backend work:

- Add centralized media safety service:
  - upload classification
  - MIME/extension/size validation
  - image/video/document quarantine state
  - explicit-content scan result
  - manual review state
  - audit log
- Candidate areas:
  - `apps/core`
  - `apps/broadcasts`
  - `apps/chat`
  - `apps/commerce`
  - `apps/partners`
  - `apps/verification`
  - `apps/notifications`
- Add a `MediaSafetyScan` model if no equivalent exists.
- Default new uploads to `pending_scan` for production-sensitive surfaces.
- Add provider adapter stubs for services such as AWS Rekognition, Google Vision SafeSearch, Hive, Sightengine, Cloudflare Images/Stream moderation, or a self-hosted model. Keep live calls disabled by default.
- Never log raw images, tokens, private storage paths, or documents.

Frontend work:

- Add upload states:
  - checking
  - blocked
  - needs review
  - approved
- Apply to:
  - DMs
  - feeds/channels
  - comments
  - profile media
  - partner media
  - shop/product/service media
  - education/health documents where previewable

Policy work:

- Explicitly block pornography, nudity for sexual display, sexualized minors, exploitation, and sexually explicit text/media.
- Define medical/education exceptions only where clinically/educationally necessary, private, and permissioned.

Validation:

- Backend tests for blocked media metadata.
- Frontend lint/typecheck for touched upload surfaces.

Best next prompt:

```text
Please implement Phase 02 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on platform-wide anti-pornography and media safety architecture. Add or design a centralized media safety gate for uploads across DMs, feeds/channels, comments, profile media, partner spaces, commerce, education, health, and verification. Include MIME/size validation, quarantine states, explicit-content provider adapter stubs with live calls disabled by default, audit logs, user-safe blocked/review messages, and no raw secret/path logging. Preserve existing uploads where possible, run safe validation, update docs/kis-120-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 03.
```

### Phase 03 - Royal UX Design System 2.0

Goal:

- Create a state-of-the-art UX foundation for the whole app: royal, luxurious, calm, readable, fast, and emotionally safe.

Frontend work:

- Centralize design tokens:
  - royal gold material gradients
  - deep purple surfaces
  - white/cream reading backgrounds
  - dark theme gold adjusted for contrast
  - semantic colors for success/warning/danger/info
  - age-friendly text scale
  - spacing and radius rules
  - tab badges
  - icon states
  - press/hover/disabled/loading states
- Candidate files:
  - `/Users/nigel/dev/KIS/src/theme/constants.ts`
  - `/Users/nigel/dev/KIS/src/theme/navTheme.ts`
  - `/Users/nigel/dev/KIS/src/theme/health/colors.ts`
  - shared button/card/input/badge components.

Psychology requirements:

- Children: clear icons, friendly copy, no dense cognitive load.
- Youth: fast, expressive, social, but safe.
- Adults: productive, trustworthy, efficient.
- Older users: readable, predictable, high contrast, large tap targets.
- All users: calm hierarchy, obvious actions, no hidden critical controls.

Design requirements:

- Deep gold borders, not orange.
- Purple should feel royal, not heavy.
- Light theme should not drown screens in purple; use warm whites and cream surfaces.
- Dark theme should use the same gold language with slightly darker gold for contrast.
- Avoid unreadable gold text on gold backgrounds.
- Use shiny gold only for actionable controls/selection accents, not all text.

Validation:

- Typecheck.
- Focused lint.
- Screenshot QA for main tabs and key surfaces.

Best next prompt:

```text
Please implement Phase 03 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on Royal UX Design System 2.0. Centralize app-wide royal gold/deep purple/cream/white/dark theme tokens, button styles, selected states, tab badge styles, card/input rules, contrast-safe text/icon colors, age-friendly spacing and tap targets, and accessibility defaults. Preserve existing screens but make the shared foundation ready for global polish. Run focused frontend validation, update docs/kis-120-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 04.
```

### Phase 04 - Global Navigation And Information Architecture

Goal:

- Make KIS understandable despite being a super-app.
- Reduce confusion by creating clear journeys for Messaging, Broadcast/Channels, Bible, Profile, Partners, Health, Education, Market, and Notifications.

Frontend work:

- Audit main tabs and nested navigation.
- Make bottom tab labels, badges, selected indicators, and headers consistent.
- Add contextual launchers instead of scattering features randomly.
- Ensure every major screen has:
  - clear title
  - search/filter behavior
  - primary action
  - empty state
  - loading state
  - error state
  - retry state

Backend work:

- No broad backend work unless navigation needs summary endpoints.

Best next prompt:

```text
Please implement Phase 04 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on global navigation and information architecture. Audit and improve the main tab and nested navigation experience so Messaging, Broadcast/Channels, Bible, Profile, Partners, Health, Education, Market, and Notifications feel coherent and easy for children, youth, adults, and older users. Add consistent headers, primary actions, empty/loading/error states, and clear entry points without breaking existing routes. Run safe frontend validation, update docs/kis-120-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 05.
```

### Phase 05 - Messaging Trust Layer

Goal:

- Bring messaging from "working feature" to trusted communication foundation.

Work:

- Complete the existing messaging roadmap Phase 01-16.
- Ensure:
  - no duplicate direct rooms
  - no duplicate subrooms
  - correct sender alignment after restart
  - fast bidirectional delivery
  - invisible retry
  - durable cache
  - E2EE production behavior
  - media/camera/files
  - full search
  - calls QA
  - statuses/updates
  - unread badges
  - partner/community messaging integration

Best next prompt:

```text
Please implement Phase 05 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on the Messaging Trust Layer. Continue from docs/messaging-platform-roadmap/status.md and complete the next highest-risk messaging reliability slice: conversation identity, duplicate direct/subroom prevention, cache durability, sender alignment after restart, fast bidirectional delivery, invisible retry, and exact conversation list updates. Preserve existing chat UI, run safe Django/Nest/React Native validation, update docs/kis-120-roadmap/status.md, docs/messaging-platform-roadmap/status.md, and docs/BUILD_STATE.md, and give the best prompt for Phase 06.
```

### Phase 06 - Safe Messaging Media And Family Controls

Goal:

- Make media sharing in DMs, groups, and partner chats safe for a Christian/family environment.

Work:

- Connect Phase 02 media safety gate to chat attachments.
- Add view-once/disappearing media only after safety and E2EE are stable.
- Add child/youth account restrictions if age support exists.
- Add family-safe defaults and reporting.

Best next prompt:

```text
Please implement Phase 06 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on safe messaging media and family controls. Connect the centralized media safety gate to chat attachments, camera sends, documents, voice/video attachments, group media, and partner chat media. Add user-safe blocked/review states, reporting hooks, and family-safe defaults without breaking current messaging. Run safe backend/frontend validation, update docs/kis-120-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 07.
```

### Phase 07 - Feed Channels 120% YouTube Core

Goal:

- Turn channels into a production-grade creator system.

Work:

- Continue Feed Channels roadmap.
- Finish:
  - creator Studio polish
  - channel creation/discovery
  - channel-scoped publishing
  - live readiness
  - comments/moderation
  - playlists
  - embeds
  - analytics
  - recommendation-ready metadata
  - subscription/bell notifications

Best next prompt:

```text
Please implement Phase 07 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on Feed Channels 120% YouTube Core. Continue from docs/feed-channels-roadmap/status.md and strengthen channel creation, channel-scoped publishing, Studio polish, content manager clarity, comments/moderation, playlist UX, subscription/bell behavior, analytics readiness, and recommendation metadata. Preserve legacy feed compatibility, run safe validation, update docs/kis-120-roadmap/status.md, docs/feed-channels-roadmap/status.md, and docs/BUILD_STATE.md, and give the best prompt for Phase 08.
```

### Phase 08 - Production Media Pipeline

Goal:

- Build or integrate the media pipeline required for YouTube-level channels and safe uploads.

Work:

- Upload sessions.
- Chunked upload.
- Thumbnail generation.
- Video metadata extraction.
- Transcoding/adaptive bitrate plan.
- CDN/storage abstraction.
- Captions/subtitles.
- Content safety scan.
- Private/public/unlisted access rules.
- Live replay storage.

Best next prompt:

```text
Please implement Phase 08 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on the production media pipeline. Add or design upload sessions, chunked upload compatibility, thumbnail/video metadata extraction, scan/quarantine states, public/private/unlisted access rules, caption metadata, CDN/storage abstraction points, and provider-neutral transcoding/live replay hooks. Keep local development working and live provider calls disabled unless explicitly configured. Run safe validation, update docs/kis-120-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 09.
```

### Phase 09 - Christian Content Moderation And Safety Operations

Goal:

- Enforce Christian content principles across public and private surfaces.

Work:

- Report categories.
- Moderation queues.
- Auto-block for pornography.
- Manual review for borderline cases.
- Appeals.
- Repeat offender controls.
- Child safety escalation.
- Admin audit logs.
- Creator/channel strikes.
- Partner/community moderation roles.

Best next prompt:

```text
Please implement Phase 09 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on Christian content moderation and safety operations. Add or connect report categories, moderation queues, auto-block rules for pornography/sexual exploitation, manual review states, appeals, repeat-offender controls, child-safety escalation, channel strikes, partner/community moderation roles, and admin audit logs. Preserve existing moderation systems where present, run safe validation, update docs/kis-120-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 10.
```

### Phase 10 - Unified Search Across The Super-App

Goal:

- Make KIS feel fast and intelligent.

Work:

- Messaging search.
- Channel/content search.
- People/search contacts.
- Shops/products/services search.
- Education/course search.
- Health institution/service search.
- Partners/group search.
- Bible search.
- Ranking, recent history, safe suggestions.

Best next prompt:

```text
Please implement Phase 10 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on unified super-app search. Add fast backend-backed search contracts and frontend UX for people, contacts, messages, groups, channels, feed content, shops/products/services, education, health institutions/services, partners, and Bible content. Preserve existing per-section search, add safe ranking and exact navigation targets, run safe validation, update docs/kis-120-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 11.
```

### Phase 11 - Notification Intelligence And Attention Health

Goal:

- Make notifications useful without overwhelming users.

Work:

- Build on main-tab badge system.
- Add notification grouping.
- Quiet hours.
- Family-safe defaults.
- Priority levels.
- Digest mode.
- Actionable notifications.
- Cross-device read state.

Best next prompt:

```text
Please implement Phase 11 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on notification intelligence and attention health. Build on the main-tab badge system with grouped notifications, priority levels, quiet hours, digest mode, actionable notifications, cross-device read state, and age-aware notification defaults. Preserve existing notification APIs/UI, run safe validation, update docs/kis-120-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 12.
```

### Phase 12 - Commerce 120% Amazon Core

Goal:

- Make commerce safe, USD-first, trust-first, and conversion-ready.

Work:

- Complete financial redesign.
- USD/Flutterwave evidence.
- Product/service detail polish.
- Seller dashboard.
- Inventory.
- Reviews/Q&A.
- Returns/refunds/disputes.
- Shipping/tracking.
- Trust badges.
- Live/channel commerce.

Best next prompt:

```text
Please implement Phase 12 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on Commerce 120% Amazon Core. Continue from docs/financial-system-redesign-roadmap.md and docs/shop-system-progress.md. Strengthen USD-first checkout, Flutterwave payment evidence, seller dashboard, inventory readiness, reviews/Q&A, returns/refunds/disputes, shipping/tracking placeholders or integrations, trust badges, and channel/live commerce connections. Keep KIS Coins promotional only, run safe validation, update docs/kis-120-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 13.
```

### Phase 13 - Education 120% Coursera Core

Goal:

- Turn education from profiles/content into a real institution LMS.

Work:

- One institution-rooted source of truth.
- Course builder.
- Lessons.
- Assignments/quizzes/exams.
- Grading.
- Progress.
- Certificates.
- Instructor dashboard.
- Student dashboard.
- Live classes.
- Paid enrollment via USD.

Best next prompt:

```text
Please implement Phase 13 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on Education 120% Coursera Core. Continue from docs/education-system-progress.md and move education toward one institution-rooted LMS source of truth with course builder, lessons, assignments/quizzes/exams, grading, progress, certificates, instructor/student dashboards, live classes, and USD paid enrollment where safe. Preserve legacy education compatibility, run safe validation, update docs/kis-120-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 14.
```

### Phase 14 - Health And Care 120% Apple Health Plus

Goal:

- Make KIS personal-health plus provider-care, not only health operations.

Work:

- Patient health hub.
- Device sync plan.
- HealthKit/Health Connect adapters.
- Medication reminders.
- Care plans.
- Appointments/telehealth.
- Consent and sharing.
- Provider messaging.
- Health institution trust.

Best next prompt:

```text
Please implement Phase 14 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on Health and Care 120% Apple Health Plus. Continue from docs/health-profile-200-apple/STATUS.md and strengthen the patient health hub, medication/reminder UX, care plans, appointments/telehealth, provider messaging, consent/sharing, audit logs, HealthKit/Health Connect adapter planning, and verified health institution trust. Preserve existing health APIs/UI, run safe validation, update docs/kis-120-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 15.
```

### Phase 15 - Partners 120% Discord Plus

Goal:

- Make partner workspaces feel like Discord plus business operations.

Work:

- Server shell polish.
- Role/permission UX.
- Channels/categories.
- Voice/live rooms.
- Events.
- Member onboarding.
- Bots/apps/automation.
- Moderation.
- Partner content, commerce, education, and health modules.

Best next prompt:

```text
Please implement Phase 15 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on Partners 120% Discord Plus. Continue from docs/partners-discord-rebuild/README.md and improve partner workspace shell polish, roles/permissions UX, channel/category navigation, invites/onboarding, moderation, presence, events, voice/live room readiness, bots/apps/automation placeholders, and integrated commerce/education/health/content modules. Run safe validation, update docs/kis-120-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 16.
```

### Phase 16 - Bible And Spiritual Growth Core

Goal:

- Make the Christian identity of KIS felt through excellent spiritual growth experiences, not only rules.

Work:

- Bible reading plans.
- Daily meditations.
- Highlights/comments.
- Family/devotional groups.
- Church/community content.
- Pastoral/leader channels.
- Safe spiritual guidance boundaries.
- Certificate/course integration.

Best next prompt:

```text
Please implement Phase 16 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on Bible and Spiritual Growth Core. Strengthen Bible reading plans, daily meditations, highlights/comments, verse navigation, family/devotional groups, church/community content, pastoral/leader channels, safe spiritual guidance boundaries, and education/channel integration. Preserve existing Bible behavior, run safe validation, update docs/kis-120-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 17.
```

### Phase 17 - Unified Identity, Verification, Trust, And Badges

Goal:

- Make trust visible everywhere.

Work:

- User verification.
- Institution verification.
- Shop/health/education/partner badges.
- Badge display consistency.
- Revocation.
- Expiry.
- Audit.
- Staff review.
- Fraud signals.

Best next prompt:

```text
Please implement Phase 17 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on unified identity, verification, trust, and badges. Continue from docs/verification-system-roadmap.md and make verified user/shop/health/education/partner badges consistent across messaging, channels, commerce, education, health, partners, profile, and search. Include badge revocation/expiry signals, staff review visibility, fraud/suspicious pattern audit, and frontend display consistency. Run safe validation, update docs/kis-120-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 18.
```

### Phase 18 - Social Graph And Recommendation Engine Foundation

Goal:

- Make KIS intelligent without becoming chaotic.

Work:

- Follow/subscribe/member relationships.
- Interest graph.
- Safe recommendations.
- People/channels/groups/shops/courses/events suggestions.
- No unsafe adult content recommendations.
- Ranking transparency controls.

Best next prompt:

```text
Please implement Phase 18 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on social graph and recommendation engine foundation. Add or consolidate follow/subscribe/member/interest relationships across users, channels, groups, shops, courses, health institutions, education institutions, partners, and Bible content. Add safe recommendation contracts and frontend surfaces with Christian safety filters, no explicit content recommendations, and transparency controls. Run safe validation, update docs/kis-120-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 19.
```

### Phase 19 - Accessibility, Age Modes, And Family Experience

Goal:

- Make KIS usable by children, youth, adults, and older people.

Work:

- Font scaling.
- High contrast.
- Large tap targets.
- Simplified mode.
- Youth safety mode.
- Elder-friendly mode.
- Family supervision concepts.
- Safer defaults.

Best next prompt:

```text
Please implement Phase 19 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on accessibility, age modes, and family experience. Add global accessibility improvements, readable font scaling, high contrast, large tap targets, simplified navigation options, youth safety defaults, elder-friendly UI patterns, and family/supervision planning where safe. Preserve existing behavior, run frontend validation and screenshot/manual QA notes, update docs/kis-120-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 20.
```

### Phase 20 - Creator, Institution, And Business Dashboards

Goal:

- Give every serious operator a world-class dashboard.

Work:

- Creator dashboard.
- Shop dashboard.
- Education institution dashboard.
- Health institution dashboard.
- Partner dashboard.
- Analytics summaries.
- Action queues.
- Moderation queues.
- Revenue/payment evidence.

Best next prompt:

```text
Please implement Phase 20 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on creator, institution, and business dashboards. Build or harmonize dashboards for creators/channels, shops, education institutions, health institutions, and partners with analytics summaries, action queues, moderation queues, verification status, payment evidence, and next-best actions. Preserve existing dashboards, run safe validation, update docs/kis-120-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 21.
```

### Phase 21 - Observability, Admin Intelligence, And Safety Command Center

Goal:

- Let operators see what is happening before users suffer.

Work:

- Admin dashboards.
- Security/audit logs.
- Moderation metrics.
- Media safety scan metrics.
- Payment incident logs.
- Messaging delivery metrics.
- Channel upload/playback metrics.
- Error alerts.

Best next prompt:

```text
Please implement Phase 21 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on observability, admin intelligence, and the safety command center. Add admin/staff visibility for security/audit logs, moderation queues, media safety scans, payment incidents, message delivery health, channel upload/playback health, notification badge health, and critical error alerts. Do not expose secrets or private message bodies. Run safe validation, update docs/kis-120-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 22.
```

### Phase 22 - Performance, Offline, And Low-Bandwidth Excellence

Goal:

- Make KIS fast for users with weak devices or weak networks.

Work:

- Caching.
- Offline queues.
- Media lazy loading.
- Pagination.
- Skeletons.
- API response slimming.
- Websocket reconnect behavior.
- Background sync.

Best next prompt:

```text
Please implement Phase 22 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on performance, offline, and low-bandwidth excellence. Improve caching, offline queues, media lazy loading, pagination, skeleton states, API response slimming, websocket reconnect behavior, and background sync for messaging, channels, commerce, education, health, Bible, partners, and notifications. Preserve existing behavior, run safe validation and performance notes, update docs/kis-120-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 23.
```

### Phase 23 - Security, Privacy, Compliance, And Child Safety Launch Gate

Goal:

- Make the platform safer before public scale.

Work:

- Finish security roadmap blockers.
- Production env evidence.
- Private media evidence.
- IDOR tests.
- Backups/rollback drills.
- Child safety escalation.
- Data retention.
- Privacy controls.
- Report/export/delete account paths.

Best next prompt:

```text
Please implement Phase 23 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on security, privacy, compliance, and child safety launch gates. Continue from docs/SECURITY_HARDENING_ROADMAP.md and close production env evidence, private media evidence, IDOR tests, backup/rollback drills, child safety escalation, data retention, privacy controls, account export/delete, and incident response blockers. Run safe validation, update docs/kis-120-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 24.
```

### Phase 24 - Monetization Without Legal Risk

Goal:

- Make KIS profitable without making KIS Coins look like money.

Work:

- USD subscriptions.
- Flutterwave direct payments.
- Creator/business/institution tiers.
- Promotional credits only.
- Receipts/invoices.
- Refunds/disputes.
- Revenue analytics.

Best next prompt:

```text
Please implement Phase 24 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on monetization without legal risk. Continue from docs/financial-system-redesign-roadmap.md and complete USD subscriptions, Flutterwave direct payment evidence, creator/business/institution tiers, promotional-credit-only KIS Coins, receipts/invoices, refunds/disputes, revenue analytics, and compliance-safe UI copy. Run safe validation, update docs/kis-120-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 25.
```

### Phase 25 - AI Assistance With Christian And Safety Boundaries

Goal:

- Add helpful AI without creating unsafe spiritual, medical, financial, or child-safety risk.

Work:

- AI search summaries.
- Creator drafting.
- Moderation assistance.
- Learning assistant.
- Health assistant boundaries.
- Bible study assistant boundaries.
- No false authority.
- Human escalation.

Best next prompt:

```text
Please implement Phase 25 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on AI assistance with Christian and safety boundaries. Add or design AI helpers for search summaries, creator drafting, moderation assistance, learning support, Bible study support, and health workflow support with strict boundaries, disclaimers, human escalation, no secret logging, and no unsafe spiritual/medical/financial authority. Preserve existing AI integration, run safe validation, update docs/kis-120-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 26.
```

### Phase 26 - Public Web, Embeds, SEO, And Growth Loops

Goal:

- Make KIS content discoverable and shareable outside the app safely.

Work:

- Public channel/content pages.
- Embeds.
- oEmbed.
- SEO metadata.
- Share cards.
- Invite links.
- Referral/growth loops.
- Safety and privacy checks.

Best next prompt:

```text
Please implement Phase 26 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on public web, embeds, SEO, and growth loops. Build or plan public channel/content/shop/course/event pages, safe embeds/oEmbed, SEO metadata, share cards, invite links, referrals, and privacy-safe access rules. Preserve private/unlisted content protections and Christian content safety. Run safe validation, update docs/kis-120-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 27.
```

### Phase 27 - Full QA, Device Lab, And Staging Evidence

Goal:

- Prove the app works end to end.

Work:

- iOS/Android QA.
- Backend tests.
- Nest tests/typecheck.
- React Native typecheck/lint.
- Manual QA scripts.
- Payment sandbox evidence.
- Media safety evidence.
- Messaging multi-device evidence.
- Live/channel evidence.

Best next prompt:

```text
Please implement Phase 27 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on full QA, device lab, and staging evidence. Run or prepare iOS/Android manual QA, backend tests, Nest checks, React Native typecheck/lint, payment sandbox evidence, media safety evidence, messaging multi-device evidence, live/channel evidence, and rollback proof. Record blockers instead of waiting on long environment setup, update docs/kis-120-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 28.
```

### Phase 28 - 80 Percent Launch Cut

Goal:

- Decide what ships first without pretending everything is 120%.

Work:

- Select launch-safe surfaces.
- Hide unsafe/incomplete surfaces behind flags.
- Final production checklists.
- App store readiness.
- Monitoring.
- Support process.

Best next prompt:

```text
Please implement Phase 28 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on the 80 Percent Launch Cut. Decide which surfaces are launch-safe, which remain behind feature flags, and which need more QA. Add production launch checklists, monitoring requirements, support process, app store readiness notes, and rollback rules. Run safe validation, update docs/kis-120-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 29.
```

### Phase 29 - 95 Percent Category Parity Push

Goal:

- Push the main pillars to near category parity.

Work:

- Messaging near WhatsApp.
- Channels near YouTube.
- Partners near Discord.
- Commerce near Amazon.
- Education near Coursera.
- Health near Apple Health/care.
- Social near Facebook.

Best next prompt:

```text
Please implement Phase 29 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on the 95 Percent Category Parity Push. Review evidence from all prior phases and close the highest-impact remaining parity gaps across messaging, channels, partners, commerce, education, health, social graph, Christian safety, UX, payments, and production QA. Preserve stable behavior, run safe validation, update docs/kis-120-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 30.
```

### Phase 30 - 120 Percent Differentiation Layer

Goal:

- Make KIS more than the sum of its inspirations.

Work:

- Cross-domain journeys:
  - watch a verified health channel and book appointment;
  - join a church/partner community and take a course;
  - message a verified shop and buy safely;
  - follow an education channel and earn certificate;
  - receive Bible plan, family discussion, and safe community support;
  - manage business/partner operations from one verified workspace.
- Global trust layer.
- Unified recommendation graph.
- Christian safety layer.
- Royal UX.
- Operator dashboards.

Best next prompt:

```text
Please implement Phase 30 of the KIS 120 Percent Platform Roadmap without using git commands. Focus on the 120 Percent Differentiation Layer. Build cross-domain journeys that no single benchmark app offers: verified channel-to-commerce, channel-to-course, health content-to-appointment, partner community-to-services, Bible plan-to-family/community discussion, shop chat-to-safe purchase, and institution dashboard-to-public channel growth. Tie them together with trust badges, Christian safety, unified search/recommendations, royal UX, and operator analytics. Run safe validation, update docs/kis-120-roadmap/status.md and docs/BUILD_STATE.md, and give the final 120 Percent readiness summary.
```

