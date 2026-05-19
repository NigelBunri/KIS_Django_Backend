# KIS Media Upload Storage: Supabase Free/Test Setup

## Decision

Render free web services must not be used as permanent media storage because uploaded files can be lost on restart or redeploy. KIS now supports Supabase Storage as a server-side Django `default_storage` backend when `OBJECT_STORAGE_PROVIDER=supabase` is set.

This keeps uploads flowing through the existing KIS backend so media safety validation, Christian family-safe moderation, private/signed access, and audit logs remain centralized.

## Supabase setup

1. In Supabase, open Storage and create a bucket such as `kis-media`.
2. For test/public feed media, make the bucket public or configure policies that allow public reads for published files.
3. Keep private/verification/DM evidence behind KIS signed download endpoints where possible.
4. Copy the server-only service role key from Supabase project API settings. Do not put this key in the mobile app.

## Render Django environment variables

Set these in Render for Django:

```env
OBJECT_STORAGE_PROVIDER=supabase
SUPABASE_URL=https://jzmamwdatswzglpkdwoi.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<server-only-service-role-key>
SUPABASE_STORAGE_BUCKET=kis-media
SUPABASE_STORAGE_PUBLIC_BUCKET=True
SUPABASE_STORAGE_API_URL=https://jzmamwdatswzglpkdwoi.supabase.co/storage/v1
SUPABASE_STORAGE_TIMEOUT_SECONDS=30
MEDIA_URL=/media/
MEDIA_SIGNED_URL_TTL_SECONDS=300
```

If the bucket is private, set `SUPABASE_STORAGE_PUBLIC_BUCKET=False`; public URLs will not be generated and the app must use KIS signed download URLs for display.

## Backend behavior

- `/uploads/file` still validates MIME, extension, size, and media safety before storage.
- The uploaded object is saved through Django `default_storage`.
- A `MediaAsset` record is created for each upload with provider, visibility, scan status, original name, and context metadata.
- A `MediaSafetyScan` record is linked to the asset.
- Public uploads return `url`/`publicUrl` when not quarantined.
- Private uploads return a short-lived `downloadUrl` when not quarantined.
- Quarantined uploads return no public URL.

## Free-tier warnings

Supabase Storage free quotas are limited. Use compressed images, thumbnails, short videos, and low-bandwidth defaults during testing. Heavy videos and many users will need a paid storage/CDN plan later.

## Smoke test

After Render deploy and env setup:

1. Sign in on the mobile app.
2. Upload a small image in chat or broadcast composer.
3. Confirm the API response includes `attachment.assetId` and either `attachment.url` for public uploads or `attachment.downloadUrl` for private uploads.
4. Open Supabase Storage and confirm the object exists under `uploads/<uuid>/...`.
5. Restart/redeploy Render and confirm the media still displays in the app.
