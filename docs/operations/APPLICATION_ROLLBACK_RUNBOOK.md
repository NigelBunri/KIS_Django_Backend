# Application And Environment Rollback Runbook

Use this when a deployment breaks login, messaging, payments, uploads, admin, or
other production-critical flows.

## Ownership

- Release owner: `TODO_RELEASE_OWNER`
- Rollback approver: `TODO_ROLLBACK_APPROVER`
- Hosting provider: `TODO_PROVIDER_NAME`
- Django service: `TODO_DJANGO_SERVICE`
- Nest service: `TODO_NEST_SERVICE`
- Mobile release channel: `TODO_MOBILE_RELEASE_CHANNEL`

## Rollback Triggers

Rollback immediately when any of these are true:

- users cannot log in or refresh tokens;
- chat cannot connect or send messages;
- payment/webhook path is broken;
- private media becomes publicly reachable;
- admin access is public or staff cannot access admin;
- migrations cause data loss or unrecoverable errors;
- error rate or latency exceeds agreed incident threshold;
- security verifier fails in production.

## Pre-Rollback Checklist

- Declare incident.
- Freeze further deploys.
- Identify current application versions for Django, Nest, and mobile release.
- Identify last known good versions.
- Confirm whether database migrations were applied.
- Confirm latest database backup ID.
- Decide whether database rollback is required.

## Django Rollback

Provider-neutral steps:

1. Select last known good build/artifact/container.
2. Keep current environment variables unless they caused the incident.
3. Deploy previous Django artifact.
4. Restart web workers and Celery workers.
5. Run:

```bash
python3 manage.py check
python3 manage.py showmigrations --plan
python3 manage.py verify_deployment_security --target-production
```

6. Smoke test:

- login;
- token refresh;
- OTP request;
- notification creation;
- upload;
- admin login;
- docs staff-only behavior.

## Nest Rollback

Provider-neutral steps:

1. Select last known good Nest artifact/container.
2. Confirm `ORIGINS`, `DJANGO_INTERNAL_TOKEN`, and `INTERNAL_SIGNATURE_REQUIRED`.
3. Deploy previous Nest artifact.
4. Restart service.
5. Run:

```bash
npm run security:env-check
```

6. Smoke test:

- Socket.IO connection from configured app origin;
- HTTP upload endpoint;
- authenticated private upload download;
- Django introspection path;
- internal broadcast/channel-message path.

## React Native Rollback

If the issue is mobile-only:

- Use the store/provider staged rollout controls if available.
- Pause rollout.
- Promote previous release or hotfix channel.
- If app store rollback is not possible, ship urgent patch release.
- Confirm backend remains compatible with both current and previous mobile versions.

## Environment Rollback

Roll back env changes when:

- CORS/origin settings lock out valid clients;
- internal signatures are misconfigured;
- provider credentials were changed incorrectly;
- Redis/database URL changed incorrectly;
- feature flags caused the incident.

Steps:

1. Export current env metadata without secret values.
2. Restore previous env values from provider history or secret manager version.
3. Restart affected services.
4. Run production verifiers.
5. Watch logs for failed auth, CORS failures, and internal signature failures.

## Post-Rollback Checks

Run:

```bash
scripts/security/phase5_validation.sh
python3 scripts/security/verify_ops_readiness.py
```

Then validate user flows:

- login / logout / refresh;
- OTP;
- messaging open/send/receive;
- media upload/download;
- push notification dispatch;
- admin/docs access;
- payment/webhook if enabled.

## Evidence To Record

- Incident ID.
- Bad version.
- Good version restored.
- Migration status.
- Database backup ID.
- Rollback start/end time.
- Smoke test results.
- Follow-up owner.
