# KIS Deployment Security Launch Gate

Use this checklist before putting production traffic on the app.

Do not paste real secret values into chat, tickets, logs, screenshots, or documentation.

## Required Before Public Launch

### Django

- `DJANGO_SETTINGS_MODULE=config.settings.production`
- `DEBUG=False`
- Strong `SECRET_KEY`
- Strong `JWT_SECRET`
- Strong `DJANGO_INTERNAL_TOKEN`
- Real `DATABASE_URL`
- Production `ALLOWED_HOSTS`
- Production `CSRF_TRUSTED_ORIGINS`
- HTTPS is enabled at the platform/load balancer
- `SECURE_SSL_REDIRECT=True`
- `SESSION_COOKIE_SECURE=True`
- `CSRF_COOKIE_SECURE=True`
- HSTS enabled after HTTPS is confirmed
- Redis/cache configured for production throttling
- OTP debug logging disabled
- Swagger/docs require staff login outside debug

Safe commands:

```bash
python3 manage.py check
python3 manage.py verify_deployment_security --target-production
python3 manage.py verify_deployment_security --target-production --strict
DJANGO_SETTINGS_MODULE=config.settings.production python3 manage.py check --deploy
```

Expected:

- Local development may fail the production command if production secrets are intentionally absent.
- Production/staging should not fail for missing or weak production secrets.

### Nest

- `NODE_ENV=production`
- Production `ORIGINS` contains only HTTPS production frontend origins
- Strong `DJANGO_INTERNAL_TOKEN`
- Strong `DJANGO_JWT_SECRET`
- `DJANGO_INTROSPECT_URL` points to production Django over HTTPS
- `DJANGO_TLS_INSECURE` is not enabled
- `MONGODB_URI` is production-only and not reachable publicly
- Socket.IO CORS uses the same configured origins
- Internal endpoints are not exposed without internal auth

Safe commands:

```bash
node scripts/verify-production-env.js
npm run security:env-check
npx tsc --noEmit
npm audit --omit=dev
```

### React Native

- No bearer/access/refresh tokens in URL query strings
- Tokens stored through secure storage only
- API base URLs point to production HTTPS
- No debug API host is compiled into release builds
- Push notification tokens are treated as sensitive identifiers
- Certificate/media downloads use headers or signed one-time URLs, not bearer tokens in URLs

Safe commands:

```bash
npx tsc --noEmit --pretty false
npm test -- --runInBand
```

## Must Be Verified In Hosting Provider

- Database backups are enabled.
- Backup retention period is defined.
- Restore process has been tested.
- Rollback process is documented.
- Production secrets are stored in the provider secret manager or protected environment settings.
- Only required staff can view/edit production secrets.
- Database is not publicly reachable.
- Admin accounts use strong passwords and MFA where available.
- WAF/CDN/DDoS protection is enabled if available.
- Error logs do not expose tokens, OTPs, passwords, payment secrets, or internal tokens.

## Current Known Blockers

- Full frontend `npx tsc --noEmit --pretty false` is blocked by existing unrelated TypeScript errors.
- Local production deploy check fails closed because the local `.env` does not contain a production-strength `SECRET_KEY`.
- Local deploy check under local settings also surfaces an existing drf-spectacular schema error in `PatientHealthSummarySerializer`.
- Local Django production-target verification currently fails expected production gates because local settings are not production, CSRF origins are absent, production security flags are disabled, Redis cache is not active, and local throttle rates are development-friendly.
- Local Nest production verification currently fails expected production gates because local env is not production, origins are not HTTPS-only, local shared secrets are weak/development values, and `DJANGO_TLS_INSECURE` is enabled.
- Private media policy is not complete while Nest serves `/uploads/` directly.
- High-risk IDOR sweep remains incomplete for analytics, tiers, billing, health_ops, partners, AI, events, and admin-like endpoints.

## Launch Decision

Launch should wait if any of these are true:

- Production settings cannot boot with strong secrets.
- `DEBUG=True` in production.
- Any production admin/docs endpoint is public.
- High-risk private data endpoint is reachable by the wrong user.
- Private uploads are publicly reachable.
- Bearer/access/refresh tokens are placed into URLs.
- No database backup exists.
- No rollback path exists.
