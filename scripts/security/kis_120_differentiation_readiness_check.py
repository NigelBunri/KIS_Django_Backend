#!/usr/bin/env python3
"""Phase 30 120% differentiation readiness checker.

This checker verifies that the final differentiation layer is documented and
kept behind safe launch gates. It does not enable experimental features and
does not print secrets.
"""

from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

REQUIRED_DOCS = [
    "docs/operations/KIS_120_PERCENT_DIFFERENTIATION_STRATEGY.md",
    "docs/operations/KIS_120_PERCENT_DIFFERENTIATION_RELEASE_SLICES.md",
    "docs/operations/KIS_95_PERCENT_CATEGORY_PARITY_PUSH.md",
    "docs/operations/KIS_95_PERCENT_PARITY_GAP_REGISTER.md",
    "docs/operations/KIS_80_PERCENT_LAUNCH_CUT.md",
    "docs/operations/KIS_120_STAGING_EVIDENCE_TEMPLATE.md",
]

REQUIRED_STRATEGY_PHRASES = [
    "Spiritual Growth OS",
    "Kingdom Impact Dashboard",
    "Creator Institution Ecosystem",
    "Family-Safe Recommendations",
    "Live Ministry Learning Commerce Health",
    "Christian AI Companion",
    "Global Low-Bandwidth Excellence",
    "Royal UX Memory System",
    "Launch Evidence Criteria",
    "Default No-Go Conditions",
]

REQUIRED_SLICE_IDS = [
    "D120-SG-001",
    "D120-KI-001",
    "D120-ECO-001",
    "D120-REC-001",
    "D120-LIVE-001",
    "D120-AI-001",
    "D120-LB-001",
    "D120-UX-001",
]

MUST_BE_FALSE_FOR_80 = [
    "KIS_PARITY_95_FEATURES_ENABLED",
    "KIS_DIFFERENTIATION_120_FEATURES_ENABLED",
    "KIS_EXPERIMENTAL_120_FEATURES_ENABLED",
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


def read(rel: str) -> str:
    path = ROOT / rel
    return path.read_text(encoding="utf-8") if path.exists() else ""


def main() -> int:
    rows: list[tuple[str, bool, str]] = []

    for rel in REQUIRED_DOCS:
        path = ROOT / rel
        add(rows, f"doc:{rel}", path.exists(), "present" if path.exists() else "missing")

    strategy = read("docs/operations/KIS_120_PERCENT_DIFFERENTIATION_STRATEGY.md")
    for phrase in REQUIRED_STRATEGY_PHRASES:
        add(rows, f"phrase:strategy:{phrase}", phrase in strategy, "required differentiation section")

    slices = read("docs/operations/KIS_120_PERCENT_DIFFERENTIATION_RELEASE_SLICES.md")
    for slice_id in REQUIRED_SLICE_IDS:
        add(rows, f"slice:{slice_id}", slice_id in slices, "required release slice")

    mode = os.environ.get("KIS_LAUNCH_CUT_MODE", "80").strip() or "80"
    add(rows, "env:KIS_LAUNCH_CUT_MODE_valid", mode in {"80", "95", "120"}, f"current={mode}")
    if mode == "80":
        enabled = [name for name in MUST_BE_FALSE_FOR_80 if env_bool(name)]
        add(rows, "env:80_cut_keeps_120_features_disabled", not enabled, f"{len(enabled)} gated flag(s) enabled")
    elif mode in {"95", "120"}:
        add(rows, "env:higher_mode_requires_external_evidence", True, "documented evidence gate; release ticket links are external")

    width = max(len(name) for name, _, _ in rows)
    failures = [row for row in rows if not row[1]]
    for name, ok, detail in rows:
        print(f"{'PASS' if ok else 'FAIL':4} {name.ljust(width)}  {detail}")
    print("")
    print(f"Summary: {len(rows) - len(failures)}/{len(rows)} checks passing.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
