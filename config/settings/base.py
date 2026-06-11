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

# CORS — django-cors-headers
# In production, set CORS_ALLOWED_ORIGINS to comma-separated list of allowed origins.
# The mobile app (React Native) does not use browsers, so CORS is primarily for the web client.
CORS_ALLOWED_ORIGINS = _env_csv("CORS_ALLOWED_ORIGINS", f"http://{DEV_SERVER_HOST}:{DEV_SERVER_PORT}")
CORS_ALLOW_CREDENTIALS = _env_bool("CORS_ALLOW_CREDENTIALS", False)
CORS_ALLOW_ALL_ORIGINS = _env_bool("CORS_ALLOW_ALL_ORIGINS", False)

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
KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED = _env_bool("KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED", False)
PAYMENTS_MOCK = os.environ.get("PAYMENTS_MOCK", "False").lower() in ("1", "true", "yes")

# Financial safety defaults. Keep these disabled unless a controlled legacy
# migration or local-only test explicitly needs the old stored-value behavior.
# Stripe
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
KIS_STRIPE_ENABLED = _env_bool("KIS_STRIPE_ENABLED", True)
KIS_STRIPE_SANDBOX = _env_bool("KIS_STRIPE_SANDBOX", True)

KIS_LEGACY_WALLET_DEPOSIT_ENABLED = _env_bool("KIS_LEGACY_WALLET_DEPOSIT_ENABLED", False)
KIS_LEGACY_WALLET_TRANSFER_ENABLED = _env_bool("KIS_LEGACY_WALLET_TRANSFER_ENABLED", False)
KIS_LEGACY_CASH_CREDIT_CONVERSION_ENABLED = _env_bool("KIS_LEGACY_CASH_CREDIT_CONVERSION_ENABLED", False)
KIS_LEGACY_WALLET_UPGRADE_ENABLED = _env_bool("KIS_LEGACY_WALLET_UPGRADE_ENABLED", False)
KIS_LEGACY_PROMO_CASH_BONUS_ENABLED = _env_bool("KIS_LEGACY_PROMO_CASH_BONUS_ENABLED", False)
KIS_LEGACY_COMMERCE_WALLET_CHECKOUT_ENABLED = _env_bool("KIS_LEGACY_COMMERCE_WALLET_CHECKOUT_ENABLED", False)
KIS_COMMERCE_DEFAULT_PAYMENT_PROVIDER = os.environ.get("KIS_COMMERCE_DEFAULT_PAYMENT_PROVIDER", "flutterwave").strip() or "flutterwave"
KIS_LEGACY_EDUCATION_WALLET_CHECKOUT_ENABLED = _env_bool("KIS_LEGACY_EDUCATION_WALLET_CHECKOUT_ENABLED", False)
KIS_EDUCATION_DEFAULT_PAYMENT_PROVIDER = os.environ.get("KIS_EDUCATION_DEFAULT_PAYMENT_PROVIDER", "flutterwave").strip() or "flutterwave"
KIS_LEGACY_HEALTH_WALLET_CHECKOUT_ENABLED = _env_bool("KIS_LEGACY_HEALTH_WALLET_CHECKOUT_ENABLED", False)
KIS_HEALTH_DEFAULT_PAYMENT_PROVIDER = os.environ.get("KIS_HEALTH_DEFAULT_PAYMENT_PROVIDER", "flutterwave").strip() or "flutterwave"

# Profitability roadmap billing readiness. These flags expose preview metadata
# only until legal/product/QA approval explicitly turns on live billing.
KIS_PROFITABILITY_BILLING_ENABLED = _env_bool("KIS_PROFITABILITY_BILLING_ENABLED", False)
KIS_PROFITABILITY_ENTITLEMENTS_ENFORCED = _env_bool("KIS_PROFITABILITY_ENTITLEMENTS_ENFORCED", False)
KIS_PROFITABILITY_TRIALS_ENABLED = _env_bool("KIS_PROFITABILITY_TRIALS_ENABLED", False)
KIS_PROFITABILITY_PROMOTION_CHECKOUT_ENABLED = _env_bool("KIS_PROFITABILITY_PROMOTION_CHECKOUT_ENABLED", False)
KIS_PROFITABILITY_ENTERPRISE_LEADS_ENABLED = _env_bool("KIS_PROFITABILITY_ENTERPRISE_LEADS_ENABLED", False)

