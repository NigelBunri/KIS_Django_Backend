# Provider Launch Readiness Checklist

Fill this after choosing the production hosting provider. Keep real IDs and
secrets in the provider console or internal incident system, not in source.

## Provider Identity

- Hosting provider: `TODO_PROVIDER_NAME`
- Region(s): `TODO_PROVIDER_REGIONS`
- Django service: `TODO_DJANGO_SERVICE`
- Nest service: `TODO_NEST_SERVICE`
- Database: `TODO_DATABASE_SERVICE`
- Redis/cache: `TODO_REDIS_SERVICE`
- Object/media storage: `TODO_MEDIA_BUCKET`
- Secret manager: `TODO_SECRET_MANAGER_PATH`
- Logs/metrics: `TODO_LOG_PROVIDER`
- Alert channel: `TODO_ALERT_CHANNEL`

## Required Evidence

Do not paste secret values, database URLs, service account JSON, private keys, or
live tokens into this file. Store real evidence in the provider console,
internal release ticket, or incident system. Reference only ticket IDs,
provider paths, owners, and non-secret command output here.

| Gate                                             | Evidence Needed                                               | Local/Code Status                                                   | Provider Evidence Status                      |
| ------------------------------------------------ | ------------------------------------------------------------- | ------------------------------------------------------------------- | --------------------------------------------- |
| Production env uses `config.settings.production` | Provider env screenshot/export metadata without secrets       | Code supports production settings and Django checks passed locally  | Evidence needed                               |
| `DEBUG=False`                                    | Verifier output from production-like env                      | Production settings force `DEBUG=False`                             | Evidence needed                               |
| Strong secrets in provider secret storage        | Secret names, owners, rotation dates, not values              | Production hard-fail checks exist                                   | Evidence needed                               |
| `INTERNAL_SIGNATURE_REQUIRED` enabled            | Django/Nest verifier output                                   | Internal signing pattern documented/implemented from earlier phases | Evidence needed                               |
| Allowed hosts/origins configured                 | Django/Nest verifier output                                   | Strict origin support exists                                        | Evidence needed                               |
| Socket.IO origins configured                     | Nest production env verification                              | Strict Socket.IO origin support exists                              | Evidence needed                               |
| Redis-backed cache/throttling active             | Verifier output and provider cache metadata                   | Django throttle config exists                                       | Evidence needed                               |
| Database backups enabled                         | Backup policy screenshot/metadata                             | Backup runbook exists                                               | Evidence needed                               |
| Restore test passed                              | Restore drill ticket/evidence                                 | Restore procedure documented                                        | Evidence needed                               |
| Rollback drill passed                            | Rollback drill ticket/evidence                                | Rollback procedure documented                                       | Evidence needed                               |
| Firebase admin key stored safely                 | Secret manager path/key owner                                 | Credential handling guide exists; local finding remains to verify   | Evidence needed                               |
| Firebase mobile key restricted                   | Firebase console restriction evidence                         | Mobile key handling guide exists                                    | Evidence needed                               |
| Private media not public                         | Bucket/CDN policy evidence and private-deny proof             | Media policy/runbook exists                                         | Evidence needed                               |
| Private-media tabletop passed                    | Ticket with owner-access, non-owner-deny, unauth-deny results | Checklist exists                                                    | Evidence needed                               |
| Dependency audit reviewed                        | Audit output and accepted risks                               | React Native launch audit is clean locally                          | Evidence needed for production release ticket |
| React Native launch CI gate passes               | `npm run ci:launch` output                                    | Passed locally on 2026-04-30                                        | Needs release-ticket attachment               |
| React Native focused regression tests pass       | `npm run test:phase5 -- ...` output                           | Passed locally on 2026-04-30 with 3 suites / 10 tests               | Needs release-ticket attachment               |
| React Native runtime QA executed                 | Device/simulator checklist evidence                           | Checklist exists                                                    | Evidence needed                               |
| Nest Firebase/Google `uuid` risk reviewed        | Reachability, controls, owner, expiry                         | Risk documented; no safe forced major override applied              | Evidence needed                               |
| Alerting routes configured                       | Alert test evidence                                           | Runbooks define escalation placeholders                             | Evidence needed                               |

## Phase 16 Launch Evidence Register

Recorded on 2026-04-30:

- Local launch gates are currently healthy:
  - Django `python3 manage.py check` passed.
  - React Native `npm run typecheck` passed.
  - React Native `npx eslint . --quiet` passed.
  - React Native `npm run ci:launch` passed with 0 production audit vulnerabilities.
  - React Native focused no-Watchman regression command passed with 3 suites and 10 tests.
  - Docs secret scan passed for the launch roadmap/checklist documents.
- Provider launch evidence is still the main blocker. The remaining work requires
  access to the actual hosting provider, Firebase console, storage provider,
  monitoring provider, backup provider, and release ticket system.

### Production Environment Evidence Needed

Record in the release ticket:

- Django service env uses `DJANGO_SETTINGS_MODULE=config.settings.production`.
- `DEBUG=False` verified by production security checker output.
- `ALLOWED_HOSTS`, Django CORS origins, CSRF origins, Nest `ORIGINS`, and
  Socket.IO origins match the real app/API/admin domains.
- Redis/cache backend is configured for throttling and not using local-memory
  cache in production.
