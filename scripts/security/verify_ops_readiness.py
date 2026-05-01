#!/usr/bin/env python3
"""
Verify that KIS operational recovery runbooks exist and contain the required
sections/placeholders. This does not connect to production or read secrets.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OPS_DIR = ROOT / "docs" / "operations"

REQUIRED_DOCS = {
    "PRODUCTION_OPERATIONS_OVERVIEW.md": [
        "Systems Covered",
        "Provider Placeholders",
        "Recovery Targets",
        "Required Runbooks",
        "TODO_PROVIDER_NAME",
    ],
    "DATABASE_BACKUP_RESTORE_RUNBOOK.md": [
        "Backup Policy",
        "Pre-Deploy Backup Checklist",
        "Restore Test Procedure",
        "Emergency Restore Procedure",
        "Evidence To Record",
    ],
    "APPLICATION_ROLLBACK_RUNBOOK.md": [
        "Rollback Triggers",
        "Django Rollback",
        "Nest Rollback",
        "Environment Rollback",
        "Post-Rollback Checks",
    ],
    "MEDIA_STORAGE_RECOVERY_RUNBOOK.md": [
        "Media Policy Reminder",
        "Backup Policy",
        "Restore Test Procedure",
        "Accidental Public Exposure Response",
        "Evidence To Record",
    ],
    "SECRET_ROTATION_RUNBOOK.md": [
        "Secrets Covered",
        "Planned Rotation Steps",
        "Emergency Rotation Steps",
        "Service-Specific Notes",
        "Evidence To Record",
    ],
    "SECURITY_INCIDENT_RESPONSE_RUNBOOK.md": [
        "Severity Levels",
        "First 15 Minutes",
        "Investigation Checklist",
        "Containment Playbooks",
        "Post-Incident Review",
    ],
}


def main() -> int:
    checks: list[tuple[str, bool, str]] = []
    for filename, required_terms in REQUIRED_DOCS.items():
        path = OPS_DIR / filename
        if not path.exists():
            checks.append((filename, False, "missing file"))
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        missing = [term for term in required_terms if term not in content]
        checks.append(
            (
                filename,
                not missing,
                "all required sections present" if not missing else f"missing: {', '.join(missing)}",
            )
        )

    roadmap = ROOT / "docs" / "SECURITY_HARDENING_ROADMAP.md"
    build_state = ROOT / "docs" / "BUILD_STATE.md"
    checks.append(
        (
            "roadmap references phase 6",
            roadmap.exists() and "Phase 6" in roadmap.read_text(encoding="utf-8", errors="ignore"),
            "SECURITY_HARDENING_ROADMAP.md should track Phase 6",
        )
    )
    checks.append(
        (
            "build state exists",
            build_state.exists(),
            "BUILD_STATE.md should be present for handoff continuity",
        )
    )

    width = max(len(name) for name, _, _ in checks)
    failures = 0
    for name, ok, detail in checks:
        status = "PASS" if ok else "FAIL"
        print(f"{status:<4} {name:<{width}}  {detail}")
        if not ok:
            failures += 1
    print("")
    print(f"Summary: {len(checks) - failures}/{len(checks)} operational readiness checks passing.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
