# KIS 100% Implementation And 80%+ Global Parity Roadmap

## Phase 00 - Launch Scope Lock And Blocker Register

Date: 2026-05-17

Purpose: lock what can safely go live now, what must remain hidden or explicitly gated, and what belongs to the post-launch global-parity push. This phase does not redesign product behavior. It gives the team a single decision document before production exposure.

## Operating Rule

KIS should not launch by exposing every partially built capability. Launch should expose stable working flows, keep risky unfinished systems behind feature flags, and track global-parity work separately.

Launch categories:

- **Launchable**: safe to expose after production environment evidence and device QA.
- **Launchable with evidence**: implementation exists but must be proven in staging/production-like QA before public exposure.
- **Hidden / flagged**: do not expose to normal users at launch unless explicitly approved.
- **Post-launch parity**: required for 80%+ global parity, but not required for first safe launch.

## Existing Feature Flags And Config Checks

These controls already exist or are documented in the codebase and should be treated as the Phase 00 launch lock.

| Area | Flag / check | Launch state |
|---|---|---|
| Legacy wallet deposits | `KIS_LEGACY_WALLET_DEPOSIT_ENABLED` | Must remain `False` |
| Legacy wallet transfers | `KIS_LEGACY_WALLET_TRANSFER_ENABLED` | Must remain `False` |
| Cash/credit conversion | `KIS_LEGACY_CASH_CREDIT_CONVERSION_ENABLED` | Must remain `False` |
| Legacy wallet upgrades | `KIS_LEGACY_WALLET_UPGRADE_ENABLED` | Must remain `False` unless counsel approves |
| Promo cash bonus | `KIS_LEGACY_PROMO_CASH_BONUS_ENABLED` | Must remain `False` |
| Commerce wallet checkout | `KIS_LEGACY_COMMERCE_WALLET_CHECKOUT_ENABLED` | Must remain `False` |
| Education wallet checkout | `KIS_LEGACY_EDUCATION_WALLET_CHECKOUT_ENABLED` | Must remain `False` |
| Health wallet checkout | `KIS_LEGACY_HEALTH_WALLET_CHECKOUT_ENABLED` | Must remain `False` |
| Direct provider links | `KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED` | Staging evidence required before production |
| Mock payments | `PAYMENTS_MOCK` | Must be `False` in production |
| Payment provider | `KIS_COMMERCE_DEFAULT_PAYMENT_PROVIDER`, `KIS_EDUCATION_DEFAULT_PAYMENT_PROVIDER`, `KIS_HEALTH_DEFAULT_PAYMENT_PROVIDER` | Should be `flutterwave` for launch |
| Profitability billing | `KIS_PROFITABILITY_BILLING_ENABLED` | Must remain `False` |
| Trials | `KIS_PROFITABILITY_TRIALS_ENABLED` | Must remain `False` |
| Promotion checkout | `KIS_PROFITABILITY_PROMOTION_CHECKOUT_ENABLED` | Must remain `False` |
| Enterprise leads | `KIS_PROFITABILITY_ENTERPRISE_LEADS_ENABLED` | Must remain `False` until support/legal process exists |
| Verification live calls | `VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED` | Must remain `False` in production until approved |
| Verification sandbox network | `VERIFICATION_PROVIDER_SANDBOX_NETWORK_ENABLED` | Staging only |
| Verification live envs | `VERIFICATION_PROVIDER_LIVE_ALLOWED_ENVS` | Should not include production before sign-off |
| Media safety | `MEDIA_SAFETY_ENABLED` | Must be `True` |
| Explicit scan required | `MEDIA_EXPLICIT_SCAN_REQUIRED` | Must be `True` in production |
| Media safety live provider | `MEDIA_SAFETY_LIVE_PROVIDER_CALLS_ENABLED` | Keep `False` until provider evidence exists |
| Media size/MIME controls | `MEDIA_SAFETY_MAX_UPLOAD_BYTES`, `MEDIA_SAFETY_ALLOWED_MIME_TYPES`, `MEDIA_SAFETY_ALLOWED_MIME_PREFIXES`, `MEDIA_SAFETY_BLOCKED_EXTENSIONS` | Must be set to production policy |
| AI live provider | `KIS_AI_LIVE_PROVIDER_CALLS_ENABLED` | Must remain `False` |
| AI prompt/response storage | `KIS_AI_STORE_PROMPTS_ENABLED`, `KIS_AI_STORE_RESPONSES_ENABLED` | Must remain `False` unless privacy review approves |
| AI safety | `KIS_AI_OUTPUT_MODERATION_REQUIRED`, `KIS_AI_INPUT_REDACTION_REQUIRED`, `KIS_AI_CHILD_SAFE_MODE_REQUIRED` | Must remain `True` |
| AI medical/financial advice | `KIS_AI_MEDICAL_DIAGNOSIS_ENABLED`, `KIS_AI_FINANCIAL_ADVICE_ENABLED` | Must remain `False` |
| Public web | `KIS_PUBLIC_WEB_ENABLED` | Can be enabled only for safe public pages |
| Public indexing | `KIS_PUBLIC_WEB_INDEXING_ENABLED` | Keep `False` until SEO/privacy QA |
| Referrals | `KIS_PUBLIC_REFERRALS_ENABLED` | Keep `False` until abuse review |
| 95% parity features | `KIS_PARITY_95_FEATURES_ENABLED` | Keep `False` at first launch |
| 120% features | `KIS_DIFFERENTIATION_120_FEATURES_ENABLED`, `KIS_EXPERIMENTAL_120_FEATURES_ENABLED` | Keep `False` at first launch |
| Production debug | `DEBUG` | Must be `False` |
| Hosts/origins | `ALLOWED_HOSTS`, CORS, CSRF, Socket.IO origins | Must match deployed domains/IPs only |
| Redis/cache/throttling | production cache config | Must be verified |
| Private media | signed/private media checks | Must be verified |
| Admin/docs | staff-only checks | Must be verified |
| Backup/rollback | provider evidence | Must be verified |