- `INTERNAL_SIGNATURE_REQUIRED` is enabled for production internal endpoints.
- Staff-only admin/docs behavior is verified against deployed URLs.

### Firebase/Admin Credential Evidence Needed

Record in the release ticket:

- Firebase Admin service account JSON is stored in provider secret manager or a
  protected mounted secret path, not in source.
- Secret path/name, owner, and last rotation date are documented without values.
- IAM permissions are limited to the minimum needed for push notifications.
- Any service account key that appeared in local files, tickets, screenshots, or
  logs has been rotated and revoked.
- Android/mobile Firebase API key restrictions are enabled by package, SHA, and
  allowed APIs where supported.
- Staging push notification smoke test is passed before production launch.

### Nest Firebase/Google Upstream Risk Sign-Off

Open risk:

- Firebase Admin / Google Cloud transitive packages still carry upstream audit
  paths for `uuid` and `@tootallnate/once` in some audit contexts.
- A forced global major override was not applied because compatible upstream
  Firebase Admin package ranges still requested lower `uuid` majors during
  earlier review.

Required sign-off:

- Confirm Nest uses Firebase Admin only for authenticated server-side push/admin
  workflows.
- Confirm no unauthenticated client can reach Firebase Admin operations.
- Confirm Firebase Admin credentials are least-privilege and isolated.
- Attach latest Nest production audit output.
- Assign an owner and expiry date for rechecking Firebase Admin / Google Cloud
  package advisories before launch.

### Backup, Restore, Rollback, And Media Evidence Needed

Record before launch:

- Backup policy enabled with retention, encryption, and failed-backup alerting.
- Restore drill passed in staging or isolated restore environment.
- Application rollback drill passed for Django and Nest artifacts.
- Environment rollback process verified using provider secret/version history.
- Media/storage rollback tabletop passed.
- Private media proof includes:
  - unauthenticated access denied;
  - non-owner access denied;
  - owner/staff access allowed through the intended signed/proxy path;
  - public media still loads through the public path.

### React Native Runtime QA Evidence Needed

Record before launch:

- Device/simulator model, OS, app build, backend environment, tester, and date.
- Required gates output:
  - `npm run typecheck`
  - `npx eslint . --quiet`
  - `npm run ci:launch`
  - `npm run test:phase5 -- __tests__/broadcast-feeds.video-playback.test.tsx __tests__/phase5.wallet-modal.test.tsx __tests__/phase5.profile-controller.test.tsx`
- Screenshots or short recordings for:
  - health appointment/session;
  - service booking review/cancel/reschedule;
  - education management/detail;
  - market cart/order;
  - wallet verification gating;
  - broadcast video fallback.

## Phase 10 Evidence Status

Recorded on 2026-04-30:

- React Native dependency audit is clean with `npm audit --omit=dev --legacy-peer-deps`.
- React Native launch CI gate exists and passed locally with registry access:
  - `npm run ci:launch`
- React Native full strict gates failed during Phase 10, but this was superseded
  by Phases 11-15. Current Phase 16 local status:
  - `npm run typecheck` passes.
  - `npx eslint . --quiet` passes.
  - `npm run test:phase5 -- ...` passes for the focused launch regression set.
- Nest dependency audit still reports Firebase/Google upstream risk:
  - `uuid`: 3 moderate audit paths through `firebase-admin` / Google Cloud packages.
  - `@tootallnate/once`: 1 low audit path through the Firebase/Google transitive chain.

Nest Firebase/Google risk notes:

- Current KIS use is Firebase Admin push/notification credential handling. Firestore and Cloud Storage package paths are present transitively through Firebase Admin even if production reachability depends on enabled Firebase features.
- Do not force `uuid@14` globally before isolated compatibility testing. The checked compatible package paths still request lower `uuid` majors:
  - `firebase-admin@12.7.0` uses `uuid@^10.0.0`.
  - latest checked `firebase-admin@13.8.0` uses `uuid@^11.0.2`.
- Compensating controls before launch:
  - Keep Firebase Admin credentials in provider secret storage only.
  - Keep push token registration authenticated.
  - Do not expose Firebase Admin operations to unauthenticated clients.
  - Monitor Firebase Admin error logs after deploy.
  - Recheck Firebase Admin and Google Cloud SDK advisories before final launch approval.

## Launch Commands

Django:

```bash
python3 manage.py check
python3 manage.py verify_deployment_security --target-production --strict
python3 manage.py makemigrations --check --dry-run
python3 scripts/security/verify_ops_readiness.py
```

Nest:

```bash
npm run security:env-check
npm run typecheck
npm run audit:prod
```

React Native:

```bash
npm run ci:launch
npm run typecheck
npx eslint . --quiet
npm run test:phase5 -- __tests__/broadcast-feeds.video-playback.test.tsx __tests__/phase5.wallet-modal.test.tsx __tests__/phase5.profile-controller.test.tsx
```

Full local sweep:

```bash
RUN_DEPENDENCY_AUDIT=1 scripts/security/phase5_validation.sh
```

## Go / No-Go Rule

Do not launch if any of these are unresolved:

- production secrets are in files instead of provider secret storage;
- Firebase admin service account key is unrotated after exposure;
- database backups are unverified;
- restore test has never been performed;
- rollback path is unknown;
- private media bucket/path is public;
- critical/high dependency advisories are unreviewed;
- React Native `ci:launch` fails;
- production verifiers fail for non-local reasons.
