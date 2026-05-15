# KIS 80 Percent Launch Blocker Register

Status: Phase 28 foundation.

Use this register during staging. Do not delete rows; append updates with dates and owners.

## Active Blockers

| ID | Severity | Area | Blocker | Owner | Status | Evidence / Notes |
| --- | --- | --- | --- | --- | --- | --- |
| P0-001 | P0 | Production evidence | Real staging/production evidence is not attached yet | Release owner | Open | Use `KIS_120_STAGING_EVIDENCE_TEMPLATE.md` |
| P0-002 | P0 | Backup/rollback | Backup/restore and rollback proof must be attached before GO | Operations | Open | Use backup and rollback runbooks |
| P0-003 | P0 | Payments | Flutterwave callback proof is required for enabled payment flows | Payments owner | Open | Success/failure/cancelled/duplicate/unmatched callbacks |
| P0-004 | P0 | Media safety | Explicit media safety and child/youth safety proof must be attached | Safety owner | Open | Upload gate, quarantine, moderation queue |
| P1-001 | P1 | Device lab | iOS and Android smoke evidence must be captured | QA owner | Open | Use device-lab checklist |

## Closed Blockers

| ID | Severity | Area | Resolution | Evidence |
| --- | --- | --- | --- | --- |

## Triage Rules

- P0 blocks launch.
- P1 blocks launch unless the whole affected feature is disabled by flag and removed from launch scope.
- P2 may ship only with owner, workaround, and follow-up date.
- P3 does not block 80% launch.