## Scope By Product Area

### Messaging

Launchable:

- Direct conversations.
- Conversation list.
- Basic group/subroom messaging where already stable.
- Pin, mute, archive, delete-for-me, mark-read.
- Unread counts and main-tab badge count.
- Safe media upload path when media safety is enabled.

Launchable with evidence:

- Bidirectional delivery after app restart.
- Cache durability on iOS and Android.
- Long conversation history alignment.
- E2EE fallback/history behavior.
- Status/updates messaging.
- Call entry points.

Hidden / flagged:

- Any advanced WhatsApp/Telegram-style feature that is not proven on both devices.
- Any message backup/export flow that lacks privacy review.

Post-launch parity:

- Full calls, screen share, call links, disappearing/view-once, message folders, global message search, large communities, bots/automation, multi-device encrypted backup.

Blockers:

- Real-device QA is still required for restart alignment, duplicate-room prevention, unread state, call behavior, and media attachments.

### Broadcast / Channels

Launchable:

- Legacy broadcast feeds.
- Channel discovery.
- Channel home/detail.
- Channel creation and channel-scoped creation where implemented.
- Playlists, comments, saves, history where backend/frontend are stable.
- Channel/content broadcast state after ownership checks.

Launchable with evidence:

- Channel Studio creation/publishing workflow.
- Embed/oEmbed policy.
- Media validation across image/video/audio/document.
- Moderation/report/hide/mute/delete/unbroadcast.

Hidden / flagged:

- Live streaming as a production feature until provider/player/moderation evidence exists.
- Public indexing for channel pages until SEO/privacy QA.

Post-launch parity:

- YouTube-grade analytics, creator monetization, subscriptions, Shorts/Reels polish, live/replay pipeline, recommendation ranking, creator copyright/safety tooling.