# AI assistance safety. These defaults allow UI/policy placeholders but keep
# networked provider calls disabled until an approved provider, moderation, and
# privacy review are complete.
KIS_AI_ASSISTANCE_ENABLED = _env_bool("KIS_AI_ASSISTANCE_ENABLED", True)
KIS_AI_LIVE_PROVIDER_CALLS_ENABLED = _env_bool("KIS_AI_LIVE_PROVIDER_CALLS_ENABLED", False)
KIS_AI_PROVIDER = os.environ.get("KIS_AI_PROVIDER", "").strip()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_DEFAULT_MODEL = os.environ.get("ANTHROPIC_DEFAULT_MODEL", "claude-haiku-4-5-20251001")
KIS_AI_OUTPUT_MODERATION_REQUIRED = _env_bool("KIS_AI_OUTPUT_MODERATION_REQUIRED", True)
KIS_AI_INPUT_REDACTION_REQUIRED = _env_bool("KIS_AI_INPUT_REDACTION_REQUIRED", True)
KIS_AI_CHILD_SAFE_MODE_REQUIRED = _env_bool("KIS_AI_CHILD_SAFE_MODE_REQUIRED", True)
KIS_AI_STORE_PROMPTS_ENABLED = _env_bool("KIS_AI_STORE_PROMPTS_ENABLED", False)
KIS_AI_STORE_RESPONSES_ENABLED = _env_bool("KIS_AI_STORE_RESPONSES_ENABLED", False)
KIS_AI_MEDICAL_DIAGNOSIS_ENABLED = _env_bool("KIS_AI_MEDICAL_DIAGNOSIS_ENABLED", False)
KIS_AI_FINANCIAL_ADVICE_ENABLED = _env_bool("KIS_AI_FINANCIAL_ADVICE_ENABLED", False)

# Public web / growth safety. Public pages expose only published public content
# and redacted metadata; embeds still obey their stricter embed flags/policies.
KIS_PUBLIC_WEB_ENABLED = _env_bool("KIS_PUBLIC_WEB_ENABLED", True)
KIS_PUBLIC_WEB_BASE_URL = os.environ.get("KIS_PUBLIC_WEB_BASE_URL", "https://kis.app").strip().rstrip("/")
KIS_PUBLIC_WEB_INDEXING_ENABLED = _env_bool("KIS_PUBLIC_WEB_INDEXING_ENABLED", False)
KIS_PUBLIC_REFERRALS_ENABLED = _env_bool("KIS_PUBLIC_REFERRALS_ENABLED", False)

# Launch-cut controls. The 80% launch cut keeps high-risk/optional systems
# behind approval flags while preserving the roadmap toward 95% and 120%.
KIS_LAUNCH_CUT_MODE = os.environ.get("KIS_LAUNCH_CUT_MODE", "80").strip() or "80"
KIS_PARITY_95_FEATURES_ENABLED = _env_bool("KIS_PARITY_95_FEATURES_ENABLED", False)
KIS_DIFFERENTIATION_120_FEATURES_ENABLED = _env_bool("KIS_DIFFERENTIATION_120_FEATURES_ENABLED", False)
KIS_EXPERIMENTAL_120_FEATURES_ENABLED = _env_bool("KIS_EXPERIMENTAL_120_FEATURES_ENABLED", False)

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

# SendGrid — transactional email (OTP, welcome, receipts)
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "").strip()

