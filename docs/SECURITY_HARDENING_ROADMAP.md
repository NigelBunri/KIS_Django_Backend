# KIS Security Hardening Roadmap

This document is the durable handoff point for the KIS security hardening work across:

- Django backend: `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis`
- Nest backend: `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/Nestjs/CC_Node_Backend`
- React Native frontend: `/Users/nigel/dev/KIS`

Instruction for future agents:

- Do not use git commands unless the user explicitly changes that instruction.
- Make direct file edits only.
- If tests are blocked by existing environment or migration issues, record the blocker and continue with safe validation.
- Keep this document updated at the end of each phase.

## Launch Security Gate

| Gate                                                              | Status                                   | Evidence / Next Action                                                                                                                                                                                                                                               |
| ----------------------------------------------------------------- | ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Production config hardening is enabled                            | Complete                                 | Django production settings hard-fail weak production config. Nest hard-fails unsafe production config.                                                                                                                                                               |
| Strong secrets are set in production                              | Not verified                             | Code enforces strong secrets, but real deployed secret values must be verified in the hosting provider. Do not paste secrets into prompts.                                                                                                                           |
| `DEBUG=False`                                                     | Complete in code                         | `config/settings/production.py` forces `DEBUG = False`. Deployment must use `DJANGO_SETTINGS_MODULE=config.settings.production`.                                                                                                                                     |
| Real `ALLOWED_HOSTS`, HTTP CORS, and Socket.IO origins configured | Partial                                  | Strict code paths exist. Real production env values still need deployment verification.                                                                                                                                                                              |
| Admin/docs are not public                                         | Mostly complete                          | Django OpenAPI docs are staff-only outside debug. Deployed `/admin/`, `/control/admin/`, `/api/docs/`, and `/api/schema/` still need smoke verification.                                                                                                             |
| High-risk IDOR endpoints are locked                               | Partial                                  | Some apps are scoped, but analytics, tiers, billing, health_ops, partners, AI, and admin-like endpoints still need systematic object-level tests.                                                                                                                    |
| Tokens are not placed in URLs                                     | In progress                              | 2026-04-30 removed the known Bible certificate bearer-token query-string flow. Continue scanning for token-in-URL patterns.                                                                                                                                          |
| Private media is not publicly exposed                             | Mostly complete in code                  | Django explicit private media is owner/staff-only and supports short-lived signed downloads. Nest no longer serves `/uploads/` in production unless `SERVE_UPLOADS_PUBLICLY=1`. Real deployment must set env values and migrate private files out of public storage. |
| Internal service trust uses replay-resistant signatures           | Mostly complete in code                  | Django and Nest now support HMAC-signed internal calls with timestamp and nonce replay checks. Production must set `INTERNAL_SIGNATURE_REQUIRED=1/True` and keep strong internal tokens.                                                                             |
| Login/register/OTP/password reset throttling is active            | Complete in code                         | Scoped DRF throttle rates exist. Production Redis/cache behavior still needs verification.                                                                                                                                                                           |
| Basic security logging is active                                  | Partial                                  | Redaction and audit foundations exist. Alerting/SIEM export is not yet proven.                                                                                                                                                                                       |
| Database backups are configured                                   | Runbook complete / provider not verified | Database backup and restore runbook exists. Real provider backup schedule, retention, and restore test still need production verification.                                                                                                                           |
| Rollback plan exists                                              | Runbook complete / drill not verified    | Application, environment, media, and database rollback runbooks exist. A real staging rollback drill still needs to be performed.                                                                                                                                    |

## Phase 0: Durable Tracking And Known Leak Removal

Goal:

- Create a durable security status document.
- Remove the known token-in-URL frontend flow.
- Preserve enough context for future model handoff.

Completed on 2026-04-30:

- Added this roadmap/status document.
- Removed bearer token from the Bible certificate download URL in the React Native app.
- Certificate downloads now use the existing `Authorization: Bearer ...` header only.

Validation:

- `rg` found no remaining `certificateToken`, `setCertificateToken`, `certificateFetchUrl`, or certificate `token=` query construction in `src/components/Bible/BibleCourseDetailSheet.tsx`.
- `python3 manage.py check` passed.
- `DJANGO_SETTINGS_MODULE=config.settings.production python3 manage.py check --deploy` correctly failed closed because the local `.env` does not provide a production-strength `SECRET_KEY`. This is expected locally and confirms the production hard-fail path is active.
- `python3 manage.py check --deploy` under local settings is blocked by an existing drf-spectacular schema error in `PatientHealthSummarySerializer` plus local deployment warnings.
- `npx tsc --noEmit --pretty false` is blocked by existing unrelated frontend TypeScript errors in education, broadcast market/feed, health service session, market cart/order/shop screens.

Remaining risk:

- Other token-in-URL patterns may exist outside the certificate flow.
- Header-only download behavior should be verified on device/simulator against the Django certificate endpoint.
- Full frontend typecheck is not clean yet; the certificate change itself is narrow but should be verified when the broader frontend type errors are addressed.

## Phase 1: Deployment Environment Verification

Goal:

- Prove the deployed environment uses secure production settings without exposing secrets.

Tasks:

- Add a deployment checklist that confirms `DJANGO_SETTINGS_MODULE=config.settings.production`.
- Verify production has strong values for `SECRET_KEY`, `JWT_SECRET`, `DJANGO_INTERNAL_TOKEN`, `NEST_INTERNAL_TOKEN`, `DJANGO_JWT_SECRET`, Firebase credentials, payment secrets, and database URL.
- Verify `DEBUG=False`.
- Verify production `ALLOWED_HOSTS`.
- Verify Django CORS and CSRF origins.
- Verify Nest `ORIGINS`.
- Verify Socket.IO accepts only configured production origins.
- Verify Redis/cache is used for throttling in production.
- Verify `/api/schema/`, `/api/docs/`, `/api/docs/swagger/`, and `/api/docs/redoc/` require staff login outside debug.

Suggested validation commands:

```bash
python3 manage.py check --deploy
python3 manage.py check
python3 manage.py verify_deployment_security --target-production
python3 manage.py verify_deployment_security --target-production --strict
cd "/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/Nestjs/CC_Node_Backend"
npm run security:env-check
```

Completed on 2026-04-30:

- Added Django safe deployment verifier:
  - `apps/core/management/commands/verify_deployment_security.py`
- Added Nest safe production environment verifier:
  - `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/Nestjs/CC_Node_Backend/scripts/verify-production-env.js`
- Added Nest package script:
  - `npm run security:env-check`
- Updated Django `.env.example` with:
  - `DJANGO_SETTINGS_MODULE=config.settings.production`
  - explicit optional `CORS_ALLOWED_ORIGINS`
- Updated `docs/DEPLOYMENT_SECURITY_LAUNCH_GATE.md` with safe verification commands and current local blockers.

Validation:

- `python3 manage.py check` passed.
- `python3 -m py_compile apps/core/management/commands/verify_deployment_security.py` passed.
- `node --check scripts/verify-production-env.js` passed.
- `python3 manage.py verify_deployment_security --target-production` ran without exposing secret values and reported expected local production-gate failures.
- `node scripts/verify-production-env.js` ran without exposing secret values and reported expected local Nest production-gate failures.

