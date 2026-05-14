# Verification Staging Go/No-Go Checklist

This checklist is for enabling one verification provider in staging only. Production live provider calls must remain disabled until there is explicit production approval.

Current Phase 15 status: **NO-GO for production live provider calls**. Local validation is green, but real staging provider evidence and real private-media asset proof are still required before production sign-off.

## Required Environment

- `DJANGO_ENV=staging`
- `VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED=true`
- `VERIFICATION_PROVIDER_SANDBOX_ENABLED=true`
- `VERIFICATION_PROVIDER_SANDBOX_NETWORK_ENABLED=true`
- First user-only proof: `VERIFICATION_LIVE_PROVIDER_SUBJECTS=user`
- Institution expansion proof: include only the exact subject being tested, for example:
  - `VERIFICATION_LIVE_PROVIDER_SUBJECTS=user,partner`
  - `VERIFICATION_LIVE_PROVIDER_SUBJECTS=user,health_institution`
  - `VERIFICATION_LIVE_PROVIDER_SUBJECTS=user,education_institution`
  - `VERIFICATION_LIVE_PROVIDER_SUBJECTS=user,shop`
- `VERIFICATION_PROVIDER_LIVE_ALLOWED_ENVS=staging`
- `VERIFICATION_WEBHOOK_SECRET` is set in the staging secret manager.
- `VERIFICATION_WEBHOOK_BASE_URL` points to the public staging API base URL.
- Exactly one provider is selected for the first execution test.
- Provider sandbox credentials are present only in the staging secret manager.

## Go Criteria

- `python3 manage.py check` passes in staging.
- `python3 manage.py verification_provider_readiness` shows the selected provider configured, live calls enabled for only the approved subject types, and sandbox network enabled.
- `python3 manage.py verification_private_media_access_check --asset-id <private-media-asset-id>` proves the staging evidence asset is private and signed-token access works.
- A user verification request creates a `provider_pending` case with redacted `provider_request` and `provider_response`.
- At least one institution verification request creates a `provider_pending` case with redacted `provider_request` and `provider_response`:
  - partner/company KYB, or
  - health institution license/registration verification, or
  - education institution accreditation/registration verification.
- Provider dashboard shows the sandbox request or the replay fixture is accepted.
- `python3 manage.py verification_webhook_replay_fixture --provider <provider> --case-id <case-id> --status approved` produces a signed replay payload.
- Signed webhook replay maps:
  - approved to public `verified_user` and `id_verified` badges;
  - approved partner case to `verified_partner`;
  - approved shop case to `verified_shop`;
  - approved health institution case to `verified_health_institution`;
  - approved education institution case to `verified_education_institution`;
  - rejected to rejected case;
  - needs_more_info to needs-more-info case;
  - unmatched to audited unmatched callback with no raw payload leakage.
- Staff console can filter by all subject types and issue/revoke valid badges for user, shop, partner, health institution, and education institution cases.
- Audit events show redacted payloads only.
- No raw documents, base64 payloads, provider secrets, tokens, or public media URLs are stored in verification models.

## Phase 14 Production Sign-Off Evidence Matrix

| Evidence Item | Required Proof | Phase 14 Status | Owner / Evidence Link |
| --- | --- | --- | --- |
| Backend verification regression tests | `python3 manage.py test apps.verification --keepdb --noinput` passes | Passed locally | TODO_STAGING_EVIDENCE_TICKET |
| Production provider calls disabled | Production settings load with verification live/sandbox-network flags disabled | Passed locally with safe dummy env | TODO_STAGING_EVIDENCE_TICKET |
| Production live call fail-closed | Production settings refuse `VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED=true` | Passed locally | TODO_STAGING_EVIDENCE_TICKET |
| Production sandbox-network fail-closed | Production settings refuse `VERIFICATION_PROVIDER_SANDBOX_NETWORK_ENABLED=true` | Passed locally | TODO_STAGING_EVIDENCE_TICKET |
| Provider readiness | Selected provider configured in staging with non-secret output only | Evidence needed in staging | TODO_PROVIDER_OWNER |
| User provider case | User case starts, reaches `provider_pending`, and stores only redacted handoff metadata | Evidence needed in staging | TODO_PROVIDER_OWNER |
| Institution provider case | At least one partner, health, education, or shop case starts and stores only redacted handoff metadata | Evidence needed in staging | TODO_PROVIDER_OWNER |
| Provider callback URL | Provider console sends callback to staging `/api/v1/verification/webhooks/<provider>/` | Evidence needed in staging | TODO_PROVIDER_OWNER |
| Webhook replay mapping | Approved/rejected/needs-info/pending/unmatched callbacks map correctly and reject bad signatures | Local tests passed; staging replay evidence needed | TODO_PROVIDER_OWNER |
| Private media signed access | `verification_private_media_access_check --asset-id <private-media-asset-id>` proves private signed access | Evidence needed in staging | TODO_MEDIA_OWNER |
| Staff console QA | Staff can filter, review, issue/revoke badges, and inspect audit/provider callbacks for all launch subject types | Local launch validation passed; device/staging QA evidence needed | TODO_QA_OWNER |
| Monitoring and alerting | Alert destinations and thresholds are assigned for webhook rejects, provider errors, suspicious signals, and expiry reminders | Evidence needed before production | TODO_OPS_OWNER |
| Rollback path | Owner confirms provider flags can be disabled and provider-pending cases moved to manual review | Checklist exists; owner/evidence needed | TODO_RELEASE_OWNER |