# Infobip — SMS and WhatsApp OTP delivery
INFOBIP_API_KEY = os.environ.get("INFOBIP_API_KEY", "").strip()
INFOBIP_BASE = os.environ.get("INFOBIP_BASE", "").strip().rstrip("/")

# WhatsApp Business sender number (digits only, no leading +), e.g. 237676000000
WHATSAPP_SENDER_NUMBER = os.environ.get("WHATSAPP_SENDER_NUMBER", "").strip()
# Optional: Meta-approved template name for first-contact OTP delivery
WHATSAPP_TEMPLATE_NAME = os.environ.get("WHATSAPP_TEMPLATE_NAME", "").strip()
WHATSAPP_TEMPLATE_LANGUAGE = os.environ.get("WHATSAPP_TEMPLATE_LANGUAGE", "en").strip() or "en"

# Override OTP code that always passes verification (testing only — never commit a real value here).
OTP_OVERRIDE_CODE = os.environ.get("OTP_OVERRIDE_CODE", "676139").strip()

# Verification / KYC / KYB providers.
# Phase 1 only reads configuration. Live provider calls are introduced in later phases.
VERIFICATION_PROVIDER_PRIMARY = os.environ.get("VERIFICATION_PROVIDER_PRIMARY", "dojah").strip() or "dojah"
VERIFICATION_PROVIDER_FALLBACK = os.environ.get("VERIFICATION_PROVIDER_FALLBACK", "sumsub").strip() or "sumsub"
VERIFICATION_WEBHOOK_SECRET = os.environ.get("VERIFICATION_WEBHOOK_SECRET", "").strip()
VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED = _env_bool("VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED", False)
VERIFICATION_PROVIDER_SANDBOX_ENABLED = _env_bool("VERIFICATION_PROVIDER_SANDBOX_ENABLED", True)
VERIFICATION_PROVIDER_SANDBOX_NETWORK_ENABLED = _env_bool("VERIFICATION_PROVIDER_SANDBOX_NETWORK_ENABLED", False)
VERIFICATION_PROVIDER_TIMEOUT_SECONDS = int(os.environ.get("VERIFICATION_PROVIDER_TIMEOUT_SECONDS", "10"))
VERIFICATION_WEBHOOK_BASE_URL = os.environ.get("VERIFICATION_WEBHOOK_BASE_URL", "").strip().rstrip("/")
VERIFICATION_PROVIDER_LIVE_ALLOWED_ENVS = _env_csv("VERIFICATION_PROVIDER_LIVE_ALLOWED_ENVS", "staging")
VERIFICATION_LIVE_PROVIDER_SUBJECTS = [
    item.strip()
    for item in os.environ.get("VERIFICATION_LIVE_PROVIDER_SUBJECTS", "").split(",")
    if item.strip()
]
VERIFICATION_EXPIRY_REMINDER_DAYS = [
    int(item.strip())
    for item in os.environ.get("VERIFICATION_EXPIRY_REMINDER_DAYS", "30,14,7,1").split(",")
    if item.strip().isdigit()
]
DOJAH_APP_ID = os.environ.get("DOJAH_APP_ID", "").strip()
DOJAH_SECRET_KEY = os.environ.get("DOJAH_SECRET_KEY", "").strip()
DOJAH_BASE_URL = os.environ.get("DOJAH_BASE_URL", "https://api.dojah.io").rstrip("/")
SUMSUB_APP_TOKEN = os.environ.get("SUMSUB_APP_TOKEN", "").strip()
SUMSUB_SECRET_KEY = os.environ.get("SUMSUB_SECRET_KEY", "").strip()
SUMSUB_BASE_URL = os.environ.get("SUMSUB_BASE_URL", "https://api.sumsub.com").rstrip("/")
SMILE_ID_PARTNER_ID = os.environ.get("SMILE_ID_PARTNER_ID", "").strip()
SMILE_ID_API_KEY = os.environ.get("SMILE_ID_API_KEY", "").strip()
SMILE_ID_BASE_URL = os.environ.get("SMILE_ID_BASE_URL", "").rstrip("/")

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
    "corsheaders",
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
    "apps.verification.apps.VerificationConfig",

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
    "apps.location.apps.LocationConfig",
    "admin_control.apps.AdminControlConfig",
    "apps.testimony.apps.TestimonyConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
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
MEDIA_URL = os.environ.get("MEDIA_URL", "/media/")
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_ROOT = BASE_DIR / "media"
SITE_URL = os.environ.get("SITE_URL", _dev_host_url(port=DEV_SERVER_PORT)).rstrip("/")
API_BASE_URL = os.environ.get("API_BASE_URL", SITE_URL)

