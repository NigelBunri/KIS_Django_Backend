# Phase 01 - Production Security And Environment Proof

Date: 2026-05-17

Purpose: convert the Phase 00 launch lock into a concrete production-security evidence checklist for Django, Nest, React Native, provider operations, private media, staff-only surfaces, and rollback readiness.

This phase does not expose secret values, rotate credentials, or change runtime behavior. It adds a redacted verification layer and records the remaining launch evidence needed before KIS goes live.

## Evidence Areas

| Area | Required proof | Current local status |
|---|---|---|
| Django production settings | `DJANGO_SETTINGS_MODULE=config.settings.production`, `DEBUG=False`, strong `SECRET_KEY`, HTTPS security flags | Verification command exists; production env evidence still needed |
| Django hosts/origins | Real `ALLOWED_HOSTS`, CORS, CSRF origins | Code supports strict config; deployed values still need proof |
| Redis/cache throttling | Production shared cache, non-development throttle rates | Verification command exists; provider cache proof still needed |
| Admin/docs | Staff-only admin/docs/schema behavior outside debug | Code path exists; deployed URL smoke proof still needed |
| Private media | Signed/private access and no direct private upload exposure | Media safety/private patterns exist; staging proof still needed |
| Nest origins/security | `security:env-check`, Socket.IO origins, internal auth/signature config | Nest env checker exists; deployed values still need proof |
| React Native launch gate | `ci:launch`, typecheck/lint/audit scripts | Scripts exist; real-device QA still needed |
| Firebase/admin credentials | Handling runbook and no secret logging | Runbook exists; credential rotation/ownership evidence still needed |
| Backup/restore | Database restore drill and evidence | Runbook exists; provider drill still needed |
| Rollback | App/env/media rollback proof | Runbook exists; provider drill still needed |
| Payments | Flutterwave staging proof before live payment exposure | Direct payment foundations exist; staging proof still needed |
| Live providers | Verification, AI, media explicit-scan live calls gated | Flags must remain disabled until provider sign-off |

## Added Verification Script

New read-only checker:

```bash
python3 scripts/security/implementation_parity_phase01_check.py
```

Strict mode, for CI or final launch evidence:

```bash
python3 scripts/security/implementation_parity_phase01_check.py --strict
```

The checker:

- confirms required launch/security/runbook docs exist;
- confirms risky launch flags are not enabled in the current environment;
- confirms Django deployment verifier exists;
- confirms Nest `security:env-check` exists;
- confirms React Native launch validation scripts exist;
- reports provider evidence gaps as warnings without printing secrets.

## Safe Validation Commands

Django:

```bash
python3 manage.py check
python3 manage.py makemigrations --check --dry-run
python3 manage.py verify_deployment_security --target-production
python3 scripts/security/implementation_parity_phase01_check.py
```

Postgres-first testing rule:

- Serious backend regression testing for launch should use PostgreSQL, not SQLite, because SQLite can hide locking, JSON, transaction, migration, and concurrency behavior that production PostgreSQL will enforce.
- Use the project PostgreSQL test database/environment when running app tests.
- If PostgreSQL setup, credentials, or local service state blocks a run, record the exact blocker and move on instead of spending the phase on environment repair.

Nest:

```bash
cd "/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/Nestjs/CC_Node_Backend"
npm run security:env-check
```

React Native:

```bash
cd /Users/nigel/dev/KIS
npm run ci:launch
```

Use the Django/Nest production checks with real production-like environment variables, but never paste secret values into docs or chat.

## Phase 01 Remaining Blockers

| Priority | Blocker | Evidence needed |
|---|---|---|
| P0 | Production env values are not proven in this local session | Redacted deployment verifier output |
| P0 | Backup/restore drill not proven | Restore test evidence with timestamp, provider, database, operator |
| P0 | Rollback drill not proven | App/env/media rollback proof |
| P0 | Private media proof not captured | Owner/non-owner/public denial proof and signed-access success proof |
| P0 | Firebase/admin credential handling needs production owner evidence | Credential owner, storage path, rotation date, no raw key in repo |
| P0 | Flutterwave staging proof not captured here | Payment success/failure/cancel/duplicate/unmatched webhook proof |
| P0 | React Native real-device QA not captured here | iOS and Android launch checklist evidence |
| P0 | PostgreSQL-backed regression evidence not captured here | Run launch-critical Django tests against PostgreSQL; record blocker if local Postgres is unavailable |
| P1 | Staff-only admin/docs surfaces need deployed smoke proof | Normal user denied, staff user allowed |
| P1 | Socket.IO origins/internal signatures need deployed proof | Nest env checker plus internal call smoke proof |

## Local Validation Results

Completed on 2026-05-17.

Passed:

- `python3 -m py_compile scripts/security/implementation_parity_phase01_check.py`
- `python3 manage.py check`
- `python3 manage.py makemigrations --check --dry-run`
- `python3 scripts/security/implementation_parity_phase01_check.py`
  - 16 pass, 8 warning/evidence-needed, 0 fail.
- Current Django database engine check:
  - `django.db.backends.postgresql`
  - default database name: `kis_dev_db`
- PostgreSQL-backed focused security/media tests:
  - `python3 manage.py test apps.media.tests.PrivateMediaAccessTests apps.media.tests.MediaSafetyUploadTests --noinput --keepdb`
  - 9 tests passed.
- React Native launch typecheck:
  - `npm run typecheck:launch`
- React Native launch lint:
  - `npm run lint:launch`

Expected local production-gate failures / blockers:

- `python3 manage.py verify_deployment_security --target-production` ran in local/dev settings and reported expected production-gate failures:
  - production settings module not active;
  - `DEBUG` still true locally;
  - CSRF origins not configured for production;
  - internal production secrets/signature env values not proven;
  - HTTPS/HSTS production flags not active locally;
  - Redis/cache throttling not active locally;
  - development throttle rates detected;
  - docs staff-only behavior requires `DEBUG=False`;
  - explicit media scan production requirement not active locally;
  - one critical Phase 23 launch gate failure.
- Nest `npm run security:env-check` ran in local/dev env and reported expected production-gate failures:
  - `NODE_ENV` is not production;
  - origins are not exact HTTPS-only production origins;
  - Django internal/JWT secrets are not proven strong for production;
  - `DJANGO_TLS_INSECURE` is enabled locally;
  - internal signatures are not required locally.

These are not code regressions from Phase 01. They are the production evidence items that must be supplied from staging/production-like deployment configuration.

## Go/No-Go Rule

KIS should not go live until every P0 blocker above has explicit evidence. P1 items can only be accepted with written owner sign-off and compensating controls.

## Best Prompt For Phase 02

```text
Please implement Phase 02 of the KIS 100% Implementation and 80%+ Global Parity roadmap without using git commands. Focus on Messaging 100% Launch Reliability. Use the Phase 00 launch scope and Phase 01 security evidence to complete and prove the launch-safe messaging core: direct conversation identity, duplicate direct/subroom prevention, conversation list persistence, sender/receiver alignment after restart, fast bidirectional delivery, invisible retry, unread counts, selected-chat actions, safe media attachments through the media safety gate, and basic calls/status/update QA evidence. Prefer PostgreSQL-backed Django tests instead of SQLite for launch-critical behavior; if PostgreSQL or any test environment blocks validation, record the exact blocker and move on. Preserve existing UI behavior, do not expose secrets, run safe Django/Nest/React Native validation, record blockers in docs/implementation-parity-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 03.
```