## No-Go Criteria

- Any live provider flag is enabled in production.
- Provider readiness prints configured/live status for production.
- Private media proof cannot prove the evidence asset is private.
- Provider/audit payload includes raw document data, base64, token, secret, phone/email, or identity number values.
- Webhook replay accepts an unsigned or incorrectly signed payload.
- Staff console exposes raw evidence metadata.
- Staff console cannot filter or act on any subject type that is planned for launch.
- Rollback owner and rollback command path are not assigned.

## Phase 13 Evidence Status

- Local backend checks passed for institution sandbox handoff redaction and subject-specific webhook badge mapping.
- Local React Native launch validation passed after staff-console subject/status filter updates.
- Real external provider sandbox calls were not executed locally.
- Real private-media signed-access proof with `--asset-id` was not executed locally because no staging private asset id was available in this environment.
- Phase 14 must capture staging evidence for at least one provider and one institution subject before production sign-off.

## Phase 14 Evidence Status

- Local backend validation passed.
- Local React Native launch validation passed.
- Production settings were verified with safe dummy values:
  - disabled provider live/sandbox-network flags load successfully;
  - `VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED=true` fails closed;
  - `VERIFICATION_PROVIDER_SANDBOX_NETWORK_ENABLED=true` fails closed.
- Staging external provider execution was not performed in this local environment.
- Real staging private-media signed-access proof with `--asset-id` was not performed because no staging private asset id was available locally.
- Current explicit sign-off status remains **NO-GO for production live provider calls** until the evidence matrix above is completed.

## Phase 15 Evidence Status

- Evidence log added: `docs/operations/VERIFICATION_PHASE15_STAGING_EVIDENCE.md`.
- Local provider readiness passed, but all providers reported `configured=false`, `live_calls_enabled=false`, and `sandbox_network_enabled=false`.
- Local private-media readiness command loaded successfully, but no real staging `--asset-id` was available.
- Local signed webhook replay fixture generation passed for:
  - approved;
  - rejected;
  - needs-more-info;
  - provider-pending;
  - unmatched.
- Full backend verification regression suite passed with 17 tests.
- React Native focused verification lint and launch validation passed.
- Real staging provider execution was not performed because approved staging credentials, provider-console access, and sandbox network configuration are not available in this local environment.
- Current explicit sign-off status remains **NO-GO for production live provider calls**.

## Monitoring And Alerting Requirements

Before production live calls are approved, assign alert owners and destinations for:

- `webhook.rejected` audit events above the normal threshold.
- `webhook.unmatched` audit events from unknown provider references.
- provider handoff failures or provider HTTP non-2xx responses.
- cases stuck in `provider_pending` beyond the provider SLA.
- suspicious verification signals from `/api/v1/verification/staff/suspicious-signals/`.
- expiry reminders failing to run on schedule.

Recommended initial thresholds:

- page immediately when production live provider calls are enabled unexpectedly;
- alert within 15 minutes for repeated webhook signature failures from the same source;
- alert daily for stale provider-pending cases older than 24 hours;
- alert daily for failed expiry reminder jobs.

## Rollback

1. Set `VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED=false`.
2. Set `VERIFICATION_PROVIDER_SANDBOX_NETWORK_ENABLED=false`.
3. Keep webhook signature verification enabled.
4. Move open provider-pending cases to manual review from the staff console or backend staff API.
5. Keep audit events; do not delete provider callback records.

## Production Approval Rule

Production live provider calls require all of the following:

1. Completed evidence matrix with release-ticket links.
2. Explicit written approval from the release owner and security owner.
3. Confirmed rollback owner and rollback test.
4. Provider secret rotation owner assigned.
5. Monitoring destination tested.
6. A single-provider, single-subject rollout plan before expanding to all subjects.
