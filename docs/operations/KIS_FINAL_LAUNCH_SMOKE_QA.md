# KIS Final Launch Smoke QA And Cutover Handoff

This checklist is the final Phase 14 launch smoke handoff. It must be completed in staging before production release. Do not paste secret values, raw provider payloads, private media paths, private health records, payment instrument data, or verification documents into tickets or docs.

## Safe Local/CI Commands

Run from `/Users/nigel/dev/backend/kis`:

```bash
../env/bin/python manage.py final_launch_smoke
../env/bin/python manage.py final_launch_smoke --json
```

Use strict mode only after all evidence is attached:

```bash
../env/bin/python manage.py final_launch_smoke --strict
```

Run direct validation commands:

```bash
python3 -m py_compile apps/core/management/commands/final_launch_smoke.py apps/core/launch_ops.py apps/core/tests.py
../env/bin/python manage.py check
../env/bin/python manage.py test apps.core.tests.FinalLaunchSmokeCommandTests apps.core.tests.LaunchOperationsReadinessTests --keepdb
```

Frontend and Nest validation:

```bash
cd /Users/nigel/dev/KIS
pnpm exec tsc --noEmit --pretty false

cd /Users/nigel/dev/backend/Nestjs
pnpm run typecheck
```

## Staging Runtime Smoke Evidence

Attach evidence links or screenshots in the release ticket, not in source files:

- Django Render service deploy: service URL, health/API response, migrations applied, static collection complete.
- NestJS Render service deploy: `/health` response, Socket.IO authenticated connection, message send/receive smoke.
- Supabase storage: profile upload, channel/feed upload, private-media reference, signed/private access where required.
- Flutterwave sandbox/direct payment: payment link created, successful callback, failed callback, cancelled callback, duplicate callback, unmatched callback, audit log visible.
- React Native Android APK: login, profile, messaging, media upload, Bible, broadcast/channel, market checkout handoff, education, health, partners.
- React Native iOS/staging build: same critical flows as Android.
- Notifications: device token registration, in-app notification, push path if credentials are mounted.
- Media safety: safe upload allowed, disallowed MIME/size blocked, quarantine/review state visible to staff.
- Public web/embeds: public channel page, public content page, oEmbed, signed private/unlisted embed, abuse report, robots/sitemap policy.
- Staff-only surfaces: safety command center, security launch gate, launch operations readiness, revenue evidence, verification staff console; non-staff account must receive 403 or no entry point.

## Release Cutover Rules

- Keep live charges disabled until Flutterwave sandbox and callback evidence is approved.
- Keep legacy wallet deposit, transfer, cash/credit conversion, and wallet checkout disabled.
- Keep public indexing disabled until public pages, moderation, child-safety, and legal review are approved.
- Keep live AI/provider calls disabled until Christian/safety review is approved.
- Keep explicit-content provider live calls disabled unless provider evidence and fallback quarantine are approved.
- Rollback Django, NestJS, and mobile release independently. After rollback, re-run health checks and confirm wallet-as-money flags remain disabled.

## Production Go/No-Go

Go is allowed only when:

- `final_launch_smoke --strict` passes in the approved staging environment.
- Staff launch operations readiness is not `no_go`.
- No critical security launch gate failures remain.
- Provider callback, backup/restore, rollback, private-media, and staff-only access evidence are attached.
- Android/iOS smoke evidence is attached.
- Product/legal/pastoral/child-safety owners approve the release ticket.

If any item is missing, the status is `NO-GO` or `CONDITIONAL-GO`, not production-ready.
