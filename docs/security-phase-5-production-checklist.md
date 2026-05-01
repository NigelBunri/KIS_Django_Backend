# Phase 5 Production Security Checklist

Use this before every production deploy. Keep real values in the hosting provider, not in source control.

## Secrets

- Rotate `SECRET_KEY`, `JWT_SECRET`, `DJANGO_INTERNAL_TOKEN`, payment keys, Firebase credentials, SMS provider keys, and AI provider keys if any value was ever shared or committed.
- Set `OTP_DEBUG_LOG_CODES=False`. Production startup rejects this flag when enabled.
- Keep `LOG_LEVEL=INFO` or stricter in production. Avoid `DEBUG` logging for request-heavy services.
- Store Firebase credentials as a secret file or secret manager value. Do not paste service account JSON into logs or tickets.

## Django

- Set `DEBUG=False`.
- Set exact `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`.
- Set `USE_X_FORWARDED_PROTO=True` behind TLS-terminating proxies.
- Keep `SECURE_SSL_REDIRECT=True`, secure cookies enabled, HSTS enabled, and `X_FRAME_OPTIONS=DENY`.
- Confirm `/api/docs/`, `/api/schema/`, and `/api/docs/redoc/` require staff auth in production.
- Confirm `/media/` is not served by Django in production. Use object storage or a web server with explicit MIME, size, and cache controls.

## Realtime / Nest

- Set `NODE_ENV=production`.
- Set `ORIGINS` to exact HTTPS app/admin origins only.
- Do not allow `*` or `http://` origins in production.
- Keep `DJANGO_TLS_INSECURE=0`.
- Keep `DJANGO_INTERNAL_TOKEN` and `DJANGO_JWT_SECRET` synchronized with Django.

## Logging

- Confirm application logs redact `authorization`, `password`, `secret`, `token`, `refresh`, `otp`, `code`, and provider keys.
- Do not log SMS bodies, OTP codes, push tokens, refresh tokens, access tokens, payment authorization headers, or Firebase service account payloads.
- Treat admin-exported logs as sensitive because user identifiers and IP addresses are still present.

## Auth And Sessions

- Deploy and run all account migrations before releasing code.
- Verify logout revokes the current device session.
- Verify revoked devices cannot refresh tokens.
- Monitor `security.auth.failed`, `security.auth.login_success`, `security.auth.refresh_success`, and `security.device.revoked` audit events.

## Media And Files

- Keep executable uploads blocked.
- Keep upload size limits explicit with `UPLOAD_MAX_BYTES`.
- Serve user uploads from a media domain or object-storage bucket that cannot execute scripts.
- Do not reuse the same domain/cookie scope for trusted admin pages and untrusted user-uploaded files.

## Operational Runbook

- Before deploy: run `python3 manage.py check`, `python3 manage.py makemigrations --check --dry-run`, and service-specific type/format checks.
- After deploy: test login, refresh, logout, upload, OTP request, admin login, OpenAPI docs access, and WebSocket connection from the configured app origin.
- Review audit logs after deploy for unexpected spikes in failed auth or CORS/origin failures.
