# Verification Phase 15 Staging Evidence Log

Status: **NO-GO for production live provider calls**

This document captures Phase 15 evidence for verification provider staging execution. It must not contain provider secrets, raw identity data, passport images, document scans, service account JSON, live tokens, phone/email values, or raw provider payloads.

## Evidence Ticket

- Release ticket: `TODO_RELEASE_TICKET`
- Evidence owner: `TODO_OWNER`
- Date: `2026-05-06`
- Selected provider: `dojah`
- Environment tested: local readiness only
- Production live provider calls: disabled
- Staging sandbox credentials available to this environment: no
- Staging private media asset id available to this environment: no

## Local Evidence Captured

| Evidence Item | Command / Source | Result |
| --- | --- | --- |
| Provider readiness | `python3 manage.py verification_provider_readiness` | Passed locally; providers reported unconfigured, live calls disabled, sandbox network disabled. |
| Private media command readiness | `python3 manage.py verification_private_media_access_check` | Passed no-asset readiness mode; real staging `--asset-id` still required. |
| Django system check | `python3 manage.py check` | Passed. |
| Migration dry run | `python3 manage.py makemigrations --check --dry-run` | Passed with no model changes. |
| Verification regression suite | `python3 manage.py test apps.verification --keepdb --noinput` | Passed with 17 tests. |
| Webhook replay fixture generation | `verification_webhook_replay_fixture` for approved, rejected, needs-more-info, provider-pending, unmatched | Passed locally with non-production throwaway secret. |
| React Native focused lint | `npx eslint src/components/verification/VerificationStaffConsole.tsx src/services/verificationService.ts --quiet` | Passed. |
| React Native launch validation | `npm run ci:launch` | Passed; production npm audit found 0 vulnerabilities. |

## Staging Evidence Still Required

| Evidence Item | Required Proof | Status |
| --- | --- | --- |
| Provider sandbox readiness | Selected provider shows configured in staging without printing secrets. | Blocked: staging credentials are not available locally. |
| User sandbox case | A real staging user verification case reaches `provider_pending` and stores only redacted provider handoff metadata. | Blocked: staging credentials/network/provider console are not available locally. |
| Institution sandbox case | A real staging partner, health, education, or shop verification case reaches `provider_pending` and stores only redacted provider handoff metadata. | Blocked: staging credentials/network/provider console are not available locally. |
| Private media signed access | `python3 manage.py verification_private_media_access_check --asset-id <private-media-asset-id>` proves a real staging evidence file is private and signed-token accessible. | Blocked: no staging private MediaAsset id available locally. |
| Provider callback URL | Provider dashboard shows the staging webhook URL and sends a callback to `/api/v1/verification/webhooks/<provider>/`. | Blocked: provider-console access unavailable locally. |
| Staff console device QA | Staff filters, review actions, badge issue/revoke, provider callback inspection, and audit views work on a staging build/device. | Blocked: no staging build/device evidence captured locally. |
| Monitoring | Alert destination receives test events for webhook rejects, provider failures, stale provider-pending cases, suspicious signals, and expiry reminder failures. | Blocked: monitoring destination not provided locally. |
| Rollback | Release owner confirms provider flags can be disabled and provider-pending cases can be moved to manual review. | Blocked: owner sign-off not available locally. |

## Required Staging Commands

Run these in staging after approved sandbox credentials and a private media asset are available:

```bash
python3 manage.py check
python3 manage.py verification_provider_readiness
python3 manage.py verification_private_media_access_check --asset-id <private-media-asset-id>
python3 manage.py verification_webhook_replay_fixture --provider <provider> --case-id <case-id> --status approved
python3 manage.py verification_webhook_replay_fixture --provider <provider> --case-id <case-id> --status rejected
python3 manage.py verification_webhook_replay_fixture --provider <provider> --case-id <case-id> --status needs_more_info
python3 manage.py verification_webhook_replay_fixture --provider <provider> --case-id <case-id> --status provider_pending
python3 manage.py verification_webhook_replay_fixture --provider <provider> --case-id <case-id> --status unmatched
```

## Production Go / No-Go

Current status: **NO-GO**

Reason:

- No real provider sandbox case was executed from this environment.
- No real institution sandbox case was executed from this environment.
- No real staging private media signed-access proof was captured.
- Provider callback URL proof and provider-console evidence are missing.
- Monitoring and rollback sign-off are missing.

Production live provider calls must remain disabled until the missing staging evidence is attached to the release ticket and approved by the release owner and security owner.
