# apps/statuses/status_media.py
"""
Status/story-specific wiring on top of the shared direct-to-S3 presigned
upload handshake (apps/media/upload_intent.py). Mirrors the confirm-then-
attach pattern in apps/commerce/media_uploads.py, but simpler: status media
has no pre-existing target to authorize at initiate time (any authenticated
user may post a status for themselves), so the generic
POST /api/v1/media/uploads/initiate/ endpoint is used directly with
context=status_image|status_video|status_audio — no status-specific
initiate view is needed.

The one thing the generic confirm step can't do is content moderation.
StatusCreateSerializer's legacy multipart path runs
scan_upload_for_explicit_content() and records a MediaSafetyScan row before
a status becomes visible; resolve_and_validate_status_media() below
replicates that exact gate for confirmed S3 media, using the confirmed
intent's metadata (filename/content_type/size) instead of file bytes.
apps/media/safety.py's validate_upload_file_safety()/
scan_upload_for_explicit_content() are already metadata-only — the byte
hash produced by hash_upload() is the only piece that needs real bytes, and
it is confirmed audit-only (never read/compared anywhere), so it is simply
left blank for media confirmed through this path.
"""

from __future__ import annotations

from rest_framework.exceptions import ValidationError

from apps.media import upload_intent
from apps.media.models import MediaSafetyScan, MediaUploadIntent
from apps.media.safety import USER_SAFE_REVIEW_MESSAGE, scan_upload_for_explicit_content

STATUS_TYPE_TO_CONTEXT = {
    "image": "status_image",
    "video": "status_video",
    "audio": "status_audio",
}


def resolve_and_validate_status_media(*, user, media_id, status_type: str) -> MediaUploadIntent:
    """Resolves a confirmed, not-yet-attached MediaUploadIntent for this user
    matching the status type, then runs the same explicit-content gate the
    legacy multipart path runs — raising ValidationError exactly like that
    path does when the media is quarantined/blocked/pending review.

    Deliberately does NOT call intent.mark_attached() — the caller
    (StatusCreateSerializer.create) only does that after the StatusItem row
    has actually been saved, so a failure anywhere else in status creation
    (e.g. audience/contact validation) never leaves an upload silently
    consumed with no status to show for it.
    """
    expected_context = STATUS_TYPE_TO_CONTEXT.get(status_type)
    if not expected_context:
        raise ValidationError({"type": "This status type does not accept a mediaId."})

    intent = upload_intent.resolve_confirmed_intent(
        user=user, media_id=media_id, expected_context=expected_context,
    )

    decision = scan_upload_for_explicit_content(
        filename=intent.original_filename or "status-upload",
        mime_type=intent.content_type or "",
        context="status",
    )
    MediaSafetyScan.objects.create(
        owner=user if user and user.is_authenticated else None,
        context="status",
        original_name=intent.original_filename or "",
        mime_type=intent.content_type or "",
        bytes=intent.size_bytes or 0,
        checksum="",
        provider=decision.provider,
        status=decision.status,
        quarantine=decision.quarantine,
        requires_review=decision.requires_review,
        policy_version=decision.policy_version,
        reason=decision.reason,
        result={**decision.as_metadata(), "surface": "messaging_status"},
        upload_id=str(intent.id),
    )
    if decision.quarantine or decision.requires_review or decision.status in {"blocked", "failed", "pending_review"}:
        raise ValidationError({"file": decision.user_message or USER_SAFE_REVIEW_MESSAGE})

    return intent