Current local production-gate failures:

- Django local settings are not using `config.settings.production`.
- Django local `DEBUG` is enabled.
- Django local `CSRF_TRUSTED_ORIGINS` is empty.
- Django local HTTPS security flags are not production-enabled.
- Django local HSTS is disabled.
- Django local cache is not Redis-backed.
- Django local throttle rates are development-friendly.
- Django docs are not staff-only while local `DEBUG=True`.
- Nest local `NODE_ENV` is not production.
- Nest local origins are not HTTPS-only.
- Nest local shared secrets are weak/development values.
- Nest local `DJANGO_TLS_INSECURE` is enabled.

Remaining risk:

- These verifiers prove configuration shape only. They do not verify hosting-provider backups, deployed WAF/CDN, real production secret storage permissions, or deployed URL smoke behavior.
- Staff-only docs/admin behavior still needs a deployed smoke test against `/api/schema/`, `/api/docs/`, `/api/docs/swagger/`, `/api/docs/redoc/`, `/admin/`, and `/control/admin/`.
- Phase 2 must address the larger IDOR/object-access risk.

## Phase 2: High-Risk IDOR And Object Access

Goal:

- Stop users from reading or mutating data they do not own.

Priority apps:

- `apps.analytics`
- `apps.tiers`
- `apps.billing`
- `apps.health_ops`
- `apps.partners`
- `apps.ai_integration`
- `apps.events`
- `admin_control`

Tasks:

- Replace broad `Model.objects.all()` API surfaces with scoped `get_queryset()` methods.
- Use owner, organization, tenant, membership, role, or staff-only access rules.
- Add tests proving user A cannot list, retrieve, update, delete, or action user B data.
- Reduce serializer exposure for sensitive admin, analytics, billing, health, token, and internal fields.

Completed on 2026-04-30:

- Hardened `apps.analytics`:
  - Platform analytics/config surfaces (`Metric`, `EventStream`, `Dashboard`, `AppSetting`, `FeatureFlag`, `Alert`, `EngagementScore`) are staff-only.
  - Healthcare analytics surfaces are scoped to organizations/profiles/patients owned by the requesting user, while staff can still see all.
  - Added regression tests for clinical report ownership and staff-only metrics.
- Hardened `apps.tiers`:
  - Shadow tier users and organizations are staff-only.
  - `UserSerializer` no longer exposes `password_hash`.
  - Subscriptions, usage quotas, invoices, and quantum settings are scoped to `owner_type="user"` and the requesting user's ID for non-staff users.
  - Plan/feature reference data remains authenticated read-only, staff-write.
  - Partner settings, impact settings, campaigns, tickets, and hologram settings are staff-only until org ownership is modeled safely.
  - Added regression tests for user-owned subscriptions, staff visibility, and password hash masking.
- Hardened `apps.ai_integration`:
  - AI jobs are scoped by `triggered_by`.
  - Translation requests are scoped through their job owner.
  - QnA sessions are scoped by `user_id`.
  - AI feedback is scoped by `user_id` or job ownership.
  - AI pipelines and schedules are staff-only.
  - AI models are authenticated read-only, staff-write.
  - New AI records now stamp user IDs instead of usernames where safe.
  - Added regression tests for AI job/session ownership and staff-only pipelines.

Validation:

- `python3 manage.py check` passed.
- `python3 -m py_compile apps/analytics/views.py apps/tiers/views.py apps/ai_integration/views.py apps/analytics/tests.py apps/tiers/tests.py apps/ai_integration/tests.py` passed.
- `python3 manage.py test apps.analytics.tests.AnalyticsAccessBoundaryTests apps.tiers.tests.TiersAccessBoundaryTests apps.ai_integration.tests.AIIntegrationAccessBoundaryTests --noinput` passed: 9 tests.

Known notes:

- `apps.tiers` routes overlap with earlier `/api/v1/users/` and `/api/v1/subscriptions/` routes from other apps. The hardened tiers viewsets are tested directly because the current URL order does not reliably exercise them at those paths.
- A follow-up should either namespace tier routes or remove unused shadow routes to avoid ambiguity.

Remaining Phase 2 risk:

- `apps.events` still has attendance/ticketing flows needing owner/buyer/event-owner scoping.
- `apps.billing` serializer relationships and payment/claim endpoints need deeper ownership tests.
- `apps.health_ops` is very large and still needs a systematic endpoint-by-endpoint permission sweep.
- `apps.partners` still needs partner membership/owner-level regression tests.
- `admin_control` querysets should be verified against admin roles and staff-only access.

## Phase 3: Private Media And Upload Exposure

Goal:

- Prevent private files from being publicly reachable.

Tasks:

- Define public vs private media policy.
- Stop serving private uploads directly from Nest `/uploads/`.
- Move private media behind signed short-lived URLs or authenticated proxy endpoints.
- Add malware scanning hook or quarantine state for uploads.
- Add tests for private file denial without auth and denial for non-owners.

Media policy added on 2026-04-30:

- Media is treated as public only when it is a legacy ready asset with no explicit private marker, or when upload metadata/storage declares `visibility="public"`.
- Media is treated as private when `storage`, `metadata`, `security`, or `access_policy.rules` declares `visibility`, `access`, or `privacy` as `private`, `restricted`, `owner`, `authenticated`, or `tenant`, or when `private=true`.
- Private media must be fetched by the owner/staff user or by a short-lived signed media URL. Access tokens must not be placed in media URLs.
- New Django upload responses default to private visibility unless the client explicitly sends `visibility=public`. Local debug still returns `localUrl` to avoid breaking development previews.
- Nest upload responses are private by default in production because `/uploads/` static serving is disabled unless `SERVE_UPLOADS_PUBLICLY=1`.
- Malware scanning is represented as a hook/quarantine state through `UPLOAD_SCAN_REQUIRED`. A real scanner worker still needs to be connected before treating `pending` files as fully cleared.

Completed on 2026-04-30:

- Hardened Django media access in `apps/media/views.py`:
  - Explicit private assets are hidden from anonymous/non-owner asset lists.
  - Private downloads deny anonymous and non-owner users.
  - Owners/staff can create short-lived signed download URLs through `/api/v1/assets/<id>/sign/`.
  - Signed downloads use `/api/v1/assets/<id>/download/?token=...` and `MEDIA_SIGNED_URL_TTL_SECONDS`, defaulting to 300 seconds.
  - Private download responses set `Cache-Control: private, max-age=0, no-store`.
  - Upload responses now include `visibility`, `private`, `scanStatus`, and `quarantined`.
- Hardened Nest upload exposure:
  - `src/main.ts` no longer registers public static `/uploads/` in production by default.
  - `SERVE_UPLOADS_PUBLICLY=1` is required to deliberately expose local uploads in production.
  - `GET /uploads/file?key=...` is protected by the existing `HttpAuthGuard` and serves local files through an authenticated proxy.
  - Upload responses include `downloadUrl`, `publicUrl`, `visibility`, `private`, `scanStatus`, and `quarantined`.
  - Local file path resolution rejects traversal outside `UPLOADS_DIR`.
