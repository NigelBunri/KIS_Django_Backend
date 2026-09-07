# Production overrides
from .base import *  # noqa
import os
from django.core.exceptions import ImproperlyConfigured

from .base import _is_weak_secret, parse_database_url

DEBUG = False
ALLOWED_HOSTS = [host.strip() for host in os.environ.get("ALLOWED_HOSTS", "").split(",") if host.strip()]
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("ALLOWED_HOSTS must be configured in production.")

SECRET_KEY = os.environ.get("SECRET_KEY", "").strip()
if _is_weak_secret(SECRET_KEY):
    raise ImproperlyConfigured("SECRET_KEY must be set to a strong value in production.")

JWT_SECRET = os.environ.get("JWT_SECRET", "").strip()
if _is_weak_secret(JWT_SECRET):
    raise ImproperlyConfigured("JWT_SECRET must be set to a strong value in production.")

DJANGO_INTERNAL_TOKEN = os.environ.get("DJANGO_INTERNAL_TOKEN", "").strip()
if _is_weak_secret(DJANGO_INTERNAL_TOKEN) or DJANGO_INTERNAL_TOKEN in {"dev-internal-secret", "internal-token-testing-2026-secure"}:
    raise ImproperlyConfigured("DJANGO_INTERNAL_TOKEN must be set to a strong non-development value in production.")

if os.environ.get("ALLOW_ALL_HOSTS", "").strip().lower() in ("1", "true", "yes", "on"):
    raise ImproperlyConfigured("ALLOW_ALL_HOSTS must not be enabled in production.")

if os.environ.get("OTP_DEBUG_LOG_CODES", "").strip().lower() in ("1", "true", "yes", "on"):
    raise ImproperlyConfigured("OTP_DEBUG_LOG_CODES must not be enabled in production.")

if os.environ.get("OTP_OVERRIDE_ENABLED", "").strip().lower() in ("1", "true", "yes", "on"):
    raise ImproperlyConfigured("OTP_OVERRIDE_ENABLED must not be enabled in production.")

if os.environ.get("OTP_OVERRIDE_CODE", "").strip():
    raise ImproperlyConfigured("OTP_OVERRIDE_CODE must not be set in production.")

if os.environ.get("VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED", "").strip().lower() in ("1", "true", "yes", "on"):
    raise ImproperlyConfigured("Verification live provider calls must not be enabled in production.")

if os.environ.get("VERIFICATION_PROVIDER_SANDBOX_NETWORK_ENABLED", "").strip().lower() in ("1", "true", "yes", "on"):
    raise ImproperlyConfigured("Verification sandbox network calls must not be enabled in production.")

# apps.chat.internal_signing already defaults to requiring a full HMAC
# signature (not just the bare token) whenever DEBUG=False — this only
# guards against an operator explicitly overriding that default back down
# in production, the same footgun-prevention pattern as OTP_OVERRIDE_ENABLED
# above.
if os.environ.get("INTERNAL_SIGNATURE_REQUIRED", "").strip().lower() in ("0", "false", "no", "off"):
    raise ImproperlyConfigured("INTERNAL_SIGNATURE_REQUIRED must not be disabled in production.")

_nest_internal_token = os.environ.get("NEST_INTERNAL_TOKEN", "").strip()
if _nest_internal_token and _is_weak_secret(_nest_internal_token):
    raise ImproperlyConfigured("NEST_INTERNAL_TOKEN must be set to a strong value in production.")

if os.environ.get("KIS_VIDEO_SERVICE_ENABLED", "").strip().lower() in ("1", "true", "yes", "on"):
    _kisvideo_token = os.environ.get("KIS_VIDEO_SERVICE_INTERNAL_TOKEN", "").strip()
    if not _kisvideo_token or _is_weak_secret(_kisvideo_token):
        raise ImproperlyConfigured("KIS_VIDEO_SERVICE_INTERNAL_TOKEN must be set to a strong value when KIS_VIDEO_SERVICE_ENABLED is on.")
    if not os.environ.get("KIS_VIDEO_SERVICE_BASE_URL", "").strip():
        raise ImproperlyConfigured("KIS_VIDEO_SERVICE_BASE_URL must be set when KIS_VIDEO_SERVICE_ENABLED is on.")

# Database — must be PostgreSQL in production; SQLite is not supported.
_db_url = os.environ.get("DATABASE_URL", "").strip()
if not _db_url:
    raise ImproperlyConfigured("DATABASE_URL must be set in production.")
if _db_url.startswith("sqlite"):
    raise ImproperlyConfigured(
        "DATABASE_URL must point to PostgreSQL in production, not SQLite."
    )
DATABASES["default"] = parse_database_url(_db_url, conn_max_age=60)
# Ping the connection before reuse so stale sockets are dropped silently.
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

# Redis cache — REDIS_URL must be set explicitly; no dev-IP fallback allowed.
_redis_url = os.environ.get("REDIS_URL", "").strip()
if not _redis_url:
    raise ImproperlyConfigured("REDIS_URL must be set in production.")
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": _redis_url,
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
    }
}

# Email: Resend (HTTP API) when RESEND_API_KEY is configured, otherwise the
# previous SMTP path — no forced cutover. Deliberately not a hard
# ImproperlyConfigured failure if neither is set: unlike DATABASE_URL/
# REDIS_URL, a missing email provider degrades one notification channel,
# not the whole app; verify_email_launch is the explicit opt-in guardrail
# for confirming it's actually production-ready before relying on it.
if RESEND_API_KEY:
    EMAIL_BACKEND = "apps.notifications.resend_backend.ResendEmailBackend"
else:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("EMAIL_HOST")
EMAIL_PORT = os.environ.get("EMAIL_PORT", 587)
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD")
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "no-reply@example.com")

# Logging: structured and less verbose
LOGGING["root"]["level"] = os.environ.get("LOG_LEVEL", "INFO")

# Sentry error tracking — optional; no-op if SENTRY_DSN is not set
_sentry_dsn = os.environ.get("SENTRY_DSN", "").strip()
if _sentry_dsn:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.redis import RedisIntegration
    sentry_sdk.init(
        dsn=_sentry_dsn,
        integrations=[DjangoIntegration(), CeleryIntegration(), RedisIntegration()],
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        send_default_pii=False,
        environment="production",
        # RENDER_GIT_COMMIT is auto-injected by Render at build and runtime
        # (not something we set) — ties an error report to the exact commit
        # that shipped it, instead of every deploy showing up as the same
        # undifferentiated "production" bucket in Sentry.
        release=os.environ.get("RENDER_GIT_COMMIT", "").strip() or None,
    )

# Object storage — require an explicit remote provider in production to prevent
# silent fallback to local container disk.
# Set OBJECT_STORAGE_PROVIDER=supabase or OBJECT_STORAGE_PROVIDER=s3 with the
# matching provider env vars.
_storage_provider = os.environ.get("OBJECT_STORAGE_PROVIDER", "").strip().lower()
if _storage_provider not in {"supabase", "s3"}:
    raise ImproperlyConfigured(
        "OBJECT_STORAGE_PROVIDER must be set to 'supabase' or 's3' in production."
    )

# Strict deploy security defaults
SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "True").lower() in ("1", "true", "yes", "on")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = os.environ.get("SECURE_HSTS_PRELOAD", "True").lower() in ("1", "true", "yes", "on")
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
