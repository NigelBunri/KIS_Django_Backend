#!/usr/bin/env python3
"""
Verify Phase 7 launch-blocker artifacts exist and contain required sections.
This is documentation/config readiness only; it does not rotate or read secrets.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OPS_DIR = ROOT / "docs" / "operations"

REQUIRED = {
    "PHASE7_LAUNCH_BLOCKER_REGISTER.md": [
        "Blocker Summary",
        "Secret Scan Findings From Phase 5",
        "Dependency Audit Findings",
        "React Native Typecheck Baseline",
        "Provider-Specific Readiness",
    ],
    "FIREBASE_CREDENTIAL_HANDLING.md": [
        "Firebase Admin Credentials",
        "React Native Firebase Mobile Config",
        "Verification Checklist",
    ],
    "DEPENDENCY_REMEDIATION_PLAN.md": [
        "Nest Plan",
        "React Native Plan",
        "Acceptance Process",
    ],
    "REACT_NATIVE_TYPECHECK_TRIAGE.md": [
        "Current Failure Groups",
        "Triage Order",
        "Guardrails",
    ],
    "PROVIDER_LAUNCH_READINESS_CHECKLIST.md": [
        "Provider Identity",
        "Required Evidence",
        "Go / No-Go Rule",
    ],
}


def main() -> int:
    checks: list[tuple[str, bool, str]] = []
    for filename, terms in REQUIRED.items():
        path = OPS_DIR / filename
        if not path.exists():
            checks.append((filename, False, "missing file"))
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        missing = [term for term in terms if term not in text]
        checks.append(
            (
                filename,
                not missing,
                "all required sections present" if not missing else f"missing: {', '.join(missing)}",
            )
        )

    for rel_path, term in (
        ("docs/SECURITY_HARDENING_ROADMAP.md", "Phase 7"),
        ("docs/BUILD_STATE.md", "Phase 7"),
    ):
        path = ROOT / rel_path
        ok = path.exists() and term in path.read_text(encoding="utf-8", errors="ignore")
        checks.append((rel_path, ok, f"contains {term}" if ok else f"missing {term}"))

    width = max(len(name) for name, _, _ in checks)
    failures = 0
    for name, ok, detail in checks:
        status = "PASS" if ok else "FAIL"
        print(f"{status:<4} {name:<{width}}  {detail}")
        if not ok:
            failures += 1
    print("")
    print(f"Summary: {len(checks) - failures}/{len(checks)} Phase 7 readiness checks passing.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
