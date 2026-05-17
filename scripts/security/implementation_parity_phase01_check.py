#!/usr/bin/env python3
"""Phase 01 production-security evidence checker.

This checker is intentionally read-only and redacted. It verifies that the
launch evidence system exists, that dangerous production feature flags are not
enabled in the current environment, and that Django/Nest/React Native have
documented validation commands. It does not print secret values.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = Path("/Users/nigel/dev/KIS")
NEST_ROOT = ROOT.parent / "Nestjs" / "CC_Node_Backend"

REQUIRED_DOCS = {
    "docs/implementation-parity-roadmap/phase-00-launch-scope-lock.md": [
        "Existing Feature Flags And Config Checks",
        "Master Blocker Register",
    ],
    "docs/DEPLOYMENT_SECURITY_LAUNCH_GATE.md": [
        "verify_deployment_security",
        "ALLOWED_HOSTS",
        "CORS",
    ],
    "docs/operations/PROVIDER_LAUNCH_READINESS_CHECKLIST.md": [
        "Production",
        "Evidence",
    ],
    "docs/operations/DATABASE_BACKUP_RESTORE_RUNBOOK.md": [
        "Restore Test Procedure",
        "Evidence To Record",
    ],
    "docs/operations/APPLICATION_ROLLBACK_RUNBOOK.md": [
        "Rollback Triggers",
        "Post-Rollback Checks",
    ],
    "docs/operations/REACT_NATIVE_LAUNCH_QA_CHECKLIST.md": [
        "Platform/device/OS",
        "Device and OS versions",
    ],
    "docs/operations/FIREBASE_CREDENTIAL_HANDLING.md": [
        "Firebase",
        "credential",
    ],
}

MUST_BE_DISABLED = [
    "KIS_LEGACY_WALLET_DEPOSIT_ENABLED",
    "KIS_LEGACY_WALLET_TRANSFER_ENABLED",
    "KIS_LEGACY_CASH_CREDIT_CONVERSION_ENABLED",
    "KIS_LEGACY_WALLET_UPGRADE_ENABLED",
    "KIS_LEGACY_PROMO_CASH_BONUS_ENABLED",
    "KIS_LEGACY_COMMERCE_WALLET_CHECKOUT_ENABLED",
    "KIS_LEGACY_EDUCATION_WALLET_CHECKOUT_ENABLED",
    "KIS_LEGACY_HEALTH_WALLET_CHECKOUT_ENABLED",
    "KIS_PROFITABILITY_BILLING_ENABLED",
    "KIS_PROFITABILITY_TRIALS_ENABLED",
    "KIS_PROFITABILITY_PROMOTION_CHECKOUT_ENABLED",
    "KIS_PROFITABILITY_ENTERPRISE_LEADS_ENABLED",
    "KIS_AI_LIVE_PROVIDER_CALLS_ENABLED",
    "KIS_AI_STORE_PROMPTS_ENABLED",
    "KIS_AI_STORE_RESPONSES_ENABLED",
    "KIS_AI_MEDICAL_DIAGNOSIS_ENABLED",
    "KIS_AI_FINANCIAL_ADVICE_ENABLED",
    "KIS_PUBLIC_WEB_INDEXING_ENABLED",
    "KIS_PUBLIC_REFERRALS_ENABLED",
    "KIS_PARITY_95_FEATURES_ENABLED",
    "KIS_DIFFERENTIATION_120_FEATURES_ENABLED",
    "KIS_EXPERIMENTAL_120_FEATURES_ENABLED",
    "VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED",
    "VERIFICATION_PROVIDER_SANDBOX_NETWORK_ENABLED",
    "MEDIA_SAFETY_LIVE_PROVIDER_CALLS_ENABLED",
    "PAYMENTS_MOCK",
]

SHOULD_BE_ENABLED_FOR_PRODUCTION = [
    "MEDIA_SAFETY_ENABLED",
    "MEDIA_EXPLICIT_SCAN_REQUIRED",
    "KIS_AI_OUTPUT_MODERATION_REQUIRED",
    "KIS_AI_INPUT_REDACTION_REQUIRED",
    "KIS_AI_CHILD_SAFE_MODE_REQUIRED",
]


def env_bool(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def add(rows: list[tuple[str, str, str]], status: str, name: str, detail: str) -> None:
    rows.append((status, name, detail))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero for warnings/evidence gaps. Default exits non-zero only for hard failures.",
    )
    args = parser.parse_args()

    rows: list[tuple[str, str, str]] = []

    for rel, phrases in REQUIRED_DOCS.items():
        path = ROOT / rel
        if not path.exists():
            add(rows, "FAIL", f"doc:{rel}", "missing")
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        missing = [phrase for phrase in phrases if phrase not in text]
        add(
            rows,
            "PASS" if not missing else "FAIL",
            f"doc:{rel}",
            "required sections present" if not missing else f"missing sections: {', '.join(missing)}",
        )

    enabled_risky = [name for name in MUST_BE_DISABLED if env_bool(name)]
    add(
        rows,
        "PASS" if not enabled_risky else "FAIL",
        "env:risky_launch_flags_disabled",
        f"{len(enabled_risky)} risky flag(s) enabled; values redacted",
    )

    missing_enabled = [
        name
        for name in SHOULD_BE_ENABLED_FOR_PRODUCTION
        if os.environ.get(name, "") and not env_bool(name)
    ]
    add(
        rows,
        "PASS" if not missing_enabled else "WARN",
        "env:production_safety_flags",
        "enabled or defaults are safety-preserving" if not missing_enabled else f"{len(missing_enabled)} explicitly disabled",
    )

    provider_defaults_ok = all(
        (os.environ.get(name, "flutterwave").strip().lower() or "flutterwave") == "flutterwave"
        for name in (
            "KIS_COMMERCE_DEFAULT_PAYMENT_PROVIDER",
            "KIS_EDUCATION_DEFAULT_PAYMENT_PROVIDER",
            "KIS_HEALTH_DEFAULT_PAYMENT_PROVIDER",
        )
    )
    add(
        rows,
        "PASS" if provider_defaults_ok else "WARN",
        "env:direct_payment_provider_defaults",
        "commerce/education/health default to Flutterwave" if provider_defaults_ok else "unexpected provider default configured",
    )

    django_verifier = ROOT / "apps" / "core" / "management" / "commands" / "verify_deployment_security.py"
    add(
        rows,
        "PASS" if django_verifier.exists() else "FAIL",
        "django:verify_deployment_security",
        "management command exists" if django_verifier.exists() else "missing management command",
    )

    nest_package = read_json(NEST_ROOT / "package.json")
    nest_scripts = nest_package.get("scripts", {}) if isinstance(nest_package, dict) else {}
    nest_env_script = NEST_ROOT / "scripts" / "verify-production-env.js"
    add(
        rows,
        "PASS" if nest_env_script.exists() and "security:env-check" in nest_scripts else "FAIL",
        "nest:security_env_check",
        "security:env-check script available" if nest_env_script.exists() else "missing verify-production-env.js",
    )

    frontend_package = read_json(FRONTEND_ROOT / "package.json")
    frontend_scripts = frontend_package.get("scripts", {}) if isinstance(frontend_package, dict) else {}
    for script in ("ci:launch", "typecheck:launch", "lint:launch", "audit:prod"):
        add(
            rows,
            "PASS" if script in frontend_scripts else "FAIL",
            f"react-native:{script}",
            "script available" if script in frontend_scripts else "missing script",
        )

    evidence_gaps = [
        "production env values",
        "backup/restore drill",
        "rollback drill",
        "private media proof",
        "Firebase/admin credential proof",
        "React Native real-device QA",
        "Flutterwave staging proof",
        "Postgres-backed test run",
    ]
    for gap in evidence_gaps:
        add(rows, "WARN", f"evidence:{gap}", "must be captured from staging/production provider; no secret values required")

    width = max(len(name) for _, name, _ in rows)
    counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
    for status, name, detail in rows:
        counts[status] = counts.get(status, 0) + 1
        print(f"{status:<4} {name:<{width}}  {detail}")
    print("")
    print(f"Summary: {counts['PASS']} pass, {counts['WARN']} warning/evidence-needed, {counts['FAIL']} fail.")

    if counts["FAIL"] or (args.strict and counts["WARN"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
