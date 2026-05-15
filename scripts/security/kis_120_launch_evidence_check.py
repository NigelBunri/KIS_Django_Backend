#!/usr/bin/env python3
"""Phase 27 local evidence checker.

This script intentionally checks only repository runbook/readiness artifacts and
safe environment shapes. It never prints secret values and does not contact
external providers.
"""

from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

REQUIRED_DOCS = [
    "docs/operations/KIS_120_STAGING_QA_RUNBOOK.md",
    "docs/operations/KIS_120_DEVICE_LAB_CHECKLIST.md",
    "docs/operations/KIS_120_STAGING_EVIDENCE_TEMPLATE.md",
    "docs/operations/KIS_120_GO_NO_GO_SUMMARY.md",
    "docs/operations/MEDIA_SAFETY_AND_CHRISTIAN_CONTENT_POLICY.md",
    "docs/operations/MONETIZATION_LEGAL_SAFETY_RUNBOOK.md",
    "docs/operations/AI_ASSISTANCE_SAFETY_RUNBOOK.md",
    "docs/operations/PUBLIC_WEB_GROWTH_RUNBOOK.md",
    "docs/operations/FINANCIAL_PRODUCTION_LAUNCH_SIGNOFF.md",
    "docs/operations/VERIFICATION_STAGING_GO_NO_GO.md",
    "docs/operations/REACT_NATIVE_LAUNCH_QA_CHECKLIST.md",
    "docs/operations/APPLICATION_ROLLBACK_RUNBOOK.md",
    "docs/operations/DATABASE_BACKUP_RESTORE_RUNBOOK.md",
    "docs/operations/SECURITY_INCIDENT_RESPONSE_RUNBOOK.md",
]

REQUIRED_RUNBOOK_PHRASES = {
    "docs/operations/KIS_120_STAGING_QA_RUNBOOK.md": [
        "Django",
        "Nest",
        "React Native",
        "Payments QA",
        "Media And Child Safety QA",
        "Verification And Trust QA",
        "Public Web, Embeds, SEO, Growth QA",
        "Rollback And Recovery QA",
    ],
    "docs/operations/KIS_120_DEVICE_LAB_CHECKLIST.md": [
        "iPhone",
        "Android",
        "Messaging",
        "Broadcast And Channels",
        "Bible And Spiritual Growth",
        "Commerce, Education, Health, Partners",
    ],
    "docs/operations/KIS_120_GO_NO_GO_SUMMARY.md": [
        "NO-GO",
        "Wallet-as-money disabled",
        "Private media protection",
        "Child/youth controls",
        "Backup/restore proof",
    ],
}


def env_bool(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def check(name: str, ok: bool, detail: str, rows: list[tuple[str, bool, str]]) -> None:
    rows.append((name, ok, detail))


def main() -> int:
    rows: list[tuple[str, bool, str]] = []

    for rel in REQUIRED_DOCS:
        path = ROOT / rel
        check(f"doc:{rel}", path.exists(), "present" if path.exists() else "missing", rows)

    for rel, phrases in REQUIRED_RUNBOOK_PHRASES.items():
        path = ROOT / rel
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        for phrase in phrases:
            check(f"phrase:{rel}:{phrase}", phrase in text, "required launch evidence section", rows)

    unsafe_money_flags = [
        "KIS_LEGACY_WALLET_DEPOSIT_ENABLED",
        "KIS_LEGACY_WALLET_TRANSFER_ENABLED",
        "KIS_LEGACY_CASH_CREDIT_CONVERSION_ENABLED",
        "KIS_LEGACY_COMMERCE_WALLET_CHECKOUT_ENABLED",
        "KIS_LEGACY_EDUCATION_WALLET_CHECKOUT_ENABLED",
        "KIS_LEGACY_HEALTH_WALLET_CHECKOUT_ENABLED",
    ]
    enabled_money = [name for name in unsafe_money_flags if env_bool(name)]
    check(
        "env:legacy_wallet_as_money_disabled",
        not enabled_money,
        f"{len(enabled_money)} unsafe legacy flag(s) enabled",
        rows,
    )
    check(
        "env:ai_live_calls_disabled_or_gated",
        not env_bool("KIS_AI_LIVE_PROVIDER_CALLS_ENABLED")
        or (
            bool(os.environ.get("KIS_AI_PROVIDER", "").strip())
            and env_bool("KIS_AI_OUTPUT_MODERATION_REQUIRED")
            and env_bool("KIS_AI_INPUT_REDACTION_REQUIRED")
            and env_bool("KIS_AI_CHILD_SAFE_MODE_REQUIRED")
        ),
        "AI live calls must be disabled or fully gated",
        rows,
    )
    check(
        "env:public_indexing_off_until_qa",
        not env_bool("KIS_PUBLIC_WEB_INDEXING_ENABLED"),
        "indexing should remain off until QA evidence is attached",
        rows,
    )
    check(
        "env:embeds_off_until_qa",
        not env_bool("KIS_EMBEDS_ENABLED"),
        "embeds should remain off until embed QA evidence is attached",
        rows,
    )

    width = max(len(name) for name, _, _ in rows)
    failures = [row for row in rows if not row[1]]
    for name, ok, detail in rows:
        status = "PASS" if ok else "FAIL"
        print(f"{status:4} {name.ljust(width)}  {detail}")
    print("")
    print(f"Summary: {len(rows) - len(failures)}/{len(rows)} checks passing.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
