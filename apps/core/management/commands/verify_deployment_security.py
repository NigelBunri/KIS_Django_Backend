import os
from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand


def _is_weak_secret(value):
    text = str(value or "").strip()
    if not text:
        return True
    if text.startswith("django-insecure-"):
        return True
    if len(text) < 50:
        return True
    if len(set(text)) < 5:
        return True
    return text in {"dev-secret", "dev-internal-secret", "change-me", "password"}


def _env_bool(name):
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _csv_env(name):
    return [item.strip() for item in str(os.environ.get(name, "")).split(",") if item.strip()]


def _is_https_origin(value):
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


class Command(BaseCommand):
    help = "Safely verify deployment security settings without printing secret values."

    def add_arguments(self, parser):
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Return a non-zero exit code when any required production check fails.",
        )
        parser.add_argument(
            "--target-production",
            action="store_true",
            help="Apply production expectations even when the current local settings module is active.",
        )

    def handle(self, *args, **options):
        strict = bool(options["strict"])
        target_production = bool(options["target_production"])
        settings_module = os.environ.get("DJANGO_SETTINGS_MODULE", "")
        production_mode = target_production or settings_module.endswith(".production") or not settings.DEBUG
        rows = []

        def check(name, ok, detail):
            rows.append((name, bool(ok), detail))

        allowed_hosts = list(getattr(settings, "ALLOWED_HOSTS", []) or [])
        csrf_origins = list(getattr(settings, "CSRF_TRUSTED_ORIGINS", []) or [])
        throttle_rates = (
            getattr(settings, "REST_FRAMEWORK", {})
            .get("DEFAULT_THROTTLE_RATES", {})
        )
        cache_default = getattr(settings, "CACHES", {}).get("default", {})
        cache_backend = str(cache_default.get("BACKEND", ""))

        check(
            "DJANGO_SETTINGS_MODULE production",
            settings_module.endswith(".production") if production_mode else True,
            "production settings module is active" if settings_module.endswith(".production") else "set DJANGO_SETTINGS_MODULE=config.settings.production for production",
        )
        check("DEBUG disabled", not settings.DEBUG, "DEBUG is false" if not settings.DEBUG else "DEBUG must be false in production")
        check(
            "ALLOWED_HOSTS configured",
            bool(allowed_hosts) and "*" not in allowed_hosts,
            f"{len(allowed_hosts)} host(s) configured; wildcard not allowed",
        )
        check(
            "CSRF trusted origins configured",
            bool(csrf_origins) and all(_is_https_origin(origin) for origin in csrf_origins) if production_mode else True,
            f"{len(csrf_origins)} CSRF origin(s) configured; production origins should be HTTPS",
        )

        cors_origins = list(getattr(settings, "CORS_ALLOWED_ORIGINS", []) or [])
        cors_regexes = list(getattr(settings, "CORS_ALLOWED_ORIGIN_REGEXES", []) or [])
        cors_allow_all = bool(getattr(settings, "CORS_ALLOW_ALL_ORIGINS", False))
        has_cors_middleware = any("corsheaders" in item for item in getattr(settings, "MIDDLEWARE", []))
        check(
            "Django CORS policy",
            (not cors_allow_all and (bool(cors_origins) or bool(cors_regexes) or not has_cors_middleware)) if production_mode else True,
            "CORS is not wildcard; configure explicit origins if django-cors-headers is enabled",
        )

        for key in ("SECRET_KEY",):
            check(
                f"{key} strength",
                not _is_weak_secret(getattr(settings, key, "")),
                "present and strong enough by local policy",
            )
        for key in ("JWT_SECRET", "DJANGO_INTERNAL_TOKEN", "NEST_INTERNAL_TOKEN"):
            value = os.environ.get(key, "")
            check(
                f"{key} configured",
                not _is_weak_secret(value) if production_mode else bool(value) or True,
                "present and strong enough by local policy" if value else "must be set in production",
            )
        check(
            "Internal request signatures required",
            _env_bool("INTERNAL_SIGNATURE_REQUIRED") if production_mode else True,
            "set INTERNAL_SIGNATURE_REQUIRED=True in production to reject token-only internal calls",
        )
        skew = os.environ.get("INTERNAL_SIGNATURE_MAX_SKEW_SECONDS", "300")
        try:
            skew_seconds = int(skew)
        except ValueError:
            skew_seconds = 0
        check(
            "Internal signature timestamp window",
            30 <= skew_seconds <= 300 if production_mode else True,
            f"INTERNAL_SIGNATURE_MAX_SKEW_SECONDS={skew}",
        )

        check("OTP debug logging disabled", not _env_bool("OTP_DEBUG_LOG_CODES"), "OTP_DEBUG_LOG_CODES is not enabled")
        check(
            "HTTPS security flags",
            bool(settings.SECURE_SSL_REDIRECT and settings.SESSION_COOKIE_SECURE and settings.CSRF_COOKIE_SECURE),
            "SECURE_SSL_REDIRECT, SESSION_COOKIE_SECURE, and CSRF_COOKIE_SECURE should be true in production",
        )
        check(
            "HSTS configured",
            int(getattr(settings, "SECURE_HSTS_SECONDS", 0) or 0) > 0 if production_mode else True,
            f"HSTS seconds is {int(getattr(settings, 'SECURE_HSTS_SECONDS', 0) or 0)}",
        )
        check(
            "Redis/cache for throttling",
            "django_redis" in cache_backend if production_mode else True,
            "production should use django_redis cache backend for shared throttles",
        )
        risky_throttles = sorted(
            key for key, rate in throttle_rates.items()
            if str(rate).startswith("6000/")
        )
        check(
            "Production throttle rates",
            not risky_throttles if production_mode else True,
            f"{len(risky_throttles)} development-rate throttle(s) detected",
        )
        check(
            "Docs staff-only outside DEBUG",
            not settings.DEBUG,
            "config.urls selects staff-only OpenAPI views when DEBUG is false",
        )

        failures = [row for row in rows if not row[1]]
        width = max(len(row[0]) for row in rows)
        for name, ok, detail in rows:
            status = "PASS" if ok else "FAIL"
            style = self.style.SUCCESS if ok else self.style.ERROR
            self.stdout.write(f"{style(status):4} {name.ljust(width)}  {detail}")

        self.stdout.write("")
        self.stdout.write(f"Summary: {len(rows) - len(failures)}/{len(rows)} checks passing.")
        if failures and strict:
            raise SystemExit(1)
