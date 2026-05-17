from __future__ import annotations

from typing import Any

from django.conf import settings

from .profitability_entitlements import PROMOTIONAL_CREDIT_SAFETY_COPY
from .profitability_revenue_readiness import get_revenue_launch_readiness_summary


MONETIZATION_FLAGS: tuple[str, ...] = (
    "KIS_PROFITABILITY_BILLING_ENABLED",
    "KIS_PROFITABILITY_ENTITLEMENTS_ENFORCED",
    "KIS_PROFITABILITY_TRIALS_ENABLED",
    "KIS_PROFITABILITY_PROMOTION_CHECKOUT_ENABLED",
    "KIS_PROFITABILITY_ENTERPRISE_LEADS_ENABLED",
    "KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED",
)


LEGACY_MONEY_FLAGS: tuple[str, ...] = (
    "KIS_LEGACY_WALLET_DEPOSIT_ENABLED",
    "KIS_LEGACY_WALLET_TRANSFER_ENABLED",
    "KIS_LEGACY_CASH_CREDIT_CONVERSION_ENABLED",
    "KIS_LEGACY_WALLET_UPGRADE_ENABLED",
    "KIS_LEGACY_PROMO_CASH_BONUS_ENABLED",
    "KIS_LEGACY_COMMERCE_WALLET_CHECKOUT_ENABLED",
    "KIS_LEGACY_EDUCATION_WALLET_CHECKOUT_ENABLED",
    "KIS_LEGACY_HEALTH_WALLET_CHECKOUT_ENABLED",
)


def _setting_bool(name: str) -> bool:
    return bool(getattr(settings, name, False))


def _flag_check(flag_name: str, *, expected: bool = False) -> dict[str, Any]:
    current = _setting_bool(flag_name)
    return {
        "name": flag_name,
        "current": current,
        "expected": expected,
        "ready": current is expected,
        "status": "ready" if current is expected else "blocked",
    }


def get_profitability_production_go_no_go_summary(*, user=None) -> dict[str, Any]:
    readiness = get_revenue_launch_readiness_summary(user=user)
    monetization_flag_checks = {name: _flag_check(name, expected=False) for name in MONETIZATION_FLAGS}
    legacy_flag_checks = {name: _flag_check(name, expected=False) for name in LEGACY_MONEY_FLAGS}

    flutterwave_live_disabled = not _setting_bool("KIS_DIRECT_PAYMENT_PROVIDER_LINKS_ENABLED")
    provider_secret_present = bool(str(getattr(settings, "FLW_SECRET_KEY", "") or "").strip())
    webhook_secret_present = bool(str(getattr(settings, "FLW_WEBHOOK_SECRET", "") or "").strip())

    evidence_areas = readiness.get("areas", {})
    approved_areas = [
        key for key, value in evidence_areas.items()
        if value.get("state") == "approved" and value.get("ready")
    ]
    rollback_ready = evidence_areas.get("rollback_proof", {}).get("state") == "approved"

    checks = {
        "monetization_flags_disabled": {
            "ready": all(item["ready"] for item in monetization_flag_checks.values()),
            "status": "ready" if all(item["ready"] for item in monetization_flag_checks.values()) else "blocked",
            "flags": monetization_flag_checks,
        },
        "legacy_money_flags_disabled": {
            "ready": all(item["ready"] for item in legacy_flag_checks.values()),
            "status": "ready" if all(item["ready"] for item in legacy_flag_checks.values()) else "blocked",
            "flags": legacy_flag_checks,
        },
        "flutterwave_live_provider_disabled": {
            "ready": flutterwave_live_disabled,
            "status": "ready" if flutterwave_live_disabled else "blocked",
            "secret_presence_redacted": {
                "FLW_SECRET_KEY_present": provider_secret_present,
                "FLW_WEBHOOK_SECRET_present": webhook_secret_present,
            },
        },
        "approved_evidence_coverage": {
            "ready": readiness.get("readiness_percent") == 100 and readiness.get("go_no_go") == "go",
            "status": "ready" if readiness.get("readiness_percent") == 100 and readiness.get("go_no_go") == "go" else "blocked",
            "readiness_percent": readiness.get("readiness_percent", 0),
            "approved_areas": approved_areas,
            "missing_count": readiness.get("missing_count", 0),
            "blocked_count": readiness.get("blocked_count", 0),
            "expired_count": readiness.get("expired_count", 0),
        },
        "rollback_readiness": {
            "ready": bool(rollback_ready),
            "status": "ready" if rollback_ready else "blocked",
            "required_area": "rollback_proof",
        },
        "staff_only_revenue_operations": {
            "ready": True,
            "status": "ready",
            "note": "Revenue evidence and production go/no-go endpoints use staff/admin permissions.",
        },
        "promotional_credit_legal_safety": {
            "ready": True,
            "status": "ready",
            "policy": PROMOTIONAL_CREDIT_SAFETY_COPY,
            "non_cash": True,
            "non_transferable": True,
            "non_withdrawable": True,
            "not_exchange_rated": True,
        },
    }

    blocked = [key for key, value in checks.items() if not value.get("ready")]
    ready_count = len(checks) - len(blocked)
    total_count = len(checks)
    return {
        "enabled": False,
        "access": "staff_read_only",
        "go_no_go": "go" if not blocked else "no_go_production_checks_blocked",
        "readiness_percent": int((ready_count / total_count) * 100) if total_count else 0,
        "ready_count": ready_count,
        "total_count": total_count,
        "blocked_checks": blocked,
        "checks": checks,
        "guardrails": {
            "no_live_charges": True,
            "no_production_provider_calls": True,
            "no_entitlement_enforcement": True,
            "no_payment_instrument_collection": True,
            "no_private_health_payment_verification_data": True,
            "staff_only": True,
            "read_only": True,
        },
        "next_readiness_steps": [
            "Keep all monetization and legacy money flags disabled until explicit release approval.",
            "Approve all evidence areas, including rollback proof, before production monetization review.",
            "Capture redacted production env flag evidence and attach it to revenue evidence records.",
            "Run final legal, pastoral/child-safety, tax, payment, privacy, and release sign-off before any live launch.",
        ],
    }
