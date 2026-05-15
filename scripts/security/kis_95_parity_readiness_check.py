#!/usr/bin/env python3
"""Phase 29 95% parity readiness checker.

The checker verifies that the 95% category parity push is documented and gated.
It does not enable parity features and does not print secrets.
"""

from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

REQUIRED_DOCS = [
    "docs/operations/KIS_95_PERCENT_CATEGORY_PARITY_PUSH.md",
    "docs/operations/KIS_95_PERCENT_PARITY_GAP_REGISTER.md",
    "docs/operations/KIS_80_PERCENT_LAUNCH_CUT.md",
    "docs/operations/KIS_120_STAGING_EVIDENCE_TEMPLATE.md",
    "docs/operations/KIS_120_DEVICE_LAB_CHECKLIST.md",
]

REQUIRED_PARITY_PHRASES = [
    "Messaging Parity: WhatsApp / Telegram",
    "Channels Parity: YouTube",
    "Education Parity: Coursera",
    "Commerce Parity: Amazon",
    "Health Parity: Apple Health Plus",
    "Partners Parity: Discord",
    "Bible And Spiritual Growth Parity",
    "95% Release Train",
    "Feature Flags",
    "QA criteria",
    "Risk Controls",
]

REQUIRED_GAP_IDS = [
    "MSG-95-001",
    "CHN-95-001",
    "EDU-95-001",
    "COM-95-001",
    "HLT-95-001",
    "PRT-95-001",
    "BIB-95-001",
    "TRU-95-001",
    "PERF-95-001",
]

MUST_BE_FALSE_FOR_80 = [
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
    "VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED",
    "MEDIA_SAFETY_LIVE_PROVIDER_CALLS_ENABLED",
]


def env_bool(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def add(rows: list[tuple[str, bool, str]], name: str, ok: bool, detail: str) -> None:
    rows.append((name, ok, detail))


def read(rel: str) -> str:
    path = ROOT / rel
    return path.read_text(encoding="utf-8") if path.exists() else ""


def main() -> int:
    rows: list[tuple[str, bool, str]] = []

    for rel in REQUIRED_DOCS:
        path = ROOT / rel
        add(rows, f"doc:{rel}", path.exists(), "present" if path.exists() else "missing")

    parity_text = read("docs/operations/KIS_95_PERCENT_CATEGORY_PARITY_PUSH.md")
    for phrase in REQUIRED_PARITY_PHRASES:
        add(rows, f"phrase:parity:{phrase}", phrase in parity_text, "required parity section")

    gap_text = read("docs/operations/KIS_95_PERCENT_PARITY_GAP_REGISTER.md")
    for gap_id in REQUIRED_GAP_IDS:
        add(rows, f"gap:{gap_id}", gap_id in gap_text, "required category gap id")

    mode = os.environ.get("KIS_LAUNCH_CUT_MODE", "80").strip() or "80"
    add(rows, "env:KIS_LAUNCH_CUT_MODE_valid", mode in {"80", "95"}, f"current={mode}")
    if mode == "80":
        enabled = [name for name in MUST_BE_FALSE_FOR_80 if env_bool(name)]
        add(rows, "env:80_cut_keeps_95_features_disabled", not enabled, f"{len(enabled)} gated flag(s) enabled")
    elif mode == "95":
        add(rows, "env:95_mode_requires_external_evidence", True, "documented evidence gate; script does not verify release ticket links")

    width = max(len(name) for name, _, _ in rows)
    failures = [row for row in rows if not row[1]]
    for name, ok, detail in rows:
        print(f"{'PASS' if ok else 'FAIL':4} {name.ljust(width)}  {detail}")
    print("")
    print(f"Summary: {len(rows) - len(failures)}/{len(rows)} checks passing.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
