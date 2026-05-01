"""
Base settings. Intended to be imported by local.py and production.py.
Contains production-safe defaults and advanced configuration patterns.
"""
import os
from pathlib import Path
from datetime import timedelta
from hashlib import sha256
from django.core.management.utils import get_random_secret_key
import dj_database_url

# NEW: load .env early so all os.environ lookups work everywhere
try:
    from dotenv import load_dotenv  # pip install python-dotenv
except ImportError:  # optional safety if not installed yet
    def load_dotenv(*args, **kwargs):
        return False

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")  # reads .env at project root

ENV = os.environ.get("DJANGO_ENV", "local")
DEV_SERVER_HOST = os.environ.get("DEV_SERVER_HOST", "10.14.20.99").strip() or "10.14.20.99"
DEV_SERVER_PORT = os.environ.get("DEV_SERVER_PORT", "8000").strip() or "8000"
DEV_CHAT_PORT = os.environ.get("DEV_CHAT_PORT", "4000").strip() or "4000"
DEV_REDIS_PORT = os.environ.get("DEV_REDIS_PORT", "6379").strip() or "6379"
DEV_BG_REMOVAL_PORT = os.environ.get("DEV_BG_REMOVAL_PORT", "9000").strip() or "9000"


def _dev_host_url(*, port: str, path: str = "", scheme: str = "http") -> str:
    suffix = path if path.startswith("/") or not path else f"/{path}"
    return f"{scheme}://{DEV_SERVER_HOST}:{port}{suffix}"


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _env_csv(name: str, default: str = "") -> list[str]:
    value = os.environ.get(name, default)
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _env_throttle_rate(name: str, *, dev_default: str, prod_default: str) -> str:
    default = dev_default if DEBUG else prod_default
    return os.environ.get(name, default).strip() or default


def _is_weak_secret(value: str | None) -> bool:
    if not value:
        return True
    text = str(value)
    if text.startswith("django-insecure-"):
        return True
    if len(text) < 50:
        return True
    if len(set(text)) < 5:
        return True
    return False

# SECRET_KEY should be overridden via environment in production
SECRET_KEY = os.environ.get("SECRET_KEY", "").strip()
if _is_weak_secret(SECRET_KEY):
    _seed_secret = os.environ.get("JWT_SECRET", "").strip() or os.environ.get("FLW_SECRET_KEY", "").strip()
    if _seed_secret:
        SECRET_KEY = (
            sha256(f"{_seed_secret}:kis:secret".encode("utf-8")).hexdigest()
            + sha256(f"{_seed_secret}:kis:salt".encode("utf-8")).hexdigest()
        )
if _is_weak_secret(SECRET_KEY):
    SECRET_KEY = get_random_secret_key()

DEBUG = _env_bool("DEBUG", False)

ALLOWED_HOSTS = _env_csv("ALLOWED_HOSTS", f"{DEV_SERVER_HOST},10.0.2.2")
CSRF_TRUSTED_ORIGINS = _env_csv("CSRF_TRUSTED_ORIGINS", "")

# Security defaults (override with env if needed)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = os.environ.get("SECURE_REFERRER_POLICY", "strict-origin-when-cross-origin")
SECURE_CROSS_ORIGIN_OPENER_POLICY = os.environ.get("SECURE_CROSS_ORIGIN_OPENER_POLICY", "same-origin")
X_FRAME_OPTIONS = os.environ.get("X_FRAME_OPTIONS", "DENY")

SECURE_SSL_REDIRECT = _env_bool("SECURE_SSL_REDIRECT", not DEBUG)
SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", not DEBUG)
CSRF_COOKIE_SECURE = _env_bool("CSRF_COOKIE_SECURE", not DEBUG)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax")
CSRF_COOKIE_SAMESITE = os.environ.get("CSRF_COOKIE_SAMESITE", "Lax")

SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "0" if DEBUG else "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", not DEBUG)
SECURE_HSTS_PRELOAD = _env_bool("SECURE_HSTS_PRELOAD", not DEBUG)

