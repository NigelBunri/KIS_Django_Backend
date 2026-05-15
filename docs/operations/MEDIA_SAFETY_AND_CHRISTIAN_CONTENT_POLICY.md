# KIS Media Safety And Christian Content Policy

Last updated: 2026-05-14

## Purpose

KIS is a Christian, family-safe platform. User-uploaded media must be protected by technical controls, not only by community text.

This runbook explains the Phase 02 media safety architecture added for:

- DMs and chat attachments
- feeds and channels
- comments
- profile media
- partner spaces
- commerce shops/products/services
- education surfaces
- health surfaces
- verification evidence
- future live/embeds/uploads

## Current Implementation

Central backend pieces:

- `apps/media/safety.py`
- `apps/media/models.py::MediaSafetyScan`
- `apps/media/views.py::UploadFileView`
- `apps/media/views.py::MediaSafetyScanViewSet`
- `apps/broadcasts/views.py` safety hooks for feed/profile/video uploads

Frontend pieces:

- `/Users/nigel/dev/KIS/src/services/mediaSafety.ts`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/uploadFileToBackend.ts`
- `/Users/nigel/dev/KIS/src/services/verificationService.ts`

## Policy

The following content must not be uploadable or publishable anywhere on KIS:

- pornography
- sexually explicit images, video, audio, text, links, documents, or live content
- sexualized minors or exploitative/predatory content
- grooming or manipulation
- degrading sexual content
- abuse, harassment, blackmail, scams, impersonation, or unsafe content

Medical, counselling, education, or verification evidence may be sensitive, but must remain private, permissioned, and reviewed under the appropriate workflow.

## Safety States

`MediaSafetyScan.status` supports:

- `pending_review`: upload accepted into quarantine, not safe to show publicly.
- `passed`: scan/review accepted.
- `blocked`: upload rejected or later blocked.
- `failed`: scanner failed and needs staff attention.
- `not_configured`: local/dev or scan-not-required state.

`quarantine=True` means the upload should not be exposed publicly.

## Environment Controls

```bash
MEDIA_SAFETY_ENABLED=True
MEDIA_EXPLICIT_SCAN_REQUIRED=False
MEDIA_SAFETY_PROVIDER=stub
MEDIA_SAFETY_LIVE_PROVIDER_CALLS_ENABLED=False
MEDIA_SAFETY_MAX_UPLOAD_BYTES=52428800
MEDIA_SAFETY_ALLOWED_MIME_TYPES=
MEDIA_SAFETY_ALLOWED_MIME_PREFIXES=
MEDIA_SAFETY_BLOCKED_EXTENSIONS=
```

Production recommendation:

```bash
MEDIA_SAFETY_ENABLED=True
MEDIA_EXPLICIT_SCAN_REQUIRED=True
MEDIA_SAFETY_PROVIDER=<approved-provider>
MEDIA_SAFETY_LIVE_PROVIDER_CALLS_ENABLED=False
```

Enable live provider calls only after staging credentials, redacted logging, callback validation, and manual-review workflows are proven.

## Provider Adapter Options

Phase 02 adds provider-neutral stubs only. Future adapters can be implemented for:

- AWS Rekognition moderation labels
- Google Vision SafeSearch
- Hive moderation
- Sightengine
- Cloudflare Images/Stream moderation
- a self-hosted classifier plus human review

Provider adapters must return the same decision shape used by `MediaSafetyDecision`.

## Privacy Rules

- Do not log raw media.
- Do not log private storage paths in external provider logs.
- Do not log credentials or signed URLs.
- Store provider references, scores, categories, and safe redacted metadata only.
- Verification evidence must remain private media references, not raw documents in verification models.

## QA Checklist

- Upload normal image in local mode: accepted with `not_configured` when explicit scan is disabled.
- Upload normal image with `MEDIA_EXPLICIT_SCAN_REQUIRED=True`: accepted but quarantined as `pending_review`.
- Upload dangerous extension such as `.sh`: rejected before storage and no scan row created.
- Confirm `GET /api/v1/media-safety-scans/` returns only the current user's scans for non-staff users.
- Confirm staff users can inspect scan rows in Django admin.
- Confirm frontend upload surfaces show a user-safe review/blocked message.

## Next Hardening Steps

- Add a real provider adapter behind staging-only flags.
- Add staff review actions to pass/block quarantined uploads.
- Attach `MediaSafetyScan` to `MediaAsset` rows where uploads are normalized into durable assets.
- Emit notifications/admin alerts for blocked or high-risk uploads.
- Add live-stream frame sampling before any live stream is public.
- Add comment/text safety scanning for sexually explicit text and predatory behavior.

