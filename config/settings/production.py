# Production overrides
from .base import *  # noqa
import os
import dj_database_url  # ensure this package is in production requirements
from django.core.exceptions import ImproperlyConfigured

from .base import _is_weak_secret

DEBUG = False
ALLOWED_HOSTS = [host.strip() for host in os.environ.get("ALLOWED_HOSTS", "").split(",") if host.strip()]
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("ALLOWED_HOSTS must be configured in production.")

SECRET_KEY = os.environ.get("SECRET_KEY", "").strip()
if _is_weak_secret(SECRET_KEY):
    raise ImproperlyConfigured("SECRET_KEY must be set to a strong value in production.")

# Database from DATABASE_URL env var.
DATABASES["default"] = dj_database_url.parse(os.environ["DATABASE_URL"], conn_max_age=600)

# Use redis for cache in production
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": os.environ.get("REDIS_URL", "redis://10.14.20.99:6379/0"),
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
    }
}

# Use real email provider settings (SendGrid, SES, etc.)
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("EMAIL_HOST")
EMAIL_PORT = os.environ.get("EMAIL_PORT", 587)
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD")
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "no-reply@example.com")

# Logging: structured and less verbose
LOGGING["root"]["level"] = os.environ.get("LOG_LEVEL", "INFO")

# Strict deploy security defaults
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = os.environ.get("SECURE_HSTS_PRELOAD", "True").lower() in ("1", "true", "yes", "on")
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