- Updated React Native chat upload adapter:
  - Preserves `downloadUrl`, `publicUrl`, `private`, and `scanStatus` in attachment metadata.
- Updated env examples:
  - Django: `MEDIA_SIGNED_URL_TTL_SECONDS`, `UPLOAD_SCAN_REQUIRED`.
  - Nest: `SERVE_UPLOADS_PUBLICLY`, `UPLOAD_SCAN_REQUIRED`.
- Added focused Django private media regression tests:
  - Anonymous private download is denied.
  - Non-owner private download is denied.
  - Owner can create a signed URL and download with it.
  - Anonymous asset list hides explicitly private media.

Validation:

- `python3 manage.py check` passed.
- `python3 -m py_compile apps/media/views.py apps/media/tests.py` passed.
- `python3 manage.py test apps.media.tests.PrivateMediaAccessTests --noinput --keepdb` passed: 4 tests.
- `npx prettier --check src/main.ts src/uploads/uploads.controller.ts src/storage/local-storage.service.ts` passed after formatting the touched Nest files.

Blocked validation:

- `python3 manage.py test apps.media.tests.PrivateMediaAccessTests --noinput` without `--keepdb` stalled while destroying/creating the existing local test database. The focused suite passed with `--keepdb`.
- Nest `npx tsc --noEmit --pretty false` is blocked locally by an existing sandbox write error to `dist/tsconfig.tsbuildinfo` and existing Jest global type errors in `src/app.controller.spec.ts` / `test/app.e2e-spec.ts`.
- A focused Nest TypeScript compile against the touched files is still blocked by existing `FastifyRequest.principal` typing errors in `src/request.helpers.ts` and `src/scopes.guard.ts`.

Remaining Phase 3 risk:

- Existing files already stored under a public bucket/domain must be migrated or reclassified; code changes do not move old files.
- The authenticated Nest download proxy currently proves auth, but it does not yet enforce per-file owner/conversation membership because local upload keys are not tied to a durable owner record in Nest. Phase 4/5 should bind upload metadata to user/conversation ownership.
- A real antivirus/malware scanning worker is not connected yet. `UPLOAD_SCAN_REQUIRED` currently marks files as pending/quarantined for integration.
- Media components that render authenticated URLs should be tested on device/simulator, especially React Native image/video surfaces that may not send auth headers automatically.

## Phase 4: Internal Service Trust

Goal:

- Harden Django-to-Nest and Nest-to-Django trust.

Tasks:

- Keep strong internal tokens.
- Add signed internal request headers.
- Add timestamp and nonce replay protection.
- Log failed internal auth attempts.
- Prefer private networking or mTLS in production.

Completed on 2026-04-30:

- Added Django internal request signing support:
  - `apps/chat/internal_signing.py` signs/verifies `X-Internal-Timestamp`, `X-Internal-Nonce`, and `X-Internal-Signature`.
  - Signature payload covers method, path/query, timestamp, nonce, and canonical body hash.
  - Nonces are stored in Django cache for replay prevention.
  - `INTERNAL_SIGNATURE_REQUIRED=True` makes token-only internal calls fail.
  - Local/dev can still allow legacy token-only calls by setting `INTERNAL_SIGNATURE_REQUIRED=0`.
- Hardened Django internal auth:
  - `apps/chat/internal_auth.py` uses constant-time token comparison.
  - Failed internal auth is logged through `security.internal_auth` without secret values.
  - Legacy token-only fallback is logged when allowed.
- Signed Django-to-Nest internal calls:
  - `apps/chat/tasks.py`
  - `apps/broadcasts/views.py`
- Added Nest internal request signing support:
  - `src/security/internal-signing.ts` signs/verifies HMAC internal calls.
  - Nest internal guard rejects replayed nonces and stale timestamps when `INTERNAL_SIGNATURE_REQUIRED=1`.
  - Failed internal auth is logged without secret values.
- Signed Nest-to-Django internal calls:
  - Django token introspection in `src/auth/django-auth.service.ts`
  - sequence allocation in `src/chat/integrations/django/django-seq.client.ts`
  - conversation permissions, read-state updates, member IDs, policy checks, and webhook dispatch in `src/chat/integrations/django/django-conversation.client.ts`
- Updated safe production verifiers:
  - Django `verify_deployment_security` now checks `INTERNAL_SIGNATURE_REQUIRED` and timestamp window.
  - Nest `scripts/verify-production-env.js` now checks `INTERNAL_SIGNATURE_REQUIRED`, timestamp window, and guard wiring.
- Updated env examples:
  - Django: `INTERNAL_SIGNATURE_REQUIRED=True`, `INTERNAL_SIGNATURE_MAX_SKEW_SECONDS=300`.
  - Nest: `INTERNAL_SIGNATURE_REQUIRED=1`, `INTERNAL_SIGNATURE_MAX_SKEW_SECONDS=300`.
- Added focused Django regression tests:
  - signed internal request is accepted in strict mode;
  - replayed nonce is rejected;
  - legacy token-only request is rejected in strict mode;
  - existing local legacy behavior is preserved when strict mode is disabled.

Validation:

- `python3 manage.py check` passed.
- `python3 -m py_compile apps/chat/internal_signing.py apps/chat/internal_auth.py apps/chat/tasks.py apps/chat/tests.py apps/broadcasts/views.py apps/core/management/commands/verify_deployment_security.py` passed.
- `python3 manage.py test apps.chat.tests.ConversationUnreadContractTests.test_internal_update_read_state_advances_monotonically apps.chat.tests.ConversationUnreadContractTests.test_strict_internal_auth_accepts_signed_request_and_rejects_replay apps.chat.tests.ConversationUnreadContractTests.test_strict_internal_auth_rejects_legacy_token_only_request apps.chat.tests.ConversationUnreadContractTests.test_pending_direct_recipient_cannot_send_via_ws_perms --noinput --keepdb` passed: 4 tests.
- `node --check scripts/verify-production-env.js` passed.
- Focused Nest TypeScript validation passed:
  - `npx tsc --noEmit --pretty false --incremental false --types node --module commonjs --target ES2021 --experimentalDecorators --emitDecoratorMetadata --esModuleInterop src/security/internal-signing.ts src/auth/internal-auth.guard.ts src/auth/django-auth.service.ts src/chat/integrations/django/django-seq.client.ts src/chat/integrations/django/django-conversation.client.ts`
- `npx prettier --check src/security/internal-signing.ts src/auth/internal-auth.guard.ts src/auth/django-auth.service.ts src/chat/integrations/django/django-seq.client.ts src/chat/integrations/django/django-conversation.client.ts scripts/verify-production-env.js` passed after formatting.
- Safe production verifiers ran and correctly reported local environment blockers without printing secrets:
  - Django verifier: 5/17 checks passing locally.
  - Nest verifier: 9/15 checks passing locally.

Blocked / expected-local validation:

- Full Nest `npx tsc --noEmit --pretty false` remains blocked by:
  - sandbox write denial for `dist/tsconfig.tsbuildinfo`;
  - existing missing Jest globals in `src/app.controller.spec.ts` and `test/app.e2e-spec.ts`.

Remaining Phase 4 risk:

- Production must explicitly set `INTERNAL_SIGNATURE_REQUIRED=1` / `True`; local `.env` currently fails the launch gate.
- This improves application-layer internal auth but does not replace private networking, mTLS, security groups/firewall rules, or provider-side service identity.
- Nonce replay protection depends on shared cache quality. Production should use Redis-backed Django cache and avoid multi-instance in-memory nonce stores for Nest, or move Nest nonce storage to Redis.
- Worker services outside the inspected Django/Nest paths should adopt the same signing scheme before being allowed to call internal endpoints.

## Phase 5: CI, Dependency, And Migration Safety

Goal:

- Prevent security regressions.

Tasks:

- Add Django checks/tests.
- Add Nest typecheck/tests.
- Add React Native lint/tests.
- Add dependency audits.
- Add secret scanning.
- Add migration dry-run checks.
- Document what to do when tests are blocked.

Completed on 2026-04-30:

- Added CI-style validation runner:
  - `scripts/security/phase5_validation.sh`
  - Runs Django checks, production verifier, migration dry run, focused security tests, Nest focused typecheck/format checks, React Native targeted lint/typecheck, optional dependency audits, and secret scanning.
  - Continues after failures and prints a pass/fail/skip summary for handoff.
- Added dependency-free secret scanner:
  - `scripts/security/secret_scan.py`
  - Reports only path, line, and rule name. It does not print matched secret values.
  - Excludes generated/dependency directories and avoids scanning its own detection patterns.
- Added validation runbook:
  - `docs/SECURITY_VALIDATION_RUNBOOK.md`
  - Documents local sweep, heavier optional checks, migration dry-run expectations, dependency audits, and production launch gates.
- Added package scripts:
  - Nest: `audit:prod`, `typecheck`, `lint:ci`.
  - React Native: `audit:prod`, `typecheck`, `lint:ci`.
- Improved dependency audit handling:
  - Phase runner uses `pnpm audit --prod` for Nest when `pnpm-lock.yaml` exists.
  - React Native uses `npm audit --omit=dev`.

Validation run on 2026-04-30:

- `bash -n scripts/security/phase5_validation.sh` passed.
- `python3 -m py_compile scripts/security/secret_scan.py` passed.
- `npx prettier --check package.json` passed in Nest.
- `npx prettier --check package.json` passed in React Native.
- Full Phase 5 safe sweep ran:
  - `scripts/security/phase5_validation.sh`
  - Result: 8 pass, 4 fail, 5 skipped optional checks.

Phase 5 sweep passes:

- Django system check passed.
- Django migration dry run passed with `No changes detected`; local Postgres consistency check emitted an operation-permitted warning but did not block dry-run detection.
- Django security helper compile passed.
- Django focused security tests passed: 6 tests.
- Nest production verifier syntax passed.
- Nest focused typecheck passed for the security/upload touched files.
- Nest formatting check passed.
- React Native targeted lint passed for `src/Module/ChatRoom/uploadFileToBackend.ts`.

Phase 5 sweep failures / blockers:

- Django production verifier fails locally as expected because local `.env` is not production:
  - not using production settings module;
  - `DEBUG` enabled;
  - missing CSRF trusted origins;
  - weak/missing local JWT/internal/Nest internal production secrets;
  - `INTERNAL_SIGNATURE_REQUIRED` not enabled locally;
  - production HTTPS/HSTS/Redis/throttle/docs gates not active locally.
- Nest production verifier fails locally as expected because local Nest env is not production:
  - `NODE_ENV` not production;
  - origins are not HTTPS-only;
  - local shared secrets are weak/development values;
  - `DJANGO_TLS_INSECURE` enabled;
  - `INTERNAL_SIGNATURE_REQUIRED` not enabled locally.
- React Native project-wide typecheck fails from existing unrelated frontend type errors in:
  - education discover;
  - broadcast feeds;
  - broadcast market;
  - health institution/service session;
  - market cart/orders/shop;
  - broadcast tab props.
- Secret exposure scan found four potential exposure locations without printing values:
  - Django `.env` line 47: `google_api_key`;
  - Nest `config/firebase-adminsdk.json` line 5: private key block / Firebase service account private key;
  - React Native `android/app/google-services.json` line 18: `google_api_key`.

Dependency audit results:

- Nest `npm audit --omit=dev` is blocked because the Nest repo has no `package-lock.json`.
- Nest `pnpm audit --prod` ran and found 42 production advisories: 1 critical, 19 high, 19 moderate, 3 low.
  - Notable families include `@fastify/middie`, `glob`, `fastify`, `@isaacs/brace-expansion`, `axios`, `minimatch`, `@fastify/static`, `uuid`, `@nestjs/core`, `qs`, and transitive Firebase/Google packages.
  - `package.json` already contains some overrides, but the current lockfile still resolves vulnerable versions and needs a controlled lockfile refresh.
- React Native `npm audit --omit=dev` ran and found 14 production advisories: 7 critical, 2 high, 4 moderate, 1 low.
  - Notable families include `fast-xml-parser`, `minimatch`, `picomatch`, `brace-expansion`, `js-yaml`, `qs`, and `yaml`.
  - Some fixes require lockfile refresh or React Native CLI patch-level updates.

Remaining Phase 5 risk:

- Optional full suites were intentionally skipped by default to avoid hiding the focused results behind known baseline noise. Run with `RUN_FULL_TESTS=1` when ready.
- Dependency audits were run manually. Use `RUN_DEPENDENCY_AUDIT=1 scripts/security/phase5_validation.sh` after lockfile hygiene work.
- The secret scanner flags local credential files. Real production credentials must live in a secret manager or provider secret storage, and any exposed private key should be rotated.
- The React Native project-wide type baseline must be cleaned before typecheck can become a reliable CI gate.

## Phase 6: Backups, Rollback, And Operations

Goal:

- Make production recoverable.

Tasks:

- Document database backup schedule.
- Document restore test process.
- Document app rollback process.
- Document environment rollback process.
- Add security incident runbook.

Completed on 2026-04-30:

- Added operational runbook index:
  - `docs/operations/PRODUCTION_OPERATIONS_OVERVIEW.md`
- Added database backup and restore runbook:
  - `docs/operations/DATABASE_BACKUP_RESTORE_RUNBOOK.md`
  - Covers backup policy, pre-deploy backup checklist, restore tests, emergency restore, bad-migration recovery, and evidence capture.
- Added application and environment rollback runbook:
  - `docs/operations/APPLICATION_ROLLBACK_RUNBOOK.md`
  - Covers rollback triggers, Django rollback, Nest rollback, React Native rollback, environment rollback, post-rollback checks, and evidence capture.
