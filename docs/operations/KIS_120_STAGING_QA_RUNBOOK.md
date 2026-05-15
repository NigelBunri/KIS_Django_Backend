# KIS 120 Percent Staging QA Runbook

Status: Phase 27 foundation.

Use this runbook to collect staging evidence before any launch decision. It is designed to preserve existing app behavior while proving that the core safety, reliability, payment, media, messaging, and public-growth gates are working.

## Evidence Folder Structure

Create a release evidence folder outside the repo or in your release tracker:

```text
KIS-release-YYYY-MM-DD/
  01-backend-django/
  02-backend-nest/
  03-react-native-ios/
  04-react-native-android/
  05-payments-flutterwave/
  06-firebase-notifications/
  07-media-safety-child-safety/
  08-verification-trust/
  09-public-web-embeds/
  10-rollback-recovery/
  11-go-no-go/
```

Do not store secret values, private documents, raw provider payloads, raw health/payment data, or raw storage paths in evidence.

## Core Validation Commands

Django:

```bash
python3 manage.py check
python3 manage.py makemigrations --check --dry-run
python3 manage.py verify_deployment_security --target-production
python3 scripts/security/kis_120_launch_evidence_check.py
```

Focused Django tests:

```bash
python3 manage.py test apps.core.tests.SecurityPrivacyLaunchGateTests --noinput --keepdb
python3 manage.py test apps.core.tests.MonetizationSafetySummaryTests --noinput --keepdb
python3 manage.py test apps.core.tests.AIAssistanceSafetyPolicyTests --noinput --keepdb
python3 manage.py test apps.broadcasts.tests.BroadcastChannelApiTests --noinput --keepdb
```

React Native:

```bash
cd /Users/nigel/dev/KIS
npm run typecheck -- --pretty false
npx eslint . --quiet
```

Nest:

```bash
cd "/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/Nestjs/CC_Node_Backend"
pnpm tsc --noEmit
```

## Django Backend QA

Capture:

- `manage.py check` output.
- Migration dry-run output.
- `verify_deployment_security --target-production` output with local/staging blockers annotated.
- Security launch gate API screenshot or response summary.
- Monetization safety API response summary.
- AI safety policy API response summary.
- Public web metadata endpoint responses for one public channel and one public content item.
- 404 proof for private/unlisted/child-sensitive public content.
- Admin/staff-only proof for security/safety command center surfaces.

## Nest Backend QA

Capture:

- `pnpm tsc --noEmit` output.
- Socket.IO allowed-origin settings evidence.
- Internal request signature settings evidence.
- Messaging delivery smoke test evidence for user A to user B and user B to user A.
- Subroom duplicate-prevention evidence.
- Partner/group unread count evidence if available.
- No secret values in logs.

## React Native QA

Capture iOS and Android evidence for:

- Cold start and login.
- Main tab badges.
- Messaging direct chat, group/subroom chat, media blocked/review state, retry invisibility, sender alignment after restart.
- Broadcast/Channels discovery, channel home, content detail, comments, saves, playlists, public metadata card.
- Bible reading, highlights, notes, reminders, daily meditation badge.
- Profile, verification center, Christian principles page, family/accessibility settings.
- Commerce marketplace browse, checkout handoff, payment pending/failed state.
- Education course discovery/detail/enrollment/payment state.
- Health dashboard, appointment/session/payment state.
- Partners workspace, channels/subrooms, unread counts, moderation entry points.
- Offline/low-bandwidth behavior.

## Payments QA

Capture:

- Flutterwave sandbox payment link for marketplace order.
- Flutterwave sandbox payment link for service booking.
- Flutterwave sandbox payment link for education booking/enrollment.
- Flutterwave sandbox payment link for health billing/session.
- Signed callback/webhook success, failure, cancelled, duplicate, and unmatched evidence.
- Payment audit log evidence.
- Confirmation that wallet/KISC checkout is disabled by default.

## Media And Child Safety QA

Capture:

- Safe image/video/document upload success.
- Unsafe/explicit test media blocked or quarantined.
- DM media gate evidence.
- Channel/content media gate evidence.
- Profile/partner/commerce/education/health upload gate evidence where available.
- Child/youth mode recommendation and content controls.
- Staff moderation queue evidence.
- Report, review, appeal/review-note evidence.

## Verification And Trust QA

Capture:

- User verification request/status/badge flow.
- Shop verification request/status/badge flow.
- Partner/company KYB request/status/badge flow.
- Health institution verification request/status/badge flow.
- Education institution verification request/status/badge flow.
- Badge revoke/expiry visibility.
- Webhook replay evidence if staging provider credentials are available.

## Public Web, Embeds, SEO, Growth QA

Capture:

- Public channel metadata response.
- Public content metadata response.
- Robots response.
- Sitemap-plan response.
- oEmbed response with embeds disabled/enabled state documented.
- Private/unlisted/child-sensitive 404 proof.
- Abuse report POST proof from public channel/content.
- Referral link behavior with referrals disabled unless approved.

## Rollback And Recovery QA

Capture:

- Database backup timestamp and restore-test evidence.
- App rollback runbook execution proof or tabletop notes.
- Environment rollback proof.
- Media/storage rollback proof.
- Secret rotation tabletop proof.
- Payment incident rollback proof.
- AI/public web/media safety disable-flag proof.

## Go / No-Go Rule

Do not launch if any of these are unresolved:

- Production secrets, `DEBUG`, hosts, CORS/CSRF, Redis/cache, or Socket.IO origins are unsafe.
- Wallet-as-money or credit cash-out/transfer/top-up behavior is enabled.
- Private media/public web exposure is unsafe.
- Explicit media safety gate is bypassed.
- Child/youth safety controls are not proven.
- Flutterwave callback evidence is missing for active payment surfaces.
- Verification provider live calls are enabled without evidence.
- AI provider live calls are enabled without evidence.
- Backup/restore or rollback evidence is missing.