if _env_bool("USE_X_FORWARDED_PROTO", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Payments / Wallet
FLW_PUBLIC_KEY = os.environ.get("FLW_PUBLIC_KEY", "")
FLW_SECRET_KEY = os.environ.get("FLW_SECRET_KEY", "")
FLW_WEBHOOK_SECRET = os.environ.get("FLW_WEBHOOK_SECRET", "")
FLW_REDIRECT_URL = os.environ.get("FLW_REDIRECT_URL", "https://kis.app/payments/complete")
PAYMENTS_MOCK = os.environ.get("PAYMENTS_MOCK", "False").lower() in ("1", "true", "yes")

# Notifications / Firebase Cloud Messaging
NOTIFICATIONS_PUSH_PROVIDER = os.environ.get("NOTIFICATIONS_PUSH_PROVIDER", "firebase")
FIREBASE_APP_NAME = os.environ.get("FIREBASE_APP_NAME", "kis-backend")
FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "")
FIREBASE_CREDENTIALS_FILE = os.environ.get("FIREBASE_CREDENTIALS_FILE", "")
FIREBASE_CREDENTIALS_JSON = os.environ.get("FIREBASE_CREDENTIALS_JSON", "")
# Legacy FCM server-key fallback kept for older deployments while Firebase Admin is rolled out.
FCM_SERVER_KEY = os.environ.get("FCM_SERVER_KEY", os.environ.get("FIREBASE_SERVER_KEY", ""))
FIREBASE_SERVER_KEY = os.environ.get("FIREBASE_SERVER_KEY", "")

# Debug-only OTP code logging. Keep false unless explicitly debugging locally.
OTP_DEBUG_LOG_CODES = _env_bool("OTP_DEBUG_LOG_CODES", False)

# Application definition
INSTALLED_APPS = [
    # Django
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Third-party
    "rest_framework",
    # "rest_framework.authtoken",  # optional: remove if no longer using opaque tokens
    "drf_spectacular",
    "django_extensions",
    "django_celery_beat",
    "django_celery_results",
    "django_filters",  # NEW: needed for DjangoFilterBackend

    # Local apps
    "apps.accounts.apps.AccountsConfig",
    "apps.core.apps.CoreConfig",
    "apps.content.apps.ContentConfig",
    "apps.media.apps.MediaConfig",
    "apps.events.apps.EventsConfig",
    "apps.notifications.apps.NotificationsConfig",
    "apps.moderation.apps.ModerationConfig",
    "apps.ai_integration.apps.AIIntegrationConfig",
    "apps.commerce.apps.CommerceConfig",
    "apps.surveys.apps.SurveysConfig",
    "apps.bridge.apps.BridgeConfig",
    "apps.analytics.apps.AnalyticsConfig",
    "apps.tiers.apps.TiersConfig",
    "apps.otp.apps.OtpConfig",
    "apps.background_removal.apps.BackgroundRemovalConfig",
    "apps.statuses.apps.StatusesConfig",
    "apps.billing.apps.BillingConfig",

    # chats
    "apps.chat.apps.ChatConfig",
    "apps.partners.apps.PartnersConfig",
    "apps.communities.apps.CommunitiesConfig",
    "apps.groups.apps.GroupsConfig",
    "apps.channels.apps.ChannelsConfig",
    "apps.broadcasts.apps.BroadcastsConfig",
    "apps.health_ops.apps.HealthOpsConfig",
    "apps.health_dashboard.apps.HealthDashboardConfig",
    "apps.feed_personalization.apps.FeedPersonalizationConfig",
    "apps.bible.apps.BibleConfig",
    "admin_control.apps.AdminControlConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",

    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",

    # Common middleware
    "common.middleware.RequestLoggingMiddleware",
    "admin_control.activity.middleware.AdminControlActivityMiddleware",
    "admin_control.security.middleware.AdminSecurityHeadersMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Database
# In production/testing on Render, DATABASE_URL should point to Supabase Postgres.
# In local development, if DATABASE_URL is missing, we fall back to SQLite.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=int(os.environ.get("PG_CONN_MAX_AGE", "600")),
            ssl_require=DATABASE_URL.startswith("postgres"),
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(BASE_DIR / "db.sqlite3"),
        }
    }

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("TIME_ZONE", "UTC")
USE_I18N = True
USE_L10N = True
USE_TZ = True

