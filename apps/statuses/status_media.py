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

Content-safety scanning for BOTH status upload paths (this direct-to-S3
one, and the legacy multipart path in StatusCreateSerializer) is
centralized in run_status_content_safety_scan() below — previously each
path called scan_upload_for_explicit_content() independently with no
file_path/storage_path at all, which meant real NudeNet detection could
never actually run for either (see MediaSafetyDecision's own routing:
provider=='nudenet' with no file_path always falls through to
pending_review, never a real pass/fail). Both paths now go through
scan_saved_upload_for_explicit_content() — the same shared entry point
apps/media/views.py's UploadFileView and the other 4 call sites fixed in
the "4-of-5-sites" pass use — instead of maintaining two separate,
silently-divergent scan implementations.
"""

from __future__ import annotations

from rest_framework.exceptions import ValidationError

from apps.media import upload_intent
from apps.media.models import MediaSafetyScan, MediaUploadIntent
from apps.media.safety import (
    MediaSafetyDecision,
    NUDENET_SCAN_QUEUED_REASON,
    USER_SAFE_REVIEW_MESSAGE,
    scan_saved_upload_for_explicit_content,
)

STATUS_TYPE_TO_CONTEXT = {
    "image": "status_image",
    "video": "status_video",
    "audio": "status_audio",
    "document": "status_document",
}

# Only image/video actually have visual content for NudeNet to inspect —
# audio and text never go through a scan at all (see StatusCreateSerializer,
# which sets moderation_status=PASSED for those types immediately). Kept
# here, not just implied, so a future third visual type doesn't silently
# skip scanning by omission.
SCANNABLE_STATUS_TYPES = {"image", "video"}


def resolve_confirmed_status_media(*, user, media_id, status_type: str) -> MediaUploadIntent:
    """Resolves a confirmed, not-yet-attached MediaUploadIntent for this
    user matching the status type. Content-safety scanning happens
    separately, in run_status_content_safety_scan() — this function only
    resolves and context-validates the intent, so StatusCreateSerializer
    can decide sync-vs-async handling (image vs video) the same way for
    both upload paths rather than this module deciding it unilaterally.

    Deliberately does NOT call intent.mark_attached() — the caller
    (StatusCreateSerializer.create) only does that after the StatusItem
    row has actually been saved, so a failure anywhere else in status
    creation never leaves an upload silently consumed with no status to
    show for it.
    """
    expected_context = STATUS_TYPE_TO_CONTEXT.get(status_type)
    if not expected_context:
        raise ValidationError({"type": "This status type does not accept a mediaId."})

    return upload_intent.resolve_confirmed_intent(
        user=user, media_id=media_id, expected_context=expected_context,
    )


def run_status_content_safety_scan(
    *,
    storage_path: str,
    filename: str,
    mime_type: str,
    status_type: str,
    owner,
    upload_id: str = "",
    size_bytes: int = 0,
) -> tuple[MediaSafetyDecision, MediaSafetyScan]:
    """The single scan-and-record call for BOTH status upload paths, run
    only for image/video (see SCANNABLE_STATUS_TYPES) and only after the
    file genuinely exists at storage_path — for the multipart path that
    means after FieldFile.save(..., save=False) has written real bytes,
    for the direct-to-S3 path it's the already-uploaded object_key. This
    is what makes scan_saved_upload_for_explicit_content's file_path-
    dependent real detection reachable, where the previous per-path calls
    (both metadata-only, no storage reference at all) never gave it one.

    Returns (decision, scan_row) — the caller (StatusCreateSerializer)
    owns the actual accept/queue/reject branching, since the multipart
    and S3 paths differ in what "reject" means (delete just-written
    bytes vs. leave an S3 object for the existing unattached-upload
    expiry sweep to reclaim).
    """
    decision = scan_saved_upload_for_explicit_content(
        storage_path=storage_path, filename=filename, mime_type=mime_type, context="status",
    )
    scan = MediaSafetyScan.objects.create(
        owner=owner if owner and getattr(owner, "is_authenticated", False) else None,
        context="status",
        original_name=filename,
        mime_type=mime_type,
        bytes=size_bytes,
        checksum="",
        provider=decision.provider,
        status=decision.status,
        quarantine=decision.quarantine,
        requires_review=decision.requires_review,
        policy_version=decision.policy_version,
        reason=decision.reason,
        result={
            **decision.as_metadata(),
            "surface": "messaging_status",
            "storage_path": storage_path,
            "mime_type": mime_type,
        },
        upload_id=upload_id,
    )
    return decision, scan


def status_scan_result_message(decision: MediaSafetyDecision) -> str:
    return decision.user_message or USER_SAFE_REVIEW_MESSAGE


__all__ = [
    "STATUS_TYPE_TO_CONTEXT",
    "SCANNABLE_STATUS_TYPES",
    "NUDENET_SCAN_QUEUED_REASON",
    "resolve_confirmed_status_media",
    "run_status_content_safety_scan",
    "status_scan_result_message",
]