- Added media/storage recovery runbook:
  - `docs/operations/MEDIA_STORAGE_RECOVERY_RUNBOOK.md`
  - Covers media policy, object backup/versioning, restore tests, accidental public exposure, corrupted upload recovery, and media rollback.
- Added secret rotation runbook:
  - `docs/operations/SECRET_ROTATION_RUNBOOK.md`
  - Covers planned and emergency rotation for Django, JWT, internal tokens, Firebase, payment, SMS, AI, Redis, database, and object-storage secrets.
- Added security incident response runbook:
  - `docs/operations/SECURITY_INCIDENT_RESPONSE_RUNBOOK.md`
  - Covers severity levels, first 15 minutes, investigation checklist, containment playbooks, communication, recovery, and post-incident review.
- Added operational readiness verifier:
  - `scripts/security/verify_ops_readiness.py`
  - Checks runbook presence and required sections without connecting to production or reading secrets.

Validation:

- `python3 -m py_compile scripts/security/verify_ops_readiness.py` passed.
- `python3 scripts/security/verify_ops_readiness.py` passed: 8/8 checks.
- `python3 scripts/security/secret_scan.py --root docs/operations --root scripts/security` passed with no findings.
- `python3 manage.py check` passed.

Remaining Phase 6 risk:

- Real provider backup schedule is still not verified.
- A restore test has not yet been performed against the production provider backup.
- A staging rollback drill has not yet been performed.
- Provider-specific commands/IDs remain placeholders until the hosting provider is finalized.
- Secret rotation procedures are documented, but actual exposed/local credential material from Phase 5 still needs rotation/removal before production.

Recommended next operational drills:

- Fill provider placeholders in `docs/operations/PRODUCTION_OPERATIONS_OVERVIEW.md`.
- Run one staging database restore test.
- Run one staging Django/Nest rollback drill.
- Run one Firebase service account rotation drill.
- Run one private-media exposure tabletop exercise.

## Phase 7: Launch Blocker Closure Planning

Goal:

- Close or explicitly track the highest-risk remaining launch blockers without deleting or rotating real credentials without approval.

Tasks:

- Document production secret exposure cleanup.
- Document Firebase admin credential handling.
- Document dependency audit remediation plan.
- Triage React Native typecheck baseline.
- Document provider-specific production launch readiness.
- Add safe verification checks.

Completed on 2026-04-30:

- Added Phase 7 launch blocker register:
  - `docs/operations/PHASE7_LAUNCH_BLOCKER_REGISTER.md`
  - Tracks secret exposure, Firebase admin handling, dependency advisories, React Native typecheck debt, and provider readiness.
- Added Firebase credential handling guide:
  - `docs/operations/FIREBASE_CREDENTIAL_HANDLING.md`
  - Separates admin service account handling from React Native mobile Firebase config and lists safe rotation/restriction steps.
- Added dependency remediation plan:
  - `docs/operations/DEPENDENCY_REMEDIATION_PLAN.md`
  - Captures Nest and React Native audit counts, priority package families, safe lockfile refresh process, and risk acceptance requirements.
- Added React Native typecheck triage:
  - `docs/operations/REACT_NATIVE_TYPECHECK_TRIAGE.md`
  - Groups current typecheck failures by education, broadcast feeds, broadcast market, health, market, and broadcast tabs.
- Added provider launch readiness checklist:
  - `docs/operations/PROVIDER_LAUNCH_READINESS_CHECKLIST.md`
  - Lists provider identity placeholders, evidence requirements, launch commands, and go/no-go rules.
- Added Phase 7 readiness verifier:
  - `scripts/security/verify_phase7_readiness.py`
  - Checks Phase 7 artifacts and required sections without reading or rotating secrets.

Validation:

- `python3 -m py_compile scripts/security/verify_phase7_readiness.py` passed.
- `python3 scripts/security/verify_phase7_readiness.py` passed: 7/7 checks.
- `python3 scripts/security/secret_scan.py --root docs/operations --root scripts/security` passed with no findings.
- `python3 manage.py check` passed.

Remaining Phase 7 launch blockers:

- Real/local credentials flagged in Phase 5 still need owner review and rotation/removal before production:
  - Django `.env` Google API key pattern;
  - Nest Firebase admin service account JSON;
  - React Native Android Firebase mobile config API key restrictions.
- Nest production dependency audit still has unresolved production advisories.
- React Native production dependency audit still has unresolved production advisories.
- React Native project-wide typecheck remains blocked by existing domain-specific type errors.
- Provider-specific launch evidence is still placeholder-only until hosting provider values are filled.
- Database restore, rollback, Firebase key rotation, and private-media tabletop drills still need to be performed.

## Phase 8: Dependency Audit Remediation

Goal:

- Reduce production dependency advisory risk using safe patch/minor lockfile updates and explicit remaining-risk tracking.

Tasks:

- Start with Nest production advisories.
- Prefer package-manager overrides and patch/minor updates.
- Avoid destructive commands, forced major upgrades, and credential rotation/deletion.
- Run focused audit, typecheck, formatting, and Django validation checks.
- Attempt React Native remediation after Nest.

Completed on 2026-04-30:

- Refreshed the Nest `pnpm-lock.yaml` to apply existing overrides.
- Updated Nest `fastify` from `5.7.3` to `5.8.5`.
- Added narrow Nest `pnpm.overrides` for patched transitive runtime packages:
  - `ajv@8.18.0`
  - `body-parser@2.2.1`
  - `follow-redirects@1.16.0`
  - `multer@2.1.1`
  - `path-to-regexp@8.4.2`
  - `socket.io-parser@4.2.6`
- Formatted the updated Nest package and lockfile.
- Attempted React Native lockfile remediation using npm lockfile-only commands, but npm resolution stalled and was stopped.
- Updated the dependency remediation plan with measured Phase 8 results and remaining blockers.

Validation:

- Nest `pnpm audit --prod` now reports 7 production advisories: 1 high, 5 moderate, 1 low.
- Nest `npx prettier --check package.json pnpm-lock.yaml` passed.
- Nest focused TypeScript check for security/internal auth, Django chat integration, uploads, and local storage files passed.
- React Native `npm audit --omit=dev` still reports 14 production advisories: 7 critical, 2 high, 4 moderate, 1 low.
- React Native lockfile inspection still shows vulnerable resolved packages including `fast-xml-parser@4.5.3`, `brace-expansion@1.1.12`, `js-yaml@3.14.1`, `picomatch@2.3.1`, `qs@6.13.0`, and `yaml@2.8.1`.
- Django `python3 manage.py check` passed.

Remaining Phase 8 risks:

- Nest still has unresolved `lodash` advisories through `@nestjs/config`.
- Nest still has Firebase/Google transitive `uuid` and `@tootallnate/once` advisories.
- React Native lockfile remediation is blocked until npm lockfile resolution completes successfully in a clean environment.
- React Native `fast-xml-parser` advisories remain critical and should stay launch-blocking until fixed or formally accepted with compensating controls.
- React Native full typecheck baseline remains a separate blocker from Phase 7.

## Phase 9: Remaining Dependency Launch Blockers

Goal:

- Finish the safe dependency launch-blocker pass without forced major upgrades or destructive commands.