# Static and media
STATIC_URL = "/static/"
MEDIA_URL = "/media/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_ROOT = BASE_DIR / "media"
SITE_URL = os.environ.get("SITE_URL", _dev_host_url(port=DEV_SERVER_PORT)).rstrip("/")
API_BASE_URL = os.environ.get("API_BASE_URL", SITE_URL)


DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Feed personalization tuning
FEED_PERSONALIZATION_RATE_LIMIT_SECONDS = int(os.environ.get("FEED_PERSONALIZATION_RATE_LIMIT_SECONDS", "8"))
FEED_PERSONALIZATION_MIN_WEIGHT = float(os.environ.get("FEED_PERSONALIZATION_MIN_WEIGHT", "0.02"))
FEED_PERSONALIZATION_GLOBAL_POPULARITY_TTL = int(
    os.environ.get("FEED_PERSONALIZATION_GLOBAL_POPULARITY_TTL", "300")
)
FEED_PERSONALIZATION_GLOBAL_POPULARITY_DECAY = float(
    os.environ.get("FEED_PERSONALIZATION_GLOBAL_POPULARITY_DECAY", "0.92")
)
FEED_PERSONALIZATION_DEFAULT_POPULARITY = float(
    os.environ.get("FEED_PERSONALIZATION_DEFAULT_POPULARITY", "0.06")
)

# Custom user
AUTH_USER_MODEL = "accounts.User"

# Authentication backends (Django-level).
AUTHENTICATION_BACKENDS = [
    "apps.accounts.auth_backends.PhoneOrEmailBackend",  # our custom backend
    "django.contrib.auth.backends.ModelBackend",   # keep as fallback
]

# REST Framework + JWT settings
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "apps.accounts.jwt_auth.DeviceBoundJWTAuthentication",
        # Keep SessionAuthentication for browsable API if you like:
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_PAGINATION_CLASS": "common.pagination.StandardResultsSetPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.ScopedRateThrottle",
        "admin_control.security.throttles.AdminBurstThrottle",
        "admin_control.security.throttles.AdminSustainedThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "anon": _env_throttle_rate("THROTTLE_ANON", dev_default="6000/min", prod_default="60/min"),
        "user": _env_throttle_rate("THROTTLE_USER", dev_default="6000/min", prod_default="300/min"),
        "admin_burst": _env_throttle_rate("THROTTLE_ADMIN_BURST", dev_default="6000/min", prod_default="60/min"),
        "admin_sustained": _env_throttle_rate("THROTTLE_ADMIN_SUSTAINED", dev_default="6000/day", prod_default="1000/day"),
        "login": _env_throttle_rate("THROTTLE_LOGIN", dev_default="6000/min", prod_default="10/min"),
        "register": _env_throttle_rate("THROTTLE_REGISTER", dev_default="6000/min", prod_default="5/min"),
        "otp": _env_throttle_rate("THROTTLE_OTP", dev_default="6000/min", prod_default="5/min"),
        "password_reset": _env_throttle_rate("THROTTLE_PASSWORD_RESET", dev_default="6000/min", prod_default="5/min"),
        "upload": _env_throttle_rate("THROTTLE_UPLOAD", dev_default="6000/min", prod_default="30/min"),
        "search": _env_throttle_rate("THROTTLE_SEARCH", dev_default="6000/min", prod_default="60/min"),
        "messaging": _env_throttle_rate("THROTTLE_MESSAGING", dev_default="6000/min", prod_default="120/min"),
        "broadcast_profile_create": _env_throttle_rate("THROTTLE_BROADCAST_PROFILE_CREATE", dev_default="6000/min", prod_default="10/min"),
        "broadcast_profile_manage": _env_throttle_rate("THROTTLE_BROADCAST_PROFILE_MANAGE", dev_default="6000/min", prod_default="60/min"),
        "broadcast_profile_attachment": _env_throttle_rate("THROTTLE_BROADCAST_PROFILE_ATTACHMENT", dev_default="6000/min", prod_default="30/min"),
    },
}

