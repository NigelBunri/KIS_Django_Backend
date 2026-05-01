# Dependency Remediation Plan

Use this plan to reduce production dependency risk without breaking the app.

## Rules

- Do not run force upgrades directly in production.
- Update one ecosystem at a time.
- Keep lockfile updates reviewable.
- Run smoke tests after each lockfile refresh.
- Treat critical/high production advisories as launch blockers unless explicitly accepted.

## Phase 8 Update - 2026-04-30

Phase 8 applied safe low-risk package and lockfile remediation where the package manager could complete without forcing major upgrades.

Completed:

- Refreshed the Nest `pnpm-lock.yaml` to apply existing production overrides.
- Updated Nest `fastify` from `5.7.3` to `5.8.5`.
- Added narrow Nest `pnpm.overrides` for patched transitive runtime packages:
  - `ajv@8.18.0`
  - `body-parser@2.2.1`
  - `follow-redirects@1.16.0`
  - `multer@2.1.1`
  - `path-to-regexp@8.4.2`
  - `socket.io-parser@4.2.6`
- Re-ran Nest audit after each safe pass.
- Attempted React Native lockfile refresh using:
  - `npm install --package-lock-only`
  - `npm update brace-expansion fast-xml-parser js-yaml minimatch picomatch qs yaml --package-lock-only --ignore-scripts --no-audit --no-fund`

Measured result:

- Nest production advisories reduced from 42 to 7.
- React Native production advisories remain at 14 because npm lockfile resolution stalled in this environment and was stopped.

Remaining Nest advisories:

- `lodash` via `@nestjs/config`: 1 high, 2 moderate.
  - Audit reports patched versions at `>=4.18.0`; do not force this until package availability and `@nestjs/config` compatibility are confirmed.
- `uuid` via `firebase-admin` and Google Cloud dependencies: 1 moderate.
  - Audit reports patched versions at `>=14.0.0`; this should be handled through a compatible `firebase-admin`/Google SDK update, not a blind override.
- `@tootallnate/once` via Firebase/Google Cloud transitive chain: 1 low.
  - Handle with the same Firebase/Google dependency update pass.

Remaining React Native advisories:

- `fast-xml-parser` remains the highest-risk family: 7 critical advisories through React Native CLI packages.
- `minimatch` and `picomatch` remain high.
- `brace-expansion`, `js-yaml`, `qs`, and `yaml` remain moderate.
- The package overrides already declare patched versions, but `package-lock.json` still resolves vulnerable versions and needs a successful npm lockfile refresh.

Current blocker:

- React Native npm lockfile refresh stalled without output in this local environment. Repeat in a clean terminal with stable registry access before forcing any major update.

## Phase 9 Update - 2026-04-30

Phase 9 completed the safe dependency launch-blocker pass that was blocked in Phase 8.

Completed:

- Updated Nest `@nestjs/config` from `^4.0.2` to `^4.0.4`.
- Added Nest `lodash@4.18.1` override.
- Refreshed the Nest lockfile, which also moved `firebase-admin` within the existing `^12.0.0` range to `12.7.0`.
- Confirmed `@nestjs/config@4.0.4` directly depends on `lodash@4.18.1`.
- Confirmed `firebase-admin@12.7.0` still depends on `uuid@^10.0.0`.
- Confirmed latest `firebase-admin@13.8.0` still depends on `uuid@^11.0.2`, so the `uuid>=14` advisory cannot be cleared by a safe Firebase patch/minor update.
- Corrected the React Native `fast-xml-parser` override from unavailable `5.6.1` to available `5.7.2`.
- Updated React Native CLI dev packages from `20.0.0` to `^20.1.3` using a lockfile-only npm update:
  - `@react-native-community/cli`
  - `@react-native-community/cli-platform-android`
  - `@react-native-community/cli-platform-ios`
- Added React Native `lodash@4.18.1` override.
- Refreshed the React Native `package-lock.json` with `--legacy-peer-deps` because npm 11 otherwise fails on an existing React/React DOM peer conflict.

Measured result:

- Nest production advisories reduced from 7 to 4.
- React Native production advisories reduced from 14 to 0.

Remaining Nest advisories:

- `uuid` via `firebase-admin` and Google Cloud dependencies: 3 moderate audit paths.
- `@tootallnate/once` via Firebase/Google Cloud transitive chain: 1 low audit path.

Current Nest decision:

- Do not force `uuid@14` as a transitive override yet. `uuid` has significant module-format/API changes across major versions, and both `firebase-admin@12.7.0` and `firebase-admin@13.8.0` request lower major ranges. Treat this as upstream risk until Firebase/Google publish a compatible path or a focused runtime compatibility test proves an override is safe.

Current React Native decision:

- Dependency audit is clean.
- Typecheck and lint still fail on the existing application baseline. These are no longer dependency advisory blockers, but they remain CI/readiness blockers.

## Nest Plan

Current production audit command:

```bash
pnpm audit --prod
```

Original status:

- 42 production advisories.
- 1 critical.
- 19 high.
- 19 moderate.
- 3 low.

Phase 8 status:

- 7 production advisories.
- 1 high.
- 5 moderate.
- 1 low.

Phase 9 status:

- 4 production advisories.
- 3 moderate audit paths for `uuid`.
- 1 low audit path for `@tootallnate/once`.

Priority order:

1. Track Firebase/Google releases that move to `uuid>=14`.
2. If launch requires accepting the remaining moderate `uuid` risk, record reachability, compensating controls, owner, and expiry date.
3. Only test a forced `uuid@14` override in an isolated branch/environment with Firebase Admin push notification, Firestore, and Storage smoke tests.
4. Re-run:

```bash
pnpm install
pnpm audit --prod
npm run typecheck
npm run security:env-check
```

Smoke tests:

- Nest starts.
- Socket.IO connects from configured origin.
- Django token introspection works.
- Upload endpoint accepts valid file and rejects blocked extension.
- Private upload download requires auth.
- Internal signed Django calls work.

## React Native Plan

Current production audit command:

```bash
npm audit --omit=dev
```

Original status:

- 14 production advisories.
- 7 critical.
- 2 high.
- 4 moderate.
- 1 low.

Phase 9 status:

- 0 production advisories.

Priority order:

1. Keep `npm audit --omit=dev --legacy-peer-deps` as the production dependency gate until the peer baseline is cleaned.
2. Clean the React Native typecheck baseline.
3. Clean the React Native lint baseline or reduce `lint:ci` scope to a realistic launch gate.
4. Run:

```bash
npm install
npm audit --omit=dev
npm run typecheck
npm run lint:ci
```

Smoke tests:

- Metro starts.
- iOS app launches.
- Android app launches.
- Login works.
- Messaging upload works.
- Push notification token registration works.

## Acceptance Process

If an advisory cannot be fixed before launch:

- Record package name.
- Record severity.
- Record exploitability in KIS context.
- Record whether vulnerable code is reachable in production.
- Record compensating controls.
- Assign expiry date for acceptance.
- Assign owner.

Do not accept critical/high production advisories silently.
