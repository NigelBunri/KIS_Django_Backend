# Phase 7 Launch Blocker Register

This register tracks the highest-risk blockers still open after Phases 0-6.
It is safe to share because it names locations and categories only; it must not
include secret values.

## Blocker Summary

| Blocker | Status | Launch Decision |
| --- | --- | --- |
| Local/prod credential exposure review | Open | Must review and rotate real exposed secrets before production. |
| Firebase admin service account file handling | Open | Must move production admin credentials to secret manager or protected file mount. |
| React Native Firebase mobile config review | Open | Must verify API key restrictions and package/app restrictions. |
| Nest production dependency advisories | Open | Must refresh lockfile and rerun `pnpm audit --prod`. |
| React Native production dependency advisories | Open | Must refresh lockfile and rerun `npm audit --omit=dev`. |
| React Native full typecheck baseline | Open | Must triage or explicitly approve known non-launch-blocking type debt. |
| Provider-specific launch readiness | Open | Must fill provider placeholders and run staging drills. |

## Secret Scan Findings From Phase 5

Do not paste values into tickets. Review these files locally and rotate real
credentials if they were ever shared, committed, uploaded, or used outside a
protected secret store.

| Finding | File | Current Safe Action |
| --- | --- | --- |
| Google API key pattern | Django `.env` line 47 | Verify key owner, restrict key, rotate if exposed. |
| Firebase service account private key | Nest `config/firebase-adminsdk.json` line 5 | Move to secret manager/protected file mount; rotate key before production. |
| Google API key pattern | React Native `android/app/google-services.json` line 18 | Verify Android package/SHA restrictions and API restrictions. |

## Dependency Audit Findings

### Nest

Last command:

```bash
pnpm audit --prod
```

Last result:

- 42 production advisories.
- 1 critical.
- 19 high.
- 19 moderate.
- 3 low.

Notable package families:

- `@fastify/middie`
- `glob`
- `fastify`
- `@isaacs/brace-expansion`
- `axios`
- `minimatch`
- `@fastify/static`
- `uuid`
- `@nestjs/core`
- `qs`
- Firebase/Google transitive packages

Safe remediation path:

1. Refresh Nest lockfile in a dedicated dependency branch/session.
2. Preserve app behavior and verify Nest starts locally.
3. Run focused typecheck.
4. Run `pnpm audit --prod`.
5. Run upload, chat, Socket.IO, and Django introspection smoke tests.

### React Native

Last command:

```bash
npm audit --omit=dev
```

Last result:

- 14 production advisories.
- 7 critical.
- 2 high.
- 4 moderate.
- 1 low.

Notable package families:

- `fast-xml-parser`
- `minimatch`
- `picomatch`
- `brace-expansion`
- `js-yaml`
- `qs`
- `yaml`

Safe remediation path:

1. Refresh lockfile without changing React Native major version.
2. Prefer patch-level CLI/transitive fixes first.
3. Run iOS/Android install/build smoke checks if available.
4. Run targeted messaging/upload lint.
5. Run `npm audit --omit=dev`.

## React Native Typecheck Baseline

Last command:

```bash
npx tsc --noEmit --pretty false
```

Current failure areas:

- Education discover content item typing.
- Broadcast feed item typing and undefined source handling.
- Broadcast market product/service field naming and style keys.
- Health institution/service session missing names and appointment state helpers.
- Market cart/order/shop typing.
- Broadcast tab prop mismatch.

Safe remediation path:

1. Split by domain and fix one domain per session.
2. Keep `src/Module/ChatRoom/uploadFileToBackend.ts` targeted lint green.
3. Avoid global type loosening to hide real contract errors.
4. Add or update API response types when the backend contract is clear.
5. Re-run full `npm run typecheck` after each domain slice.

## Provider-Specific Readiness

Before production launch, fill:

- `docs/operations/PRODUCTION_OPERATIONS_OVERVIEW.md`
- `docs/operations/DATABASE_BACKUP_RESTORE_RUNBOOK.md`
- `docs/operations/APPLICATION_ROLLBACK_RUNBOOK.md`
- `docs/operations/MEDIA_STORAGE_RECOVERY_RUNBOOK.md`
- `docs/operations/SECRET_ROTATION_RUNBOOK.md`
- `docs/operations/SECURITY_INCIDENT_RESPONSE_RUNBOOK.md`

Minimum provider evidence required:

- backup schedule screenshot or provider backup ID;
- restore test evidence;
- rollback drill evidence;
- secret manager path;
- Firebase service account storage location;
- production logs/alerts route;
- on-call owner and escalation channel.
