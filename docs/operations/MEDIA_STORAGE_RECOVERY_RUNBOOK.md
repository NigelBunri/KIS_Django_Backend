# Media And Upload Storage Recovery Runbook

Use this for public/private media rollback, corrupted uploads, accidental public
exposure, and object-storage recovery.

## Ownership

- Media owner: `TODO_MEDIA_OWNER`
- Storage provider: `TODO_STORAGE_PROVIDER`
- Media bucket/container: `TODO_MEDIA_BUCKET`
- CDN/media domain: `TODO_MEDIA_DOMAIN`
- Incident channel: `TODO_ESCALATION_CHANNEL`

## Media Policy Reminder

- Explicit private media must not be served directly from public `/uploads/`.
- Private media should use authenticated proxy access or short-lived signed URLs.
- Production Nest static `/uploads/` serving should remain disabled unless the
  storage path contains only deliberate public files.

## Backup Policy

Minimum launch policy:

- Versioning or soft delete enabled for object storage when provider supports it.
- Lifecycle retention for deleted objects: at least 7 days.
- Access logs enabled for private/public media paths.
- CDN cache purge access limited to operators.

## Restore Test Procedure

1. Pick a non-sensitive test object.
2. Delete or overwrite it in a staging bucket.
3. Restore using provider versioning/backup.
4. Verify checksum or file size.
5. Verify app can read the restored object through the intended path.
6. Verify private object is denied without auth.
7. Record restore time and operator.

## Accidental Public Exposure Response

1. Declare security incident.
2. Disable public serving path:

- Nest: ensure `SERVE_UPLOADS_PUBLICLY=0`.
- CDN/object storage: block public access or remove public bucket policy.

3. Purge CDN cache for exposed paths.
4. Rotate signed URL keys/secrets if URL signing was involved.
5. Identify exposed object keys and owners.
6. Move or reclassify objects:

- public files: keep in public bucket/path;
- private files: move to private bucket/path or mark private metadata.

7. Review access logs for external requests.
8. Notify affected users if required by policy/law.

## Corrupted Upload Recovery

1. Mark affected media rows as unavailable/blocked.
2. Restore object from provider version history if available.
3. If unavailable, request user re-upload.
4. Re-run malware scan or quarantine review.
5. Restore `status=ready` only after verification.

## Media Rollback After Deploy

If a release changes media URL generation and breaks rendering:

1. Roll back application release.
2. Keep object storage unchanged unless exposure or corruption occurred.
3. Purge CDN cache only if bad URLs were cached.
4. Verify:

- private file without auth returns denied;
- private owner access works;
- public image/video loads;
- uploads still validate MIME/extension/size.

## Evidence To Record

- Object keys affected.
- Public/private classification.
- Exposure window.
- CDN purge IDs.
- Storage policy before/after.
- Users/partners affected.
- Restore validation results.
