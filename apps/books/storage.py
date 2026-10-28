"""
Manuscript files are never served through MEDIA_URL. They're written via
the project's existing `default_storage` (same private Supabase/S3 backend
every other sensitive upload uses — see apps.media.storage_backends), and
read back only through this module's short-lived signed URL, generated
only after the caller has already been authorized by the view.
"""
from __future__ import annotations

from datetime import timedelta

from django.core.files.storage import default_storage
from django.utils import timezone

MANUSCRIPT_SIGNED_URL_TTL_SECONDS = 300
IMAGE_SIGNED_URL_TTL_SECONDS = 900


def sign_image_for_download(storage_key: str) -> dict | None:
    """Same pattern as sign_manuscript_for_download, for a BookImageAsset
    — the file field is never exposed as a permanent URL (unlike
    Book.cover_url), so a reader's chapter image always routes through a
    short-lived signed link generated only after an access check (see
    apps.books.views.BookImageAssetDownloadView)."""
    presign = getattr(default_storage, "generate_presigned_get", None)
    if not callable(presign):
        return None
    ttl = IMAGE_SIGNED_URL_TTL_SECONDS
    url = presign(storage_key, ttl)
    return {"url": url, "expiresAt": (timezone.now() + timedelta(seconds=ttl)).isoformat()}


def sign_manuscript_for_download(storage_key: str) -> dict | None:
    """Returns {"url", "expiresAt"} for authorized, short-lived access to
    the original manuscript file, or None if the configured storage
    backend has no presigned-GET support (local/dev disk storage) — in
    which case there is deliberately no public fallback URL; callers
    should tell the reviewer the original file isn't retrievable in this
    environment rather than exposing a permanent link."""
    presign = getattr(default_storage, "generate_presigned_get", None)
    if not callable(presign):
        return None
    ttl = MANUSCRIPT_SIGNED_URL_TTL_SECONDS
    url = presign(storage_key, ttl)
    return {"url": url, "expiresAt": (timezone.now() + timedelta(seconds=ttl)).isoformat()}
