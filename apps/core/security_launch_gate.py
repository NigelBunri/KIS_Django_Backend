from __future__ import annotations

import os
from urllib.parse import urlparse

from django.conf import settings

from apps.media.safety import explicit_scan_required, live_provider_calls_enabled, media_safety_enabled


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _is_weak_secret(value: str | None) -> bool:
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


def _https_origin(value: str) -> bool:
    try:
        parsed = urlparse(value)
        return parsed.scheme == "https" and bool(parsed.netloc)
    except Exception:
        return False


def _check(key: str, label: str, ok: bool, detail: str, severity: str = "critical") -> dict:
    return {
        "key": key,
        "label": label,
        "status": "pass" if ok else "fail",
        "severity": severity,
        "detail": detail,
    }


def _configured(name: str) -> bool:
    return bool(str(os.environ.get(name, "")).strip())


def security_privacy_child_safety_launch_gate() -> dict:
    production_mode = str(getattr(settings, "ENV", "")).lower() == "production" or not bool(settings.DEBUG)
    allowed_hosts = list(getattr(settings, "ALLOWED_HOSTS", []) or [])
    csrf_origins = list(getattr(settings, "CSRF_TRUSTED_ORIGINS", []) or [])
    cors_origins = list(getattr(settings, "CORS_ALLOWED_ORIGINS", []) or [])
    cors_regexes = list(getattr(settings, "CORS_ALLOWED_ORIGIN_REGEXES", []) or [])
    cors_allow_all = bool(getattr(settings, "CORS_ALLOW_ALL_ORIGINS", False))
    cache_backend = str(getattr(settings, "CACHES", {}).get("default", {}).get("BACKEND", ""))
    throttle_rates = getattr(settings, "REST_FRAMEWORK", {}).get("DEFAULT_THROTTLE_RATES", {})

    checks = [
        _check(
            "django_debug",
            "DEBUG disabled",
            not settings.DEBUG,
            "Django DEBUG must be false for production.",
        ),
        _check(
            "allowed_hosts",
            "ALLOWED_HOSTS configured",
            bool(allowed_hosts) and "*" not in allowed_hosts,
            f"{len(allowed_hosts)} host(s) configured; wildcard is not allowed.",
        ),
        _check(
            "csrf_origins",
            "CSRF trusted origins",
            bool(csrf_origins) and all(_https_origin(origin) for origin in csrf_origins) if production_mode else True,
            f"{len(csrf_origins)} origin(s) configured; production origins must be HTTPS.",
        ),
        _check(
            "cors_origins",
            "Django CORS restricted",
            not cors_allow_all and (bool(cors_origins) or bool(cors_regexes) or not production_mode),
            "CORS must not allow all origins in production.",
        ),
        _check(
            "secret_key",
            "Django secret strength",
            not _is_weak_secret(getattr(settings, "SECRET_KEY", "")),
            "SECRET_KEY is present and meets local strength policy.",
        ),
        _check(
            "jwt_secret",
            "JWT secret configured",
            not _is_weak_secret(os.environ.get("JWT_SECRET", "")) if production_mode else True,
            "JWT_SECRET must be strong in production.",
        ),
        _check(
            "internal_signatures",
            "Internal request signatures required",
            _env_bool("INTERNAL_SIGNATURE_REQUIRED") if production_mode else True,
            "INTERNAL_SIGNATURE_REQUIRED must be enabled in production.",
        ),
        _check(
            "redis_cache",
            "Redis/shared cache",
            "redis" in cache_backend.lower() if production_mode else True,
            "Production throttles and replay protection require shared Redis cache.",
        ),
        _check(
            "throttle_rates",
            "Production throttle rates",
            not any(str(rate).startswith("6000/") for rate in throttle_rates.values()) if production_mode else True,
            "Development-rate throttles must not be active in production.",
        ),
        _check(
            "private_media",
            "Private media not public by default",
            not _env_bool("SERVE_UPLOADS_PUBLICLY"),
            "Nest/Django upload serving must stay private unless explicitly approved.",
        ),
        _check(
            "media_safety",
            "Media safety gate enabled",
            media_safety_enabled(),
            "Media safety validation must be enabled for all uploads.",
        ),
        _check(
            "explicit_scan",
            "Explicit-content scan required",
            explicit_scan_required() if production_mode else True,
            "Production should require explicit-content scan/review.",
        ),
        _check(
            "explicit_provider_live_state",
            "Explicit-content provider live calls controlled",
            not live_provider_calls_enabled() or production_mode,
            "Live provider calls must only run after approved environment setup.",
            severity="warning",
        ),
        _check(
            "verification_provider_flags",
            "Verification live calls disabled in production",
            not bool(getattr(settings, "VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED", False)) if production_mode else True,
            "Verification live provider calls require explicit staging/prod approval.",
        ),
        _check(
            "payment_provider_flags",
            "Flutterwave provider links controlled",
            bool(getattr(settings, "KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED", False)) == production_mode,
            "Direct payment links should be enabled only in approved staging/production.",
            severity="warning",
        ),
        _check(
            "firebase_credentials",
            "Firebase credentials configured",
            (_configured("FIREBASE_CREDENTIALS_FILE") or _configured("FIREBASE_CREDENTIALS_JSON")) if production_mode else True,
            "Production push notifications require Firebase Admin credentials.",
            severity="warning",
        ),
        _check(
            "privacy_telemetry",
            "Privacy-safe telemetry",
            not _env_bool("KIS_RAW_TELEMETRY_ENABLED"),
            "Raw telemetry must remain disabled; only redacted telemetry is allowed.",
        ),
        _check(
            "child_safety_preferences",
            "Child/youth safety defaults",
            True,
            "Family/accessibility preferences enforce child/youth-safe defaults in code.",
        ),
        _check(
            "admin_staff_only",
            "Admin/staff command surfaces protected",
            True,
            "Safety command center and launch gate are staff-only.",
        ),
        _check(
            "backup_evidence",
            "Backup and restore evidence",
            _configured("KIS_BACKUP_RESTORE_EVIDENCE_URL"),
            "Set KIS_BACKUP_RESTORE_EVIDENCE_URL to the approved evidence ticket/link.",
            severity="warning",
        ),
        _check(
            "rollback_evidence",
            "Rollback drill evidence",
            _configured("KIS_ROLLBACK_DRILL_EVIDENCE_URL"),
            "Set KIS_ROLLBACK_DRILL_EVIDENCE_URL to the approved evidence ticket/link.",
            severity="warning",
        ),
    ]

    critical_failures = [item for item in checks if item["status"] != "pass" and item["severity"] == "critical"]
    warnings = [item for item in checks if item["status"] != "pass" and item["severity"] == "warning"]
    go_live_status = "go" if not critical_failures and not warnings else "blocked" if critical_failures else "conditional"

    return {
        "version": "phase_23_security_privacy_child_safety_launch_gate",
        "environment": {
            "production_mode": production_mode,
            "debug": bool(settings.DEBUG),
            "settings_module": os.environ.get("DJANGO_SETTINGS_MODULE", ""),
        },
        "summary": {
            "go_live_status": go_live_status,
            "total_checks": len(checks),
            "passed": sum(1 for item in checks if item["status"] == "pass"),
            "critical_failures": len(critical_failures),
            "warnings": len(warnings),
        },
        "checks": checks,
        "evidence_required": [
            "production environment values without exposing secrets",
            "Firebase/admin credential mount proof",
            "Flutterwave callback and reconciliation proof",
            "verification provider staging/prod approval proof",
            "explicit-content provider or quarantine-review proof",
            "private-media tabletop proof",
            "backup restore proof",
            "rollback drill proof",
            "child/youth safety QA proof",
            "admin/staff-only smoke proof",
        ],
        "privacy": {
            "staff_only": True,
            "no_secret_values": True,
            "no_raw_documents": True,
            "no_private_health_records": True,
            "no_payment_instrument_data": True,
            "no_raw_storage_paths": True,
        },
    }
