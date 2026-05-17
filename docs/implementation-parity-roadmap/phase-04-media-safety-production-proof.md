# Phase 04 - Media Safety And Christian Moderation Production Proof

Date: 2026-05-17

## Goal

Tighten and prove the launch-safe media safety gate across KIS upload surfaces while preserving existing user flows. KIS must not allow pornographic, sexually explicit, exploitative, unsafe, or high-risk files through DMs, group/partner messages, feeds/channels, comments, profile media, commerce, education, health, verification, or public/embed surfaces.

## Scope Completed

- Tightened the central upload validation policy in `apps.media.safety`.
- Added an explicit allowed extension policy:
  - default safe extensions for images, videos, audio, documents, and archives;
  - configurable by `MEDIA_SAFETY_ALLOWED_EXTENSIONS`.
- Removed `application/octet-stream` from the default allowed MIME policy and explicitly blocks generic binary uploads.
- Added MIME/extension compatibility checks so a file such as `photo.pdf` with `image/jpeg` is rejected before storage.
- Kept high-risk executable/script extensions blocked before storage.
- Kept upload size validation before storage.
- Preserved existing quarantine/review behavior:
  - production-style `MEDIA_EXPLICIT_SCAN_REQUIRED=True` creates `pending_review` scans;
  - quarantined uploads do not receive public URLs;
  - user-safe review/block messages are returned;
  - no raw storage path or secret is returned in safety metadata.
- Confirmed media safety alerts feed the staff moderation operations queue.
- Added a read-only launch verifier:
  - `python3 manage.py verify_media_safety_launch`
  - optional strict mode: `python3 manage.py verify_media_safety_launch --strict`
- Updated `.env.example` with `MEDIA_SAFETY_ALLOWED_EXTENSIONS`.

## Files Changed

- `apps/media/safety.py`
- `apps/media/tests.py`
- `apps/media/management/commands/verify_media_safety_launch.py`
- `config/settings/base.py`
- `.env.example`
- `docs/implementation-parity-roadmap/phase-04-media-safety-production-proof.md`
- `docs/implementation-parity-roadmap/status.md`
- `docs/BUILD_STATE.md`

## Validation

Passed:

- `python3 -m py_compile apps/media/safety.py apps/media/views.py apps/media/tests.py apps/media/management/commands/verify_media_safety_launch.py config/settings/base.py`
- `python3 manage.py check`
- `python3 manage.py makemigrations --check --dry-run`
- `python3 manage.py test apps.media.tests.PrivateMediaAccessTests apps.media.tests.MediaSafetyUploadTests apps.moderation.tests.ModerationAccessBoundaryTests --noinput --keepdb`
  - PostgreSQL-backed: 16 tests passed.
- `python3 manage.py verify_media_safety_launch`
  - 0 fail, 1 warning.
- React Native `npx eslint src/services/mediaSafety.ts --quiet`
- React Native `npm run typecheck -- --pretty false`
- Nest `pnpm tsc --noEmit --pretty false --incremental false`

## Validation Notes

- `python3 manage.py verify_media_safety_launch` produced one local warning because the command could not read the live media safety scan queue from the configured database in this sandboxed command context:
  - `OperationalError`
  - config checks still passed;
  - PostgreSQL-backed media/moderation tests did pass with `--keepdb`.
- This warning should be cleared in staging by running the same command in the deployed environment with database access.

## Existing Coverage Confirmed

- Central `/uploads/file` path validates file type, MIME, extension, size, scan/quarantine, and user-safe response.
- Chat/DM uploads use the central upload path and include conversation/client/device audit context.
- Partner, Bible, health, and broadcast serializers/views reject attachments that are still pending review, blocked, or failed.
- Commerce upload paths call `validate_upload_file_safety`.
- Verification serializers reject raw document bodies and expect private media references only.
- Staff moderation queue includes media safety scans and supports approve/block/review actions with audit logs.

## Remaining Risks

| Priority | Risk |
|---|---|
| P0 | Staging must prove every actual upload entry point uses `/uploads/file` or calls `validate_upload_file_safety` before storage. |
| P0 | Live explicit-content provider calls remain disabled; production can launch only with quarantine/manual-review mode or after provider evidence. |
| P0 | Real-device QA must prove quarantined media is not displayed or sent in DMs, feeds/channels, partner groups, commerce, education, health, profile, or verification flows. |
| P0 | Staff moderation queue must be tested with real staging uploads and reviewer accounts. |
| P1 | Public embeds must be manually tested to confirm private/unlisted/quarantined assets never expose raw storage paths. |
| P1 | Existing legacy model-specific file fields should be gradually migrated behind the central media gate where any path still writes directly. |

## Phase 05 Prompt

```text
Please implement Phase 05 of the KIS 100% Implementation and 80%+ Global Parity roadmap without using git commands. Focus on Payments And USD-Only Commerce Launch Proof. Use the Phase 00 launch scope and Phase 01-04 evidence to verify and tighten USD-only direct-provider payment readiness across Commerce/Market, service bookings, Education, Health, subscriptions/upgrades placeholders, receipts, and historical wallet/KIS promotional-credit displays. Confirm KIS promotional credits remain non-cash, non-transferable, non-withdrawable, and not exchange-rated; confirm legacy wallet checkout/deposit/transfer/conversion flags remain disabled; verify Flutterwave/direct-payment intent status handling, callback/webhook idempotency, audit logs, and rollback evidence where safe. Prefer PostgreSQL-backed Django tests; if Postgres or environment setup blocks validation, record the exact blocker and move on. Preserve existing UI behavior, do not enable live charges or expose secrets, run safe Django/Nest/React Native validation, update docs/implementation-parity-roadmap/status.md and docs/BUILD_STATE.md, and give the best prompt for Phase 06.
```