Blockers:

- Production media pipeline, live streaming, and creator analytics are not yet global-parity complete.

### Bible And Spiritual Growth

Launchable:

- Bible reader.
- Highlights/notes where stable.
- Daily meditations.
- Reading plans/reminders where existing.
- KCAN vision/community principles entry points.

Launchable with evidence:

- Sticky tab/header behavior across devices.
- Offline/low-bandwidth scripture behavior.
- Bible notification badge decrement behavior.

Hidden / flagged:

- Any AI spiritual guidance that calls a live provider.

Post-launch parity:

- Audio Bible, video devotionals, family spiritual journeys, ministry publishing workflow, study groups, advanced plans/streaks, offline packs.

Blockers:

- Manual QA needed for device sizes, dark/light theme contrast, offline behavior, and reminders.

### Profile

Launchable:

- Profile overview.
- Notifications.
- Verification center entry.
- KIS principles/community covenant.
- KCAN vision entry where stable.
- Account/settings/device controls that are proven.

Launchable with evidence:

- Profile dashboard blocks on small screens and large text settings.
- Staff-only panels only visible to staff.

Hidden / flagged:

- Normal-user profitability/readiness/beta explanation cards.
- Unfinished dashboard/admin cards for normal users.

Post-launch parity:

- Advanced public profile pages, portfolio/creator identity, trust scoring, family controls, privacy center.

Blockers:

- Final visual pass needed on smaller devices and accessibility text scaling.

### Partners

Launchable:

- Partner workspaces.
- Partner groups/channels/subrooms where stable.
- Roles/owner access where verified.
- Partner unread badge.
- Partner dashboard panels that are functional.

Launchable with evidence:

- Permissions and owner/admin actions.
- Partner message/subroom deep-linking.
- Moderation/audit flows.

Hidden / flagged:

- Enterprise monetization/lead capture.
- Advanced automation or export features lacking QA.

Post-launch parity:

- Discord-level roles, bots, voice/stage events, onboarding, audit, moderation, large communities.

Blockers:

- Full workspace permission matrix and real-device group/subroom QA.

### Commerce / Market

Launchable:

- Shops.
- Product/service browsing.
- Cart/order views.
- Provider order views.
- USD-only copy.
- Historical promotional-credit/KISC records as read-only/safe labels.

Launchable with evidence:

- Flutterwave direct payment intents and callbacks.
- Order completion, complaints, refunds, provider settlement behavior.
- Seller/product/service management.

Hidden / flagged:

- KIS Coins as money.
- Wallet checkout.
- Transfer/withdraw/deposit/cash conversion.
- Promotion checkout.

Post-launch parity:

- Amazon-grade fulfillment, returns, reviews/Q&A, seller analytics, recommendations, sponsored listings with labels.

Blockers:

- Payment and fulfillment staging evidence are required before full public commerce launch.

### Education

Launchable:

- Education discovery.
- Institution pages.
- Course/module browsing where stable.
- Enrollment/payment state copy as USD/direct-provider-first.
- Verification badges.

Launchable with evidence:

- Paid enrollment flow.
- Course progress and certificate state.
- Institution owner/admin access.

Hidden / flagged:

- Live monetization enforcement.
- Any unproven paid course checkout.

Post-launch parity:

- Coursera-grade course player, assessments, certificates, cohorts, instructor analytics, offline lessons, learning paths.

Blockers:

- Course lifecycle and frontend type/QA evidence still need final proof.

### Health

Launchable:

- Health institution listing/detail.
- Institution owner/admin management where access is verified.
- Service/session/appointment views.
- Provider trust badges.

Launchable with evidence:

- Appointment booking and payment state.
- Care plans/records summaries.
- Reminders.
- Provider/patient messaging hooks.

Hidden / flagged:

- Medical diagnosis claims.
- Any AI health provider calls.
- Any unreviewed clinical workflow.

Post-launch parity:

- Apple Health/care coordination-grade records, vitals, medication reminders, secure sharing, provider dashboards, compliance reports.

Blockers:

- Owner access and clinical workflow QA must be verified on staging data.

### Verification

Launchable:

- User, shop, partner, health, education verification cases.
- Manual staff review.
- Badge summaries.
- Badge issue/revoke.

Launchable with evidence:

- Provider sandbox end-to-end.
- Webhook replay.
- Private media signed-access proof.

Hidden / flagged:

- Production live provider calls.
- Raw document exposure.

Post-launch parity:

- Live provider integration, renewal reminders, fraud/risk scoring, institution KYB automation.

Blockers:

- Staging provider evidence is required before provider-live behavior.

### Notifications

Launchable:

- In-app notifications.
- Main tab badge counters.
- Realtime badge refresh.
- Mark-source-read endpoints where wired.

Launchable with evidence:

- Exact producer metadata across Bible, Broadcast, Education, Health, Market, Partners, Profile.
- Push delivery on real devices.

Hidden / flagged:

- Premium notification features.
- Spam-prone campaign notifications.

Post-launch parity:

- Advanced digest controls, priority alerts, quiet hours, notification analytics, cross-device delivery confidence.

Blockers:

- Producer/read-state coverage needs final QA.

### Payments And Monetization

Launchable:

- USD-first payment architecture.
- Direct payment intent skeleton.
- Flutterwave adapter/callback foundations.
- Read-only wallet/history/receipts.
- Promotional credits as non-cash/non-transferable/non-withdrawable.

Launchable with evidence:

- Flutterwave staging links and signed callbacks.
- Receipts/refunds/support.
- Payment incident rollback.

Hidden / flagged:

- Live subscriptions.
- Entitlement enforcement.
- Production payment provider enablement.
- Promotion checkout.
- Enterprise lead capture.
- KISC buy/sell/withdraw/convert/transfer.

Post-launch parity:

- Subscription lifecycle, invoices, refunds, trials, enterprise contracts, creator monetization, ad/sponsorship marketplace.

Blockers:

- Legal/accounting/payment provider sign-off required before monetization.

### Media Safety

Launchable:

- Central media safety gate.
- MIME/size/extension controls.
- Quarantine/review states.
- Audit logs.
- User-safe blocked/review copy.

Launchable with evidence:

- Enforcement across every upload producer.
- Moderator queue workflow.
- Dark/light UI states.

Hidden / flagged:

- Live explicit-content provider calls without approved provider evidence.

Post-launch parity:

- Automated provider moderation, appeal workflows, staff analytics, live-stream moderation, child-safety monitoring.

Blockers:

- Upload producer coverage and provider proof remain launch-critical.

### Search

Launchable:

- Existing unified search baseline.
- Local/module search where stable.

Launchable with evidence:

- Messaging search across contacts/groups/channels/messages.
- Deep-link and highlight behavior.

Hidden / flagged:

- Ranking/personalization that uses sensitive data without privacy review.

Post-launch parity:

- Fast global indexed search, safe recommendations, semantic search, privacy-preserving ranking, child-safe filters.

Blockers:

- Search must be performance-tested before broad use.

### Public Web And Embeds

Launchable:

- Safe public vision/trust pages.
- Embed policy foundations with private/unlisted protections.

Launchable with evidence:

- Public channel/content pages.
- oEmbed metadata.
- Share cards.
- Abuse-safe reporting.

Hidden / flagged:

- Search-engine indexing.
- Referrals/growth loops.
- Private/unlisted/child-sensitive public exposure.

Post-launch parity:

- SEO, sitemap, public player, creator pages, referral loops, web onboarding.

Blockers:

- Public web QA and privacy review are required.

### Admin / Operations

Launchable:

- Staff-only verification/moderation/revenue evidence foundations.
- Security runbooks.
- Backup/restore/rollback docs.
- Safety command center placeholders.

Launchable with evidence:

- Provider backup/restore proof.
- Rollback drill.
- Staff-only access checks.
- Incident runbook tabletop.

Hidden / flagged:

- Any admin data exposing secrets, raw documents, raw storage paths, private health/payment details.

Post-launch parity:

- Full operational intelligence, alerting, SIEM/export, live incident dashboards, support tooling.

Blockers:

- Production operations evidence is still required.

### Accessibility, Family Modes, Offline, Performance

Launchable:

- Royal UX token foundation.
- Contrast and selected-state fixes where already applied.
- Low-bandwidth/offline placeholders.
- Family/age preference foundations.

Launchable with evidence:

- iOS/Android device lab.
- Large text and small-screen QA.
- Offline/cache behavior.
- Startup and navigation performance.

Hidden / flagged:

- Any child/family enforcement not yet proven.

Post-launch parity:

- Fully persistent age modes, parental controls, offline packs, stale-while-revalidate caching, performance telemetry.

Blockers:

- Device lab and accessibility pass are required before confident public launch.

## Master Blocker Register

| Priority | Blocker | Owner evidence needed |
|---|---|---|
| P0 | Production secrets and env values not fully verified | Production launch gate output without secrets |
| P0 | `DEBUG=False`, hosts/origins, Redis/cache, private media, admin/docs need deployed proof | Deployment checklist evidence |
| P0 | Backup/restore and rollback not proven on provider | Restore drill and rollback drill evidence |
| P0 | Flutterwave staging payment proof still needed before payment launch | Payment link, callback, receipt, failure, duplicate proof |
| P0 | Media safety must be enforced on all upload entry points | Upload producer matrix and blocked/quarantine tests |
| P0 | Messaging restart/cache/history/call QA needed | iOS/Android real-device QA evidence |
| P1 | Verification provider sandbox proof incomplete | Provider case, webhook replay, badge issue/revoke proof |
| P1 | Notification producer/read-state coverage must be proven | Per-tab badge increment/decrement tests |
| P1 | Health/education/institution owner permissions require final QA | Owner/admin access screenshots or test records |
| P1 | Public web/indexing/embeds need privacy QA | Public exposure checklist |
| P1 | Staff-only pages must be verified as inaccessible to normal users | Access-control smoke tests |
| P2 | Global search, recommendations, live streaming, AI, advanced monetization are not first-launch safe | Keep flagged/hidden |

## First Launch Recommendation

Launch KIS as a controlled platform with:

- Messaging basics.
- Profile and verification.
- Bible/spiritual growth.
- Broadcast feeds and basic channels.
- Partners/workspaces basics.
- Commerce/education/health discovery and management where QA-proven.
- USD payment readiness only where Flutterwave staging evidence exists.
- Media safety and moderation enforced.
- Notifications and badges after producer/read-state proof.

Do not launch:

- KIS Coins as money.
- Live production subscriptions.
- Production live verification provider calls.
- Production live AI provider calls.
- Live streaming.
- Public indexing/referrals.
- Experimental 95%/120% features.

## Phase 00 Validation

This phase is documentation and launch-scope locking only. It intentionally avoids code behavior changes.

Safe validation to run:

```bash
python3 manage.py check
python3 manage.py makemigrations --check --dry-run
```

## Best Prompt For Phase 01

```text
Please implement Phase 01 of the KIS 100% Implementation and 80%+ Global Parity roadmap without using git commands. Focus on production security and environment proof. Use the Phase 00 launch scope lock to verify production-safe settings and launch gates for Django, Nest, and React Native: DEBUG=False, ALLOWED_HOSTS, CORS/CSRF, Socket.IO origins, Redis/cache throttling, private media, admin/docs staff-only access, production secrets, Firebase/admin credentials, backup/restore, rollback, and staff-only surfaces. Add or update safe verification scripts/docs where needed without exposing secrets, preserve local development, run safe validation, record blockers in docs/implementation-parity-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 02.
```