# drf-spectacular settings
SPECTACULAR_SETTINGS = {
    "TITLE": "KIS Accounts & Identity API",
    "DESCRIPTION": "OpenAPI schema for KIS Accounts & Identity service",
    "VERSION": "1.0.0",
    "COMPONENT_SPLIT_REQUEST": True,
    "SERVE_INCLUDE_SCHEMA": False,
    "POSTPROCESSING_HOOKS": [],
    "SECURITY": [{"bearerAuth": []}],
    "COMPONENTS": {
        "securitySchemes": {
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
            }
        }
    },
}

# Simple JWT — read signing/validation config from environment
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(os.environ.get("JWT_ACCESS_MINUTES", 60))),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=int(os.environ.get("JWT_REFRESH_DAYS", 7))),
    "ALGORITHM": "HS256",

    # Use your JWT secret from the environment (fallback to SECRET_KEY for dev)
    "SIGNING_KEY": os.environ.get("JWT_SECRET", SECRET_KEY),

    # Optional but recommended for strict validation
    # Set these in .env to have them embedded in tokens and enforced by verifiers
    "ISSUER": os.environ.get("JWT_ISSUER", SITE_URL),      # e.g., "http://10.14.20.99:8000"
    "AUDIENCE": os.environ.get("JWT_AUDIENCE", None),      # e.g., "messaging-platform"

    "AUTH_HEADER_TYPES": ("Bearer",),
    "UPDATE_LAST_LOGIN": True,
}

# Token / entitlement configuration (override these in environment-specific settings if desired)
ENTITLEMENTS_CACHE_TTL = int(os.environ.get("ENTITLEMENTS_CACHE_TTL", 300))  # seconds
API_TOKEN_PLAIN_LENGTH = int(os.environ.get("API_TOKEN_PLAIN_LENGTH", 32))  # used by secrets.token_urlsafe(n)
API_TOKEN_DEFAULT_EXPIRES_DAYS = int(os.environ.get("API_TOKEN_DEFAULT_EXPIRES_DAYS", 30))

# Caching (e.g., Redis) — used by quota enforcement and feature flags
CACHES = {
    "default": {
        # For local/dev the locmem cache is fine; override in production to Redis/Memcached
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "unique-snowflake",
    }
}

# Celery settings
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", f"redis://{DEV_SERVER_HOST}:{DEV_REDIS_PORT}/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", f"redis://{DEV_SERVER_HOST}:{DEV_REDIS_PORT}/1")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"

# NEW: media-service URL for background removal microservice
# This is what your Celery task uses to call the external service.
MEDIA_SERVICE_URL = os.environ.get(
    "MEDIA_SERVICE_URL",
    _dev_host_url(port=DEV_BG_REMOVAL_PORT, path="/process/background-removal"),
)

# NestJS internal webhook base + token for realtime event fanout
NEST_INTERNAL_URL = os.environ.get(
    "NEST_INTERNAL_URL",
    _dev_host_url(port=DEV_CHAT_PORT, path="/internal"),
)
NEST_INTERNAL_TOKEN = os.environ.get(
    "NEST_INTERNAL_TOKEN",
    os.environ.get("DJANGO_INTERNAL_TOKEN", ""),
)

# When the frontend resolves backend assets, it should reuse the API server's host instead of the chat/Nest host.
NEST_API_BASE_URL = API_BASE_URL

# Logging - keep it verbose for dev, JSON-friendly for prod
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "sensitive_data": {"()": "common.security_redaction.SensitiveDataFilter"},
    },
    "formatters": {
        "simple": {"format": "%(levelname)s %(asctime)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
            "filters": ["sensitive_data"],
        },
    },
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "DEBUG")},
}
