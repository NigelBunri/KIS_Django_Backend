# KIS Security Validation Runbook

Use this runbook before production deploys and after security-sensitive changes.

## Main Local Sweep

From the Django backend root:

```bash
scripts/security/phase5_validation.sh
```

The script keeps running after failures and prints a summary. This is intentional:
blocked checks should be recorded instead of hiding later failures.

Optional heavier checks:

```bash
RUN_FULL_TESTS=1 scripts/security/phase5_validation.sh
RUN_DEPENDENCY_AUDIT=1 scripts/security/phase5_validation.sh
RUN_FULL_TESTS=1 RUN_DEPENDENCY_AUDIT=1 scripts/security/phase5_validation.sh
```

`RUN_DEPENDENCY_AUDIT=1` may need network access to the package registry.

## Django Checks

Core checks:

```bash
python3 manage.py check
python3 manage.py verify_deployment_security --target-production
python3 manage.py makemigrations --check --dry-run
python3 manage.py test apps.chat.tests.ConversationUnreadContractTests.test_strict_internal_auth_accepts_signed_request_and_rejects_replay apps.chat.tests.ConversationUnreadContractTests.test_strict_internal_auth_rejects_legacy_token_only_request apps.media.tests.PrivateMediaAccessTests --noinput --keepdb
```

Production verifier failures are expected on a local development `.env`. The
important point is that the verifier runs without printing secret values.

Migration rule:

- `makemigrations --check --dry-run` must pass before deployment.
- If it fails, create and review the missing migration before deploying.
- Never run production migrations without a rollback and backup plan.

## Nest Checks

From the Nest backend root:

```bash
node --check scripts/verify-production-env.js
npm run security:env-check
npm run typecheck
npm run lint:ci
```

If full `npm run typecheck` is blocked by existing test globals or local build
metadata writes, run a focused typecheck on touched files and record the blocker.

Dependency hygiene:

```bash
npm audit --omit=dev
```

## React Native Checks

From the React Native app root:

```bash
npm run typecheck
npm run lint:ci
npm test -- --runInBand --watchman=false
```

If project-wide typecheck or lint is noisy, run targeted lint/tests for touched
files and record the wider blocker.

Dependency hygiene:

```bash
npm audit --omit=dev
```

## Secret Exposure Scan

From the Django backend root:

```bash
python3 scripts/security/secret_scan.py \
  --root "/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis" \
  --root "/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/Nestjs/CC_Node_Backend" \
  --root "/Users/nigel/dev/KIS"
```

The scanner reports only path, line number, and rule name. It does not print
matched secret values. Review any finding manually and rotate real secrets if
they were ever committed, logged, or shared.

## Production Launch Gates

Before deploy, confirm:

- `DJANGO_SETTINGS_MODULE=config.settings.production`
- `DEBUG=False`
- `INTERNAL_SIGNATURE_REQUIRED=True` in Django
- `INTERNAL_SIGNATURE_REQUIRED=1` in Nest
- `INTERNAL_SIGNATURE_MAX_SKEW_SECONDS` is between `30` and `300`
- Redis-backed cache is active for throttles and internal nonce replay checks
- Nest multi-instance nonce storage is either single-instance safe or moved to a shared store
- Dependency audits have no unaccepted critical/high production vulnerabilities
- Migration dry run passes
- Database backup and rollback plan exist