Tasks:

- Confirm the compatible remediation path for Nest `lodash` through `@nestjs/config`.
- Confirm whether Firebase/Google `uuid` and `@tootallnate/once` advisories can be safely removed with patch/minor updates.
- Resolve the React Native lockfile refresh blocker.
- Apply compatible React Native CLI patch updates.
- Re-run production audits and focused validation.

Completed on 2026-04-30:

- Updated Nest `@nestjs/config` from `^4.0.2` to `^4.0.4`.
- Added Nest `lodash@4.18.1` override.
- Refreshed the Nest `pnpm-lock.yaml`.
- Confirmed `@nestjs/config@4.0.4` depends on `lodash@4.18.1`, clearing the Nest lodash advisories.
- Confirmed `firebase-admin@12.7.0` still depends on `uuid@^10.0.0`.
- Confirmed latest `firebase-admin@13.8.0` still depends on `uuid@^11.0.2`, so the `uuid>=14` advisory cannot be removed through a safe Firebase patch/minor update.
- Corrected React Native override `fast-xml-parser` from unavailable `5.6.1` to available `5.7.2`.
- Updated React Native CLI packages to `^20.1.3`:
  - `@react-native-community/cli`
  - `@react-native-community/cli-platform-android`
  - `@react-native-community/cli-platform-ios`
- Added React Native `lodash@4.18.1` override.
- Refreshed React Native `package-lock.json` with `--legacy-peer-deps` because npm 11 otherwise fails on an existing React/React DOM peer conflict.
- Updated the dependency remediation plan with Phase 9 results and the remaining upstream Nest risk.

Validation:

- Nest `pnpm audit --prod` now reports 4 production advisories: 3 moderate `uuid` paths and 1 low `@tootallnate/once` path.
- Nest `npx prettier --check package.json pnpm-lock.yaml` passed.
- Nest focused TypeScript check for security/internal auth, Django chat integration, uploads, and local storage files passed.
- React Native `npm audit --omit=dev --legacy-peer-deps` passed with 0 vulnerabilities.
- React Native `npx prettier --check package.json package-lock.json` passed.
- React Native `npm run typecheck` still fails on the existing application type baseline.
- React Native `npm run lint:ci` still fails on the existing application lint baseline: 111 errors and 4415 warnings.
- Django `python3 manage.py check` passed.

Remaining Phase 9 risks:

- Nest retains Firebase/Google upstream advisories that cannot be cleared safely without either upstream package movement or a carefully isolated forced `uuid@14` compatibility test.
- React Native dependency advisories are clear, but typecheck and lint remain launch-readiness blockers.
- React Native npm commands required `--legacy-peer-deps` because of an existing React/React DOM peer conflict involving `react-native-country-picker-modal`.
- Provider-specific launch evidence, credential review/rotation, restore drill, rollback drill, Firebase key rotation drill, and private-media tabletop drill still remain open from earlier phases.

## Phase 10: Launch Readiness Baseline Gates

Goal:

- Keep dependency audits green while creating a realistic launch CI gate and documenting the remaining full React Native type/lint baseline.

Tasks:

- Triage React Native typecheck and lint failures.
- Apply the smallest high-signal code fix that does not change user-facing flows.
- Add a bounded launch CI gate that can pass while strict historical baselines remain visible.
- Document remaining Nest Firebase/Google `uuid` risk with reachability and compensating controls.
- Update provider launch evidence requirements.

Completed on 2026-04-30:

- Added React Native launch scripts:
  - `ci:launch`
  - `typecheck:launch`
  - `lint:launch`
  - `lint:strict`
- Added React Native `tsconfig.launch.json` for scoped launch typechecking of stable security/storage/API service files.
- Fixed one true React hook-order violation in `src/screens/broadcast/market/pages/ShopServicesPage.tsx` by removing an unnecessary `useMemo` below an early return.
- Kept full strict commands in place:
  - `npm run typecheck`
  - `npm run lint:ci`
- Updated React Native typecheck triage documentation with the Phase 10 launch gate and remaining strict baseline.
- Updated provider launch readiness checklist with:
  - React Native launch CI evidence requirement.
  - strict type/lint baseline review requirement.
  - Nest Firebase/Google `uuid` risk evidence requirement.
  - Firebase/Google reachability and compensating controls.

Validation:

- React Native `npm run ci:launch` passed with registry access.
- React Native `npm audit --omit=dev --legacy-peer-deps` passed with 0 vulnerabilities.
- React Native `npm run typecheck:launch` passed.
- React Native `npm run lint:launch` passed.
- React Native `npm run typecheck` still fails on the existing full app baseline.
- React Native `npm run lint:ci` still fails on the existing full app baseline: 111 errors and 4415 warnings.
- Nest `pnpm audit --prod` still reports 4 advisories: 3 moderate `uuid` paths and 1 low `@tootallnate/once` path.

Remaining Phase 10 risks:

- Full React Native typecheck is not yet a clean CI gate.
- Full React Native strict lint is not yet a clean CI gate.
- React Native `ci:launch` uses a scoped typecheck and demotes existing unused-symbol/exhaustive-deps cleanup work; this is a launch bridge, not a replacement for strict cleanup.
- Nest Firebase/Google upstream dependency risk still needs owner sign-off or isolated compatibility testing before any forced `uuid@14` override.
- Provider evidence and operational drills remain open before production launch.

## Phase 11: React Native Strict Readiness Reduction

Goal:

- Convert the React Native launch bridge toward stricter readiness without breaking launch validation.
- Clear the highest runtime-risk full typecheck failures first: health service sessions, appointments, market/order flows, and broadcast commerce contracts.
- Reduce high-signal lint failures where they represent real hook dependency/order risks.

Completed on 2026-04-30:

- Restored health service session appointment wiring:
  - added healthcare service helpers for starting service sessions and loading/cancelling/rescheduling appointments.
  - added appointment booking state, reload, ICS open, cancel, and reschedule integration in the session screen.
  - removed a stale undefined `start` reference in institution card session startup.
- Cleared market/order strict typecheck failures:
  - added the `danger` button variant to shared KIS button types/styles.
  - fixed cart/order attachment typing, missing feedback style, implicit payload types, and React Native `FormData` upload typing.
  - fixed `ShopDashboardScreen` callback declaration order and explicit service payload filtering types.
- Cleared broadcast commerce/feed/education strict typecheck failures:
  - aligned broadcast market response fields for snake_case/camelCase price values, viewer roles, service booking, and missing style keys.
  - added `searchContext` prop acceptance to the market page.
  - aligned feed item/source types and guarded subscribe source access.
  - narrowed education viewer state/progress casts through `unknown`.
- Reduced full React Native lint baseline from 111 errors to 70 errors:
  - fixed `SocketProvider` call-control callback dependencies by memoizing call helpers.
  - fixed commerce dashboard permission dependencies and callback stability.
  - removed unused imports/locals in touched market files.
  - kept remaining lint failures visible for the next strict cleanup phase.

Validation:

- React Native `npm run typecheck` passed. Full strict TypeScript is now green.
- React Native `npm run ci:launch` passed:
  - `npm audit --omit=dev --legacy-peer-deps` passed with 0 vulnerabilities.
  - `npm run typecheck:launch` passed.
  - `npm run lint:launch` passed.
- React Native `npx eslint . --quiet` still fails on the remaining full lint baseline:
  - 70 errors.
  - Remaining failures are mostly unused symbols and hook dependency cleanup outside the Phase 11 high-risk path.
- React Native targeted `npx prettier --write` was applied to touched files.

Remaining Phase 11 risks:

- Full React Native strict typecheck is now clean, but full strict lint is not yet a clean CI gate.
- Some health/session additions use existing broad `any` API response patterns; Phase 12 should prefer typed response normalizers where practical.
- Remaining hook dependency warnings in service booking, Bible, education detail, profile, and updates screens need careful functional review before automatic dependency insertion.
- Nest Firebase/Google upstream dependency risk remains open from earlier phases.
- Provider-specific evidence, credential review/rotation, restore drill, rollback drill, Firebase key rotation drill, and private-media tabletop drill remain open.

## Phase 12: React Native Strict Lint Hardening

Goal:

- Move the React Native full lint baseline closer to launch readiness while keeping full TypeScript and launch CI green.
- Prioritize real stale-closure/hook dependency risks in scheduling, Bible, education, profile, updates, and market/feed screens.
- Avoid global rule weakening and avoid broad user-facing behavior changes.

Completed on 2026-04-30:

- Fixed high-risk service booking hook dependency issues:
  - `ServiceBookingScreen` booking confirmation now captures current selected package/addons, requested price, participant/staff counts, remote/location fields, requirements, and terms state.
  - removed the complex `requestedPrice.trim()` dependency by memoizing the trimmed value.
  - `ServiceBookingDetailsPage` now memoizes `scheduledAt`, preventing date-object churn in cancellation/reschedule memo dependencies.
- Fixed health availability stale draft dependency:
  - calendar day rendering now depends on the actual draft object used by the callback.
- Fixed Bible panel stale loader dependencies:
  - `BiblePlansPanel` loaders are stable callbacks used by effects.
  - `BibleReaderPanel` navigation and library loaders are stable callbacks, and reader language sync now tracks language changes.
- Fixed education detail sheet dependency issues:
  - removed unused MIME helper.
  - extracted viewer enrollment/booking values before effects.
  - changed selected assessment reset to depend on the selected item object.
- Fixed profile and updates dependency issues:
  - profile broadcast CTA is now a stable callback.
  - updates/status composer style palettes are module constants instead of new arrays each render.
- Fixed smaller market/feed hook and unused-symbol issues:
  - broadcast feed video retry/fallback callback tracks the full sources list.
  - product detail add-to-cart callback no longer lists unnecessary variant palette dependencies.
  - market product category and shop product image callbacks now have correct dependencies.
  - removed unused imports/locals in touched cart/order/profile files.
- Reduced full React Native lint baseline from 70 errors to 23 errors.

Validation:

- React Native `npm run typecheck` passed.
- React Native `npm run ci:launch` passed:
  - `npm audit --omit=dev --legacy-peer-deps` passed with 0 vulnerabilities.
  - `npm run typecheck:launch` passed.
  - `npm run lint:launch` passed.
- React Native `npx eslint . --quiet` still fails on the remaining full lint baseline:
  - 23 errors.
- React Native targeted `npx prettier --write` was applied to touched files.

Remaining Phase 12 risks:

- Full React Native strict lint is not yet a clean CI gate.
- The only remaining hook dependency cluster is `src/screens/tabs/profile-screen/EducationManagementModal.tsx`; it needs a careful education-management state review before automatic dependency insertion.
- Remaining non-hook lint errors are unused-symbol cleanup in tests and older UI modules:
  - broadcast feed tests.
  - wallet modal test imports.
  - broadcast feed section helper.
  - shared text input helper style.
  - language switcher locals.
  - healthcare unused helpers/state setters.
  - profile controller unused KISC constant.
- Nest Firebase/Google upstream dependency risk and provider-specific launch evidence remain open from earlier phases.

## Phase 13: React Native Strict Lint Closure

Goal:

- Close the remaining React Native full strict lint baseline safely.
- Start with the `EducationManagementModal` hook dependency cluster, then clean remaining unused-symbol errors.
- Keep full TypeScript, full strict lint, launch CI, and dependency audit green.

Completed on 2026-04-30:

- Fixed the remaining `EducationManagementModal` hook dependency cluster:
  - memoized `institutions` and `quickStats` from hub data.
  - removed unused detail stack and preview material state exposure.
  - added the missing `palette.primaryStrong`, `getEducationRecordTitle`, and `palette` dependencies where callbacks captured them.
  - removed unnecessary callback dependencies after lint validation.
- Cleaned the remaining unused-symbol errors:
  - broadcast feed discover test unused trending props.
  - wallet modal test unused React Native imports.
  - broadcast feed section unused attachment URL helper.
  - shared text input unused helper style.
  - language switcher unused safe-area/active-label locals.
  - broadcast healthcare unused KISC formatter.
  - health institution/session/catalog unused helpers/state setters.
  - profile controller unused KISC constant.
- Full React Native strict lint now passes.

Validation:

- React Native `npm run typecheck` passed.
- React Native `npx eslint . --quiet` passed.
- React Native `npm run ci:launch` passed:
  - `npm audit --omit=dev --legacy-peer-deps` passed with 0 vulnerabilities.
  - `npm run typecheck:launch` passed.
  - `npm run lint:launch` passed.
- React Native targeted `npx prettier --write` was applied to touched files.

Remaining Phase 13 risks:

- React Native strict lint/typecheck are now clean, but this does not replace runtime QA on the edited education, health, wallet, feed, and language UI flows.
- `baseline-browser-mapping` continues to print an age warning during lint; it is informational and did not fail lint.
- Nest Firebase/Google upstream dependency risk and provider-specific launch evidence remain open from earlier phases.
- Provider evidence, credential review/rotation, restore drill, rollback drill, Firebase key rotation drill, and private-media tabletop drill remain open.

## Phase 14: React Native Runtime QA Confidence

Goal:

- Add practical post-lint launch confidence for flows touched during Phases 11-13.
- Preserve clean React Native typecheck, strict lint, launch CI, and dependency audit gates.
- Document runtime smoke coverage where automated tests are currently blocked by local Jest configuration.

Completed on 2026-04-30:

- Added `docs/operations/REACT_NATIVE_LAUNCH_QA_CHECKLIST.md`.
- Covered smoke-test expectations for:
  - health service sessions and appointments.
  - service booking confirmation, reschedule, and cancellation.
  - Bible reader and plans loaders.
  - education management and education detail flows.
  - broadcast feed video fallback.
  - market product, cart, and order flows.
  - wallet transfer modal verification gating.
  - language switcher.
  - profile controller phone-change and wallet verification flows.
- Re-ran full React Native gates after Phase 13 lint closure.
- Attempted focused Jest regression tests for:
  - broadcast feed video playback fallback.
  - wallet modal transfer gating.
  - profile controller phone-change/session behavior.

