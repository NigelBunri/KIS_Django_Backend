# KIS - Notifications Django App (Advanced)

This app provides the centralized KIS notification system. Every notification is stored as an in-app notification and, by default, also receives a related push delivery record for Firebase Cloud Messaging.

Features included:
- Notification templates with basic token rendering
- In-app notification model with actions, snooze, expiry, deduplication
- Notification rules for per-user suppression, quiet hours, and channel preferences
- Delivery tracking per channel with retry/backoff (Celery tasks)
- Firebase Cloud Messaging delivery through `firebase-admin` when Firebase credentials are configured
- Safe local/no-credentials behavior: in-app delivery still succeeds and push delivery stays pending with a clear error
- Digest aggregation for batched emails or in-app digests
- Utilities for signing webhooks, quiet-hours checks, and basic rate-limiting

Quick start:
1. Add `notifications` to INSTALLED_APPS.
2. Ensure `rest_framework` and `celery` (if used) are configured.
3. Include `notifications.urls` into your project's URL conf: `path("api/", include("notifications.urls"))`.
4. Run `python manage.py migrate`.

Firebase setup:
- Install dependencies from `requirements.txt` / `requirements/base.txt`.
- Prefer Firebase Admin credentials:
  - `FIREBASE_CREDENTIALS_JSON='{"type":"service_account", ...}'`, or
  - `FIREBASE_CREDENTIALS_FILE=/secure/path/firebase-service-account.json`
- Optional:
  - `FIREBASE_APP_NAME=kis-backend`
  - `FIREBASE_PROJECT_ID=<project id>`
- Legacy fallback is still supported with `FCM_SERVER_KEY` / `FIREBASE_SERVER_KEY`.

Client integration:
- Register mobile tokens at `POST /api/v1/notification-device-tokens/register/`.
- Unregister tokens at `POST /api/v1/notification-device-tokens/unregister/`.
- List in-app notifications at `GET /api/v1/notifications/`.
- Read count is available at `GET /api/v1/notifications/unread-count/`.
- Mark all read at `POST /api/v1/notifications/mark-all-read/`.

Security & production notes:
- Replace the naive template renderer with Jinja2 or Django templates for safety.
- Integrate Email/SMS/Webhook providers in the `process_notification_delivery` task.
- Respect user locale and timezone when checking quiet hours.
- Store secrets (webhook signing key) in environment variables.
- Integrate ML-based personalization in `personalization_score` and suppression logic.

Extensions I can provide next:
- Full integration examples for Email & SMS providers (sendgrid/twilio) without Node.js.
- Frontend components (React) for in-app notification center and real-time updates (websockets).
- Rate-limiting & throttling rules per user & global with Redis-backed counters.
- A production-ready templating engine and sandbox for user-editable templates.
