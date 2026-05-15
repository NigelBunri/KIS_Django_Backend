#!/usr/bin/env python3
"""Phase 28 80% launch-cut checker.

This checker is intentionally conservative. It verifies the 80% launch-cut
documents exist and that optional/high-risk features remain disabled unless
approved by separate staging evidence. It does not print secret values.
"""

from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

REQUIRED_DOCS = [
    "docs/operations/KIS_80_PERCENT_LAUNCH_CUT.md",
    "docs/operations/KIS_80_PERCENT_BLOCKER_REGISTER.md",
    "docs/operations/KIS_120_STAGING_QA_RUNBOOK.md",
    "docs/operations/KIS_120_STAGING_EVIDENCE_TEMPLATE.md",
    "docs/operations/KIS_120_GO_NO_GO_SUMMARY.md",
]

REQUIRED_PHRASES = {
    "docs/operations/KIS_80_PERCENT_LAUNCH_CUT.md": [
        "Required 80% Launch Scope",
        "Must Stay Deferred Or Flagged For 80%",
        "Required 80% Flags",
        "80% Blocker Triage",
        "Minimum Go/No-Go Criteria",
        "Path After 80%",
    ],
    "docs/operations/KIS_80_PERCENT_BLOCKER_REGISTER.md": [
        "P0-001",
        "P0-002",
        "P0-003",
        "P0-004",
        "P1-001",
    ],
}

MUST_BE_FALSE = [
    "KIS_PARITY_95_FEATURES_ENABLED",
    "KIS_DIFFERENTIATION_120_FEATURES_ENABLED",
    "KIS_EXPERIMENTAL_120_FEATURES_ENABLED",
    "KIS_LEGACY_WALLET_DEPOSIT_ENABLED",
    "KIS_LEGACY_WALLET_TRANSFER_ENABLED",
    "KIS_LEGACY_CASH_CREDIT_CONVERSION_ENABLED",
    "KIS_LEGACY_COMMERCE_WALLET_CHECKOUT_ENABLED",
    "KIS_LEGACY_EDUCATION_WALLET_CHECKOUT_ENABLED",
    "KIS_LEGACY_HEALTH_WALLET_CHECKOUT_ENABLED",
    "KIS_AI_LIVE_PROVIDER_CALLS_ENABLED",
    "KIS_PUBLIC_WEB_INDEXING_ENABLED",
    "KIS_PUBLIC_REFERRALS_ENABLED",
    "KIS_EMBEDS_ENABLED",
    "VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED",
    "MEDIA_SAFETY_LIVE_PROVIDER_CALLS_ENABLED",
]


def env_bool(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def add(rows: list[tuple[str, bool, str]], name: str, ok: bool, detail: str) -> None:
    rows.append((name, ok, detail))


def main() -> int:
    rows: list[tuple[str, bool, str]] = []

    for rel in REQUIRED_DOCS:
        path = ROOT / rel
        add(rows, f"doc:{rel}", path.exists(), "present" if path.exists() else "missing")

    for rel, phrases in REQUIRED_PHRASES.items():
        path = ROOT / rel
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        for phrase in phrases:
            add(rows, f"phrase:{rel}:{phrase}", phrase in text, "required launch-cut section")

    mode = os.environ.get("KIS_LAUNCH_CUT_MODE", "80").strip() or "80"
    add(rows, "env:KIS_LAUNCH_CUT_MODE", mode == "80", f"current={mode}")

    enabled = [name for name in MUST_BE_FALSE if env_bool(name)]
    add(rows, "env:optional_high_risk_features_disabled", not enabled, f"{len(enabled)} high-risk optional flag(s) enabled")

    payment_provider_ok = all(
        (os.environ.get(name, "flutterwave").strip().lower() or "flutterwave") == "flutterwave"
        for name in (
            "KIS_COMMERCE_DEFAULT_PAYMENT_PROVIDER",
            "KIS_EDUCATION_DEFAULT_PAYMENT_PROVIDER",
            "KIS_HEALTH_DEFAULT_PAYMENT_PROVIDER",
        )
    )
    add(rows, "env:usd_direct_provider_defaults", payment_provider_ok, "commerce/education/health default to Flutterwave")

    width = max(len(name) for name, _, _ in rows)
    failures = [row for row in rows if not row[1]]
    for name, ok, detail in rows:
        print(f"{'PASS' if ok else 'FAIL':4} {name.ljust(width)}  {detail}")
    print("")
    print(f"Summary: {len(rows) - len(failures)}/{len(rows)} checks passing.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
