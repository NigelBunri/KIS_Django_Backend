# Phase 06 - Verification, Trust Badges, And Identity Launch Proof

Date: 2026-05-17

## Scope

This phase verified and tightened the launch-safe verification and trust-badge foundation for:

- users;
- shops;
- partners;
- health institutions;
- education institutions;
- channel/creator and publisher trust readiness.

The launch policy remains:

- Live verification provider calls stay disabled by default.
- Sandbox network calls stay disabled unless explicitly enabled in staging with approved provider credentials.
- Evidence models store private media references and safe metadata only.
- Raw documents, base64 blobs, provider secrets, private media ids, and raw provider payloads must not be exposed in public or staff API payloads.
- Public badge summaries must expose only safe badge/status labels.
- Staff review, audit, provider callback inspection, badge issue/revoke, and expiry actions must stay staff-only.

## Changes Made

- Added a read-only verification launch verifier:
  - `apps/verification/management/commands/verify_verification_launch.py`
- The verifier checks, without printing secrets or making provider calls:
  - live provider calls are disabled;
  - sandbox network calls are disabled;
  - webhook secret presence is detected without printing values;
  - provider payload redaction works for secrets/raw documents;
  - Dojah, Sumsub, and Smile ID live calls are disabled;
  - required staff/public verification URLs resolve;
  - required launch subject types exist for user/shop/partner/health/education.
- Hardened staff audit serialization:
  - `StaffVerificationAuditEventSerializer` now defensively redacts metadata at serialization time.
- Added focused regression tests for:
  - staff audit redaction of provider secrets/raw documents;
  - verification launch verifier default pass state.

## Existing Launch-Critical Coverage Confirmed

- User verification:
  - status;
  - start case;
  - private evidence metadata;
  - raw evidence rejection;
  - staff/manual review;
  - `verified_user` / `id_verified` badges.
- Shop verification:
  - existing shop verification request syncs to centralized verification source of truth.
- Partner verification:
  - centralized partner cases;
  - KYB metadata references;
  - `verified_partner`, `verified_organization`, and `official_partner` badge path.
- Health verification:
  - centralized institution cases;
  - medical license/accreditation metadata references;
  - `verified_health_institution` and `licensed_provider` badge path.
- Education verification:
  - centralized institution cases;
  - accreditation/certification metadata references;
  - `verified_education_institution` and `accredited_education` badge path.
- Provider webhook mapping:
  - signed callback required;
  - approved/rejected/needs-info/provider-pending/unmatched status handling tested;
  - provider payload redaction tested.
- Badge lifecycle:
  - issue;
  - revoke;
  - expire;
  - expiry reminder dry-run.
- Public trust summaries:
  - no raw document/provider payload exposure.
- Staff queues:
  - staff-only access boundary tested.

## Files Changed

- `apps/verification/serializers.py`
- `apps/verification/tests.py`
- `apps/verification/management/commands/verify_verification_launch.py`
- `docs/implementation-parity-roadmap/phase-06-verification-trust-identity-launch-proof.md`
- `docs/implementation-parity-roadmap/status.md`
- `docs/BUILD_STATE.md`

## Validation

Passed:

- `python3 -m py_compile apps/verification/serializers.py apps/verification/tests.py apps/verification/management/commands/verify_verification_launch.py apps/verification/views.py apps/verification/services.py apps/verification/providers.py`
- `python3 manage.py check`
- `python3 manage.py makemigrations --check --dry-run`
- `python3 manage.py verify_verification_launch --include-counts`
  - Guardrails ready: `True`
  - 8 pass / 0 fail / 4 warnings.
- `python3 manage.py verification_provider_readiness`
  - Dojah configured=false, live_calls_enabled=false, sandbox_network_enabled=false.
  - Sumsub configured=false, live_calls_enabled=false, sandbox_network_enabled=false.
  - Smile ID configured=false, live_calls_enabled=false, sandbox_network_enabled=false.
- `python3 manage.py test apps.verification.tests.UserVerificationFlowTests apps.verification.tests.StaffVerificationOperationsTests --noinput --keepdb`
  - PostgreSQL-backed: 21 tests passed.
- `npm run typecheck -- --pretty false`
- `pnpm tsc --noEmit --pretty false --incremental false`

## Warnings And Blockers

| Priority | Blocker / warning |
|---|---|
| P0 | `VERIFICATION_WEBHOOK_SECRET` is not configured in the local command environment. Staging/production must configure it and prove signed callback replay. |
| P0 | `verify_verification_launch --include-counts` could not read local verification database counts due `OperationalError`; staging should rerun with database access. |
| P1 | Channel/creator verification is not a dedicated first-launch subject type. For launch, channel trust should inherit user/partner/institution verification, or a dedicated subject type must be approved later. |
| P1 | Bible/KCAN publisher verification is not a dedicated first-launch subject type. For launch, publisher trust should map to partner verification, or a dedicated publisher subject type must be approved later. |
| P1 | Provider sandbox evidence for one real user and one real institution subject is still required before enabling any live provider path. |

## Launch Evidence Still Needed

- Signed webhook replay with staging `VERIFICATION_WEBHOOK_SECRET`.
- Dojah/Sumsub/Smile sandbox evidence for:
  - one user verification case;
  - one institution verification case.
- Private media signed-access proof for verification evidence.
- Staff review/badge issue/revoke/audit QA on staging.
- Public badge display QA across:
  - profile;
  - shop;
  - partner;
  - health institution;
  - education institution;
  - channel/creator surfaces;
  - Bible/KCAN publisher surfaces.
- Decision on whether channel/creator and Bible/KCAN publisher need dedicated verification subject types before launch or can inherit user/partner/institution trust.

## Phase 07 Prompt

```text
Please implement Phase 07 of the KIS 100% Implementation and 80%+ Global Parity roadmap without using git commands. Focus on Broadcast/Channels, Feeds, And Public Content Launch Proof. Use Phase 00-06 evidence to verify channel creation, channel-scoped content creation, legacy broadcast feed compatibility, subscribe/bell behavior, playlists, comments, saves, watch history, broadcast/unbroadcast state, public/private/unlisted visibility, embeds/oEmbed safety, channel trust badge display, media safety gating before publish, and report/moderation hooks. Prefer PostgreSQL-backed Django tests; if Postgres, Redis, or environment setup blocks validation, record the exact blocker and move on. Preserve existing UI behavior, do not expose private media paths or secrets, keep risky live streaming/public indexing features flagged unless launch evidence exists, run safe Django/Nest/React Native validation, update docs/implementation-parity-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 08.
```