Validation:

- React Native `npm run typecheck` passed.
- React Native `npx eslint . --quiet` passed.
- React Native `npm run ci:launch` passed:
  - `npm audit --omit=dev --legacy-peer-deps` passed with 0 vulnerabilities.
  - `npm run typecheck:launch` passed.
  - `npm run lint:launch` passed.
- Focused Jest run was blocked:
  - plain Jest attempted to invoke Watchman, which cannot write `/Users/nigel/Library/LaunchAgents/com.github.facebook.watchman.plist` in this sandbox.
  - rerunning with `--no-watchman` reached Jest config but failed before tests executed because React Native `jest/setup.js` is loaded as ESM without the needed transform.

Remaining Phase 14 risks:

- Runtime QA checklist still needs execution on real simulator/device builds with test data.
- Jest transform setup needs repair before React Native regression tests can run reliably in this environment.
- React Native lint/typecheck/launch gates are clean, but they do not prove runtime navigation, media playback, upload, wallet, or appointment flows.
- Nest Firebase/Google upstream dependency risk and provider-specific launch evidence remain open from earlier phases.
- Provider evidence, credential review/rotation, restore drill, rollback drill, Firebase key rotation drill, and private-media tabletop drill remain open.

## Phase 15: React Native Focused Test Reliability

Goal:

- Make focused React Native regression tests runnable without Watchman.
- Preserve clean typecheck, strict lint, launch CI, and production dependency audit gates.
- Capture runtime QA readiness evidence for the highest-value tests already present in the repo.

Completed on 2026-04-30:

- Added React Native `test:phase5` script:
  - `jest --config jest.phase5.config.js --runInBand --no-watchman`
- Reused the existing `jest.phase5.config.js` no-Watchman test configuration instead of changing the default React Native Jest preset globally.
- Fixed the focused profile-controller wallet transfer regression assertion to match the current wallet model:
  - `1` KISC maps to `100` cents through `CENTS_PER_KISC = 100`.
- Re-ran the focused regression set:
  - broadcast feed video fallback.
  - wallet modal transfer gating.
  - profile controller phone-change/session behavior and wallet verification.

Validation:

- React Native `npm run test:phase5 -- __tests__/broadcast-feeds.video-playback.test.tsx __tests__/phase5.wallet-modal.test.tsx __tests__/phase5.profile-controller.test.tsx` passed:
  - 3 suites passed.
  - 10 tests passed.
- React Native `npm run typecheck` passed.
- React Native `npx eslint . --quiet` passed.
- React Native `npm run ci:launch` passed:
  - `npm audit --omit=dev --legacy-peer-deps` passed with 0 vulnerabilities.
  - `npm run typecheck:launch` passed.
  - `npm run lint:launch` passed.
- React Native targeted Prettier check passed for changed test/package files.

Remaining Phase 15 risks:

- The default `npm test` path still uses the React Native preset and may invoke Watchman; use `npm run test:phase5 -- <files>` for focused no-Watchman regression tests until the broader Jest setup is deliberately modernized.
- Phase 15 did not add automated tests for health appointment helpers or service booking helpers because those screens are tightly coupled to navigation and React Native UI dependencies; they remain covered by the manual launch QA checklist.
- Runtime QA checklist still needs execution on simulator/device builds with realistic non-production data.
- Nest Firebase/Google upstream dependency risk and provider-specific launch evidence remain open from earlier phases.

## Phase 16: Provider Launch Evidence And Security Sign-Off

Goal:

- Convert remaining production launch blockers into concrete provider evidence requirements.
- Keep local validation green while avoiding real secret exposure.
- Make final go/no-go evidence explicit for environment values, Firebase/admin credentials, Nest Firebase/Google upstream risk, backup/restore, rollback, private media, and React Native runtime QA.

Completed on 2026-04-30:

- Expanded `docs/operations/PROVIDER_LAUNCH_READINESS_CHECKLIST.md` with:
  - local/code status versus provider evidence status.
  - production environment evidence requirements.
  - Firebase/admin credential evidence requirements.
  - Nest Firebase/Google upstream risk sign-off requirements.
  - backup, restore, rollback, and private-media tabletop evidence requirements.
  - React Native runtime QA evidence requirements.
- Updated `docs/operations/REACT_NATIVE_LAUNCH_QA_CHECKLIST.md` with:
  - release-ticket evidence header fields.
  - focused regression test command evidence.
  - Phase 16 local gate status.
  - reminder to store screenshots and environment-specific proof outside the repository.
- Re-ran local backend/docs and React Native launch validation.

Validation:

- Django `python3 manage.py check` passed.
- Docs secret scan passed for the launch roadmap/checklist documents.
- React Native `npm run test:phase5 -- __tests__/broadcast-feeds.video-playback.test.tsx __tests__/phase5.wallet-modal.test.tsx __tests__/phase5.profile-controller.test.tsx` passed:
  - 3 suites passed.
  - 10 tests passed.
- React Native `npm run typecheck` passed.
- React Native `npx eslint . --quiet` passed.
- React Native `npm run ci:launch` passed:
  - `npm audit --omit=dev --legacy-peer-deps` passed with 0 vulnerabilities.
  - `npm run typecheck:launch` passed.
  - `npm run lint:launch` passed.

Remaining Phase 16 blockers:

- Real provider production env values still need evidence from the hosting provider.
- Firebase Admin credential storage, least-privilege IAM, mobile key restrictions, and key rotation status still need console/provider proof.
- Database backup policy, restore drill, application rollback drill, environment rollback proof, and private-media tabletop proof still need execution evidence.
- React Native runtime QA still needs simulator/device execution with non-production data.
- Nest Firebase/Google upstream `uuid` / `@tootallnate/once` risk still needs owner, expiry, latest audit output, and production reachability sign-off.

## Next Prompt

Use this prompt for the next execution phase:

```text
Please proceed with Phase 17 of the KIS security hardening roadmap without using git commands. Focus on executing or preparing the final launch evidence bundle without exposing secrets: run production-safe Django deployment verifiers where environment access allows, run Nest security/env/audit checks where available, capture React Native runtime QA execution notes from simulator/device if available, and tighten any checklist gaps found in provider production evidence. Do not rotate/delete credentials without explicit approval, do not paste secret values into docs, and do not make broad app changes. Keep Django `python3 manage.py check`, docs secret scan, React Native `npm run typecheck`, `npx eslint . --quiet`, `npm run ci:launch`, and `npm run test:phase5 -- __tests__/broadcast-feeds.video-playback.test.tsx __tests__/phase5.wallet-modal.test.tsx __tests__/phase5.profile-controller.test.tsx` green. Update docs/SECURITY_HARDENING_ROADMAP.md, docs/BUILD_STATE.md, docs/operations/PROVIDER_LAUNCH_READINESS_CHECKLIST.md, and docs/operations/REACT_NATIVE_LAUNCH_QA_CHECKLIST.md with evidence collected, blockers, and the best prompt for Phase 18.
```