OBJECT_STORAGE_PROVIDER = os.environ.get("OBJECT_STORAGE_PROVIDER", "").strip().lower()
if OBJECT_STORAGE_PROVIDER == "supabase":
    STORAGES = {
        "default": {"BACKEND": "apps.media.storage_backends.SupabaseStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }


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
    "REFRESH_TOKEN_LIFETIME": timedelta(days=int(os.environ.get("JWT_REFRESH_DAYS", 90))),
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
CELERY_TASK_SOFT_TIME_LIMIT = 300   # seconds — raises SoftTimeLimitExceeded for graceful cleanup
CELERY_TASK_TIME_LIMIT = 360        # hard kill after 6 minutes

# NEW: media-service URL for background removal microservice
# This is what your Celery task uses to call the external service.
MEDIA_SERVICE_URL = os.environ.get(
    "MEDIA_SERVICE_URL",
    _dev_host_url(port=DEV_BG_REMOVAL_PORT, path="/process/background-removal"),
)

# Central upload/media safety. Local development stays usable by default; production
# should require explicit scan/review before user-uploaded media becomes public.
MEDIA_SAFETY_ENABLED = _env_bool("MEDIA_SAFETY_ENABLED", True)
MEDIA_EXPLICIT_SCAN_REQUIRED = _env_bool("MEDIA_EXPLICIT_SCAN_REQUIRED", not DEBUG)
MEDIA_SAFETY_PROVIDER = os.environ.get("MEDIA_SAFETY_PROVIDER", "stub").strip() or "stub"
MEDIA_SAFETY_LIVE_PROVIDER_CALLS_ENABLED = _env_bool("MEDIA_SAFETY_LIVE_PROVIDER_CALLS_ENABLED", False)
MEDIA_SAFETY_MAX_UPLOAD_BYTES = int(os.environ.get("MEDIA_SAFETY_MAX_UPLOAD_BYTES", os.environ.get("UPLOAD_MAX_BYTES", 2147483647)))
MEDIA_UPLOAD_CHECKSUM_ENABLED = _env_bool("MEDIA_UPLOAD_CHECKSUM_ENABLED", False)
MEDIA_SAFETY_ALLOWED_MIME_TYPES = os.environ.get("MEDIA_SAFETY_ALLOWED_MIME_TYPES", "")
MEDIA_SAFETY_ALLOWED_MIME_PREFIXES = os.environ.get("MEDIA_SAFETY_ALLOWED_MIME_PREFIXES", "")
MEDIA_SAFETY_ALLOWED_EXTENSIONS = os.environ.get("MEDIA_SAFETY_ALLOWED_EXTENSIONS", "")
MEDIA_SAFETY_BLOCKED_EXTENSIONS = os.environ.get("MEDIA_SAFETY_BLOCKED_EXTENSIONS", "")

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

# Logging — JSON structured output so log aggregators (Datadog, CloudWatch, etc.) can parse fields
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "sensitive_data": {"()": "common.security_redaction.SensitiveDataFilter"},
    },
    "formatters": {
        "json": {"()": "common.logging_formatters.JsonRequestFormatter"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["sensitive_data"],
        },
    },
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "DEBUG")},
}
