# apps/media/upload_intent.py
"""
Generic direct-to-S3 presigned-PUT upload handshake.

Three steps, all context-driven (not hardcoded to any one feature):
  1. initiate  — client declares filename/content_type/size_bytes; server
     validates against the context's rules, generates a private object key,
     creates a MediaUploadIntent, returns a short-lived presigned PUT URL.
  2. (client uploads bytes directly to S3 — this module is not involved)
  3. confirm   — client sends only the upload_id; server verifies the S3
     object with head_object, then calls the context's attach handler to
     bind the result onto whatever resource that context represents.

Adding a new upload context means adding one entry to UPLOAD_CONTEXTS and
one function to ATTACH_HANDLERS — nothing else in this module changes.
profile_avatar/profile_cover auto-attach at confirm time; the commerce_*
and status_* contexts are confirm-only (see _confirm_only_media_descriptor)
with a separate, per-feature attach step that does its own authorization.
See the bottom of this file for the remaining upload surfaces found during
the codebase survey (profile showcase, background-removal) that were
deliberately left out rather than guessed at.
"""

from __future__ import annotations

import logging
import mimetypes
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from .models import MediaAsset, MediaModerationState, MediaUploadIntent, MediaVisibility

logger = logging.getLogger("apps.media.upload_intent")


# --------------------------------------------------------------------------
# Per-context configuration
# --------------------------------------------------------------------------

def _profile_image_allowed_content_types() -> set[str]:
    configured = getattr(settings, "PROFILE_IMAGE_ALLOWED_CONTENT_TYPES", "")
    values = {v.strip().lower() for v in str(configured or "").split(",") if v.strip()}
    return values or {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/heic",
        "image/heif",
    }


def _profile_image_max_bytes() -> int:
    return int(getattr(settings, "PROFILE_IMAGE_MAX_UPLOAD_BYTES", 10 * 1024 * 1024))


# Extension chosen server-side from the validated content type — the
# client's filename is never used to build the object key (only sanitized
# and stored for display/logging).
_EXTENSION_BY_CONTENT_TYPE = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
    "image/heif": "heif",
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/zip": "zip",
    "text/plain": "txt",
    "text/csv": "csv",
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "video/webm": "webm",
    "audio/mp4": "m4a",
    "audio/mpeg": "mp3",
    "audio/aac": "aac",
    "audio/wav": "wav",
}


def _commerce_image_allowed_content_types() -> set[str]:
    configured = getattr(settings, "COMMERCE_IMAGE_ALLOWED_CONTENT_TYPES", "")
    values = {v.strip().lower() for v in str(configured or "").split(",") if v.strip()}
    return values or {"image/jpeg", "image/png", "image/webp"}


def _commerce_image_max_bytes() -> int:
    return int(getattr(settings, "COMMERCE_IMAGE_MAX_UPLOAD_BYTES", 8 * 1024 * 1024))


def _commerce_complaint_allowed_content_types() -> set[str]:
    configured = getattr(settings, "COMMERCE_COMPLAINT_ALLOWED_CONTENT_TYPES", "")
    values = {v.strip().lower() for v in str(configured or "").split(",") if v.strip()}
    return values or {"image/jpeg", "image/png", "image/webp", "application/pdf"}


def _commerce_complaint_max_bytes() -> int:
    return int(getattr(settings, "COMMERCE_COMPLAINT_MAX_UPLOAD_BYTES", 15 * 1024 * 1024))


def _status_image_allowed_content_types() -> set[str]:
    configured = getattr(settings, "STATUS_IMAGE_ALLOWED_CONTENT_TYPES", "")
    values = {v.strip().lower() for v in str(configured or "").split(",") if v.strip()}
    return values or {"image/jpeg", "image/png", "image/webp"}


def _status_image_max_bytes() -> int:
    return int(getattr(settings, "STATUS_IMAGE_MAX_UPLOAD_BYTES", 10 * 1024 * 1024))


def _status_video_allowed_content_types() -> set[str]:
    configured = getattr(settings, "STATUS_VIDEO_ALLOWED_CONTENT_TYPES", "")
    values = {v.strip().lower() for v in str(configured or "").split(",") if v.strip()}
    return values or {"video/mp4", "video/quicktime", "video/webm"}


def _status_video_max_bytes() -> int:
    return int(getattr(settings, "STATUS_VIDEO_MAX_UPLOAD_BYTES", 50 * 1024 * 1024))


def _status_audio_allowed_content_types() -> set[str]:
    configured = getattr(settings, "STATUS_AUDIO_ALLOWED_CONTENT_TYPES", "")
    values = {v.strip().lower() for v in str(configured or "").split(",") if v.strip()}
    return values or {"audio/mp4", "audio/mpeg", "audio/aac", "audio/wav"}


def _status_audio_max_bytes() -> int:
    return int(getattr(settings, "STATUS_AUDIO_MAX_UPLOAD_BYTES", 15 * 1024 * 1024))


def _education_image_allowed_content_types() -> set[str]:
    configured = getattr(settings, "EDUCATION_IMAGE_ALLOWED_CONTENT_TYPES", "")
    values = {v.strip().lower() for v in str(configured or "").split(",") if v.strip()}
    return values or {"image/jpeg", "image/png", "image/webp"}


def _education_image_max_bytes() -> int:
    return int(getattr(settings, "EDUCATION_IMAGE_MAX_UPLOAD_BYTES", 8 * 1024 * 1024))


def _education_material_allowed_content_types() -> set[str]:
    configured = getattr(settings, "EDUCATION_MATERIAL_ALLOWED_CONTENT_TYPES", "")
    values = {v.strip().lower() for v in str(configured or "").split(",") if v.strip()}
    return values or {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "image/jpeg",
        "image/png",
        "image/webp",
        "video/mp4",
        "video/quicktime",
        "video/webm",
        "audio/mp4",
        "audio/mpeg",
        "audio/aac",
        "audio/wav",
    }


def _education_material_max_bytes() -> int:
    # Uploads go straight to S3 via a single presigned PUT (never through
    # Django), so the real ceiling is S3's own single-PUT limit (5 GiB) —
    # not an app-imposed cap. Previously 100 MiB, which silently rejected
    # anything but the shortest course videos.
    return int(getattr(settings, "EDUCATION_MATERIAL_MAX_UPLOAD_BYTES", 5 * 1024 * 1024 * 1024))


def _testimony_media_allowed_content_types() -> set[str]:
    configured = getattr(settings, "TESTIMONY_MEDIA_ALLOWED_CONTENT_TYPES", "")
    values = {v.strip().lower() for v in str(configured or "").split(",") if v.strip()}
    return values or {
        "video/mp4",
        "video/quicktime",
        "video/webm",
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "image/jpeg",
        "image/png",
        "image/webp",
    }


def _testimony_media_max_bytes() -> int:
    return int(getattr(settings, "TESTIMONY_MEDIA_MAX_UPLOAD_BYTES", 2 * 1024 * 1024 * 1024))


def _task_report_allowed_content_types() -> set[str]:
    configured = getattr(settings, "TASK_REPORT_ALLOWED_CONTENT_TYPES", "")
    values = {v.strip().lower() for v in str(configured or "").split(",") if v.strip()}
    return values or {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/zip",
        "text/plain",
        "text/csv",
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/heic",
        "image/heif",
        "video/mp4",
        "video/quicktime",
        "video/webm",
        "audio/mp4",
        "audio/mpeg",
        "audio/aac",
        "audio/wav",
    }


def _task_report_max_bytes() -> int:
    return int(getattr(settings, "TASK_REPORT_MAX_UPLOAD_BYTES", 2 * 1024 * 1024 * 1024))


@dataclass(frozen=True)
class UploadContextConfig:
    allowed_content_types: Callable[[], set[str]]
    max_bytes: Callable[[], int]
    key_prefix: str  # e.g. "profile-images" -> private/profile-images/{user_id}/{uuid}.{ext}


UPLOAD_CONTEXTS: dict[str, UploadContextConfig] = {
    "profile_avatar": UploadContextConfig(
        allowed_content_types=_profile_image_allowed_content_types,
        max_bytes=_profile_image_max_bytes,
        key_prefix="profile-images",
    ),
    "profile_cover": UploadContextConfig(
        allowed_content_types=_profile_image_allowed_content_types,
        max_bytes=_profile_image_max_bytes,
        key_prefix="profile-images",
    ),
    # Commerce (marketplace) contexts. Unlike profile_avatar/profile_cover,
    # these do NOT auto-attach at confirm time — see
    # _confirm_only_media_descriptor below and apps/commerce/media_uploads.py
    # for the separate, per-target-authorized attach step. Target (shop /
    # product / service / order) ownership is validated by the commerce app
    # BEFORE create_upload_intent() is even called — see
    # apps/commerce/media_uploads.py:initiate_commerce_upload.
    "commerce_shop_image": UploadContextConfig(
        allowed_content_types=_commerce_image_allowed_content_types,
        max_bytes=_commerce_image_max_bytes,
        key_prefix="commerce/shop",
    ),
    "commerce_product_main_image": UploadContextConfig(
        allowed_content_types=_commerce_image_allowed_content_types,
        max_bytes=_commerce_image_max_bytes,
        key_prefix="commerce/pmain",
    ),
    "commerce_product_gallery_image": UploadContextConfig(
        allowed_content_types=_commerce_image_allowed_content_types,
        max_bytes=_commerce_image_max_bytes,
        key_prefix="commerce/pgal",
    ),
    "commerce_service_image": UploadContextConfig(
        allowed_content_types=_commerce_image_allowed_content_types,
        max_bytes=_commerce_image_max_bytes,
        key_prefix="commerce/svc",
    ),
    "commerce_service_gallery_image": UploadContextConfig(
        allowed_content_types=_commerce_image_allowed_content_types,
        max_bytes=_commerce_image_max_bytes,
        key_prefix="commerce/svcgal",
    ),
    "commerce_complaint_attachment": UploadContextConfig(
        allowed_content_types=_commerce_complaint_allowed_content_types,
        max_bytes=_commerce_complaint_max_bytes,
        key_prefix="commerce/cmpl",
    ),
    # Status/story media. Like the commerce contexts, these are
    # confirm-only (see _confirm_only_media_descriptor) — there is no
    # pre-existing target to authorize against at initiate time (any
    # authenticated user may post a status for themselves), so unlike
    # commerce this needs no dedicated initiate view at all: the generic
    # POST /api/v1/media/uploads/initiate/ with context=status_image|
    # status_video|status_audio is used directly. See
    # apps/statuses/status_media.py for the attach step (content
    # moderation + binding onto a new StatusItem).
    "status_image": UploadContextConfig(
        allowed_content_types=_status_image_allowed_content_types,
        max_bytes=_status_image_max_bytes,
        key_prefix="status/img",
    ),
    "status_video": UploadContextConfig(
        allowed_content_types=_status_video_allowed_content_types,
        max_bytes=_status_video_max_bytes,
        key_prefix="status/vid",
    ),
    "status_audio": UploadContextConfig(
        allowed_content_types=_status_audio_allowed_content_types,
        max_bytes=_status_audio_max_bytes,
        key_prefix="status/aud",
    ),
    # Education contexts. Like commerce, these are confirm-only (see
    # _confirm_only_media_descriptor) with a separate, per-institution-
    # authorized attach step — see apps/broadcasts/education_media.py.
    # Institution ownership/staff-membership is validated BEFORE
    # create_upload_intent() is even called (initiate_education_upload).
    "education_institution_logo": UploadContextConfig(
        allowed_content_types=_education_image_allowed_content_types,
        max_bytes=_education_image_max_bytes,
        key_prefix="education/logo",
    ),
    "education_module_cover_image": UploadContextConfig(
        allowed_content_types=_education_image_allowed_content_types,
        max_bytes=_education_image_max_bytes,
        key_prefix="education/cover",
    ),
    "education_material": UploadContextConfig(
        allowed_content_types=_education_material_allowed_content_types,
        max_bytes=_education_material_max_bytes,
        key_prefix="education/material",
    ),
    "testimony_media": UploadContextConfig(
        allowed_content_types=_testimony_media_allowed_content_types,
        max_bytes=_testimony_media_max_bytes,
        key_prefix="testimony/media",
    ),
    # Partner task reports (apps.tasks) — a member attaches evidence of
    # completed work when submitting an assigned task. Any of the broad
    # "office document" types the task feature promises (video/audio/pdf/
    # doc/image). Confirm-only, same shape as status_*/commerce_* above:
    # no pre-existing target to authorize at initiate time (any partner
    # member may submit their own assigned task), so the generic confirm
    # response's `assetId` is passed straight to apps.tasks's own submit
    # endpoint, which does its own ownership + task-state authorization.
    "task_report": UploadContextConfig(
        allowed_content_types=_task_report_allowed_content_types,
        max_bytes=_task_report_max_bytes,
        key_prefix="tasks/report",
    ),
    # Resource Library — same allowed types as task reports (documents,
    # spreadsheets, images, zips); reused rather than duplicated.
    "partner_resource": UploadContextConfig(
        allowed_content_types=_task_report_allowed_content_types,
        max_bytes=_task_report_max_bytes,
        key_prefix="partners/resources",
    ),
}


def _presign_expiry_seconds() -> int:
    return int(getattr(settings, "PROFILE_IMAGE_PRESIGN_EXPIRY_SECONDS", 600))


def _intent_expiry_seconds() -> int:
    # Cleanup grace window: a few minutes longer than the presign itself, so
    # a client mid-upload right at the presign deadline isn't racing the
    # cleanup sweep for the same record.
    default = _presign_expiry_seconds() + 300
    return int(getattr(settings, "MEDIA_UPLOAD_INTENT_EXPIRY_SECONDS", default))


def _sanitize_filename(filename: str) -> str:
    cleaned = Path(str(filename or "upload")).name.strip()
    return cleaned[:255] or "upload"


def _safe_log_key(object_key: str) -> str:
    """Shortened form for logs — never the presigned URL, just enough to correlate."""
    return object_key if len(object_key) <= 80 else f"{object_key[:40]}...{object_key[-20:]}"


# --------------------------------------------------------------------------
# Initiate
# --------------------------------------------------------------------------

def validate_initiate_payload(
    *, context: str, filename: str, content_type: str, size_bytes) -> UploadContextConfig:
    config = UPLOAD_CONTEXTS.get(context)
    if not config:
        raise ValidationError({"context": "Unknown or unsupported upload context."})

    if not filename or not str(filename).strip():
        raise ValidationError({"filename": "This field is required."})

    content_type = str(content_type or "").strip().lower()
    if content_type not in config.allowed_content_types():
        raise ValidationError({"content_type": "This file type is not allowed."})

    try:
        size_bytes_int = int(size_bytes)
    except (TypeError, ValueError):
        raise ValidationError({"size_bytes": "Must be a positive integer."})
    if size_bytes_int <= 0:
        raise ValidationError({"size_bytes": "Must be a positive integer."})
    max_bytes = config.max_bytes()
    if size_bytes_int > max_bytes:
        raise ValidationError({"size_bytes": f"File exceeds the maximum allowed size of {max_bytes} bytes."})

    # Loose extension/MIME sanity check — filename is never used to build the
    # object key, this is purely a "does this look internally consistent"
    # signal before we bother issuing a presigned URL.
    ext = Path(_sanitize_filename(filename)).suffix.lower().lstrip(".")
    expected_ext = _EXTENSION_BY_CONTENT_TYPE.get(content_type)
    if expected_ext and ext and ext not in {expected_ext, "jpeg" if expected_ext == "jpg" else expected_ext}:
        # jpg/jpeg are the one common alias worth tolerating explicitly.
        if not (expected_ext == "jpg" and ext == "jpeg"):
            raise ValidationError({"filename": "File extension does not match the declared content type."})

    return config


def _generate_object_key(*, context: str, user_id, content_type: str) -> str:
    config = UPLOAD_CONTEXTS[context]
    extension = _EXTENSION_BY_CONTENT_TYPE.get(content_type, "bin")
    return f"private/{config.key_prefix}/{user_id}/{uuid.uuid4().hex}.{extension}"


def create_upload_intent(
    *, user, context: str, filename: str, content_type: str, size_bytes: int, target_id: str = ""
) -> MediaUploadIntent:
    content_type = str(content_type).strip().lower()
    object_key = _generate_object_key(context=context, user_id=user.id, content_type=content_type)
    now = timezone.now()
    intent = MediaUploadIntent.objects.create(
        owner=user,
        context=context,
        target_id=target_id or "",
        original_filename=_sanitize_filename(filename),
        object_key=object_key,
        content_type=content_type,
        size_bytes=int(size_bytes),
        status=MediaUploadIntent.STATUS_PENDING,
        expires_at=now + timezone.timedelta(seconds=_intent_expiry_seconds()),
    )
    logger.info(
        "media_upload_intent.initiated",
        extra={
            "upload_id": str(intent.id),
            "user_id": str(user.id),
            "context": context,
            "object_key": _safe_log_key(object_key),
        },
    )
    return intent


def build_initiate_response(intent: MediaUploadIntent) -> dict:
    expires_in = _presign_expiry_seconds()
    upload_url = default_storage.generate_presigned_put(
        intent.object_key, intent.content_type, expires_in
    )
    headers = {"Content-Type": intent.content_type}
    upload_id = str(intent.id)
    return {
        # Canonical, explicit field names — every upload surface in the
        # platform should read these, never a storage key.
        "uploadId": upload_id,
        "storageKey": intent.object_key,
        "uploadUrl": upload_url,
        "headers": headers,
        "expiresInSeconds": expires_in,
        # Legacy snake_case aliases, kept for callers written against the
        # original shape. Always point at the same values as above.
        "upload_id": upload_id,
        "upload_url": upload_url,
        "object_key": intent.object_key,
        "expires_in": expires_in,
        "required_headers": headers,
    }


# --------------------------------------------------------------------------
# Confirm
# --------------------------------------------------------------------------

class UploadConfirmationError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _get_owned_intent_for_update(*, user, upload_id) -> MediaUploadIntent:
    try:
        intent = MediaUploadIntent.objects.select_for_update().get(id=upload_id)
    except (MediaUploadIntent.DoesNotExist, ValueError, TypeError):
        raise NotFound("Upload not found.")
    if intent.owner_id != user.id:
        # Same response as "not found" — do not reveal that a different
        # user's upload_id exists.
        raise NotFound("Upload not found.")
    return intent


def _media_asset_type_for_content_type(content_type: str) -> str:
    normalized = (content_type or "").lower()
    if normalized.startswith("image/"):
        return "image"
    if normalized.startswith("video/"):
        return "video"
    if normalized.startswith("audio/"):
        return "audio"
    return "document"


def _ensure_canonical_asset(intent: MediaUploadIntent) -> MediaAsset:
    """Idempotent: creates (once) the canonical MediaAsset for a CONFIRMED
    intent, or returns the existing linked one. Always called from inside
    confirm_upload_intent's own transaction.atomic() block, so the intent
    and its asset are created/linked atomically — a confirm that fails
    after this point rolls back the asset too, and a confirm that succeeds
    can never leave a CONFIRMED intent (created after this field existed)
    without a linked asset.

    Phase 1 of the platform's canonical-asset migration: this does NOT
    change confirm()'s return value, does NOT change any attach handler's
    behavior (they still bind via intent.object_key exactly as before —
    MediaAsset.attached_at is deliberately left unset here, wiring it up is
    Phase 2 work once a generic attach endpoint exists), and does NOT
    backfill pre-Phase-1 rows — see apps/media/models.py's MediaAsset
    docstring."""
    if intent.canonical_asset_id:
        return intent.canonical_asset

    from .purposes import get_purpose

    try:
        context_value = get_purpose(intent.context).context
    except KeyError:
        # A context reachable via confirm_upload_intent without a matching
        # registry entry shouldn't happen (tests_purposes.py asserts the
        # registry and UPLOAD_CONTEXTS stay 1:1) — fail soft here rather
        # than blocking confirm on a registry gap.
        context_value = ""

    asset = MediaAsset.objects.create(
        owner=intent.owner,
        type=_media_asset_type_for_content_type(intent.content_type),
        bucket_key=intent.object_key,
        mime_type=intent.content_type,
        bytes=intent.size_bytes,
        status="ready",  # only ever created once the S3 object is confirmed real
        purpose=intent.context,
        context=context_value,
        target_id=intent.target_id,
        original_filename=intent.original_filename,
        moderation_state=MediaModerationState.NOT_SCANNED,
        visibility=MediaVisibility.PRIVATE,
        confirmed_at=intent.confirmed_at,
    )
    intent.canonical_asset = asset
    intent.save(update_fields=["canonical_asset", "updated_at"])
    return asset


def confirm_upload_intent(*, user, upload_id) -> dict:
    # `error` is raised AFTER the atomic block exits rather than from inside
    # it: transaction.atomic() rolls back every write made in the block if
    # any exception propagates out of it, including DRF's ValidationError.
    # Raising mark_failed()/expired-status writes from inside the same
    # block that made them would silently discard those writes, leaving the
    # intent stuck at its pre-failure status.
    error: ValidationError | None = None
    result: dict | None = None

    with transaction.atomic():
        intent = _get_owned_intent_for_update(user=user, upload_id=upload_id)

        if intent.status == MediaUploadIntent.STATUS_CONFIRMED:
            # Idempotent: repeated confirmation of an already-confirmed
            # upload returns the same success result rather than erroring,
            # so a client retry after a dropped response is safe.
            #
            # _ensure_canonical_asset is called here too (not just in the
            # first-confirm branch below) purely defensively — it's a no-op
            # for any intent that already has one, and closes the
            # theoretical gap of a row confirmed by code that predates this
            # field but replayed after a deploy that adds it.
            _ensure_canonical_asset(intent)
            handler = ATTACH_HANDLERS.get(intent.context)
            result = (
                handler(intent, already_confirmed=True)
                if handler
                else {"upload_id": str(intent.id), "status": intent.status}
            )

        elif intent.status in (
            MediaUploadIntent.STATUS_FAILED,
            MediaUploadIntent.STATUS_EXPIRED,
            MediaUploadIntent.STATUS_ABORTED,
        ):
            error = ValidationError({"detail": f"This upload cannot be confirmed (status={intent.status})."})

        elif intent.is_expired():
            intent.status = MediaUploadIntent.STATUS_EXPIRED
            intent.save(update_fields=["status", "updated_at"])
            logger.info(
                "media_upload_intent.expired",
                extra={"upload_id": str(intent.id), "user_id": str(user.id), "context": intent.context},
            )
            error = ValidationError({"detail": "This upload has expired. Please start a new upload."})

        else:
            meta = default_storage.head_object_meta(intent.object_key)
            if not meta:
                intent.mark_failed("object_missing", "The uploaded object was not found in storage.")
                logger.warning(
                    "media_upload_intent.confirm_rejected",
                    extra={
                        "upload_id": str(intent.id),
                        "user_id": str(user.id),
                        "context": intent.context,
                        "reason": "object_missing",
                    },
                )
                error = ValidationError({"detail": "Upload not found in storage. Please retry the upload."})
            else:
                config = UPLOAD_CONTEXTS.get(intent.context)
                max_bytes = config.max_bytes() if config else intent.size_bytes
                actual_size = meta["ContentLength"]
                actual_content_type = meta["ContentType"].lower()

                if actual_size <= 0:
                    intent.mark_failed("empty_object", "Uploaded object is empty.")
                    error = ValidationError({"detail": "Uploaded file is empty."})
                elif actual_size > max_bytes:
                    intent.mark_failed("object_too_large", "Uploaded object exceeds the permitted size.")
                    error = ValidationError({"detail": "Uploaded file is larger than the permitted size."})
                elif intent.size_bytes > 0 and not (0.5 <= actual_size / intent.size_bytes <= 2.0):
                    # Allow some slack between declared and actual size
                    # (compression libraries / multipart chunking can shift
                    # this by a few bytes) but reject anything wildly
                    # different from what was declared at initiate time.
                    intent.mark_failed("size_mismatch", "Uploaded object size does not match the declared size.")
                    logger.warning(
                        "media_upload_intent.confirm_rejected",
                        extra={
                            "upload_id": str(intent.id),
                            "user_id": str(user.id),
                            "context": intent.context,
                            "reason": "size_mismatch",
                        },
                    )
                    error = ValidationError({"detail": "Uploaded file size does not match what was declared."})
                elif config and actual_content_type not in config.allowed_content_types():
                    intent.mark_failed("content_type_mismatch", "Stored content type is not allowed.")
                    logger.warning(
                        "media_upload_intent.confirm_rejected",
                        extra={
                            "upload_id": str(intent.id),
                            "user_id": str(user.id),
                            "context": intent.context,
                            "reason": "content_type_mismatch",
                        },
                    )
                    error = ValidationError({"detail": "Uploaded file's content type is not allowed."})
                else:
                    intent.mark_confirmed()
                    logger.info(
                        "media_upload_intent.confirmed",
                        extra={
                            "upload_id": str(intent.id),
                            "user_id": str(user.id),
                            "context": intent.context,
                            "object_key": _safe_log_key(intent.object_key),
                        },
                    )
                    _ensure_canonical_asset(intent)
                    handler = ATTACH_HANDLERS.get(intent.context)
                    if not handler:
                        # Should be unreachable — initiate already rejects
                        # contexts without a handler — but fail loudly
                        # rather than silently if it ever happens (e.g. a
                        # handler removed after intents already existed).
                        error = ValidationError(
                            {"detail": "This upload context has no attachment handler configured."}
                        )
                    else:
                        result = handler(intent, already_confirmed=False)

    if error is not None:
        raise error
    return result


# --------------------------------------------------------------------------
# Attach handlers — one per context. Each receives the confirmed intent and
# is responsible for: (a) binding intent.object_key onto the real resource
# without re-uploading through Django, (b) deleting the previous object only
# AFTER the new one is saved successfully, (c) returning the same
# serialized shape the surface's existing API already returns.
# --------------------------------------------------------------------------

def _attach_profile_image(intent: MediaUploadIntent, field_name: str, *, already_confirmed: bool) -> dict:
    from apps.accounts.models import Profile
    from apps.accounts.serializers import ProfileSerializer

    from .services import lifecycle

    profile, _ = Profile.objects.select_for_update().get_or_create(user=intent.owner)
    field = getattr(profile, field_name)
    previous_name = field.name if field else None

    if not already_confirmed or previous_name != intent.object_key:
        # Assigning .name directly (not .save(name, content)) binds the
        # already-uploaded S3 object without Django re-reading/re-uploading
        # the bytes — S3MediaStorage._object_key() is a pass-through, so
        # this string becomes the literal S3 key.
        setattr(field, "name", intent.object_key)
        profile.save(update_fields=[field_name, "updated_at"])
        # Phase 2: keeps MediaAsset.attached_at/target_type/target_id in
        # sync with this (pre-existing, Phase 0/1) auto-attach-at-confirm
        # behavior — mark_attached() alone (the old call here) left the
        # canonical asset's own attached_at/target fields unset.
        lifecycle.sync_attachment(intent=intent, target_type="accounts.Profile", target_id=str(profile.id))
        try:
            profile.update_completion()
        except Exception:
            pass
        if previous_name and previous_name != intent.object_key:
            try:
                default_storage.delete(previous_name)
            except Exception:
                logger.warning(
                    "media_upload_intent.old_image_cleanup_failed",
                    extra={
                        "upload_id": str(intent.id),
                        "user_id": str(intent.owner_id),
                        "context": intent.context,
                    },
                )

    serialized = ProfileSerializer(profile, context={"request": None}).data
    return {
        "upload_id": str(intent.id),
        "status": intent.status,
        "profile": serialized,
        # Phase 2 addition: the canonical asset id, for any caller that
        # wants to use the generic media endpoints (signed-url, delete)
        # afterward — additive, does not change any existing field.
        "assetId": str(intent.canonical_asset_id) if intent.canonical_asset_id else None,
    }


def _attach_profile_avatar(intent: MediaUploadIntent, *, already_confirmed: bool) -> dict:
    return _attach_profile_image(intent, "avatar_file", already_confirmed=already_confirmed)


def _attach_profile_cover(intent: MediaUploadIntent, *, already_confirmed: bool) -> dict:
    return _attach_profile_image(intent, "cover_file", already_confirmed=already_confirmed)


def _confirm_only_media_descriptor(intent: MediaUploadIntent, *, already_confirmed: bool) -> dict:
    """Attach handler for contexts where confirmation and attachment are
    deliberately separate steps: confirm() proves the S3 object is real and
    within the context's limits and returns a stable `mediaId` (the
    intent's own id); a later, explicit attach call — which does its own
    per-target ownership/authorization check the generic confirm step can't
    know about (which shop? which product? whose order?) — binds it to a
    real resource. See apps/commerce/media_uploads.py. Has no side effects
    beyond what confirm() itself already performed.
    """
    return {
        "mediaId": str(intent.id),
        "upload_id": str(intent.id),
        "status": intent.status,
        "originalName": intent.original_filename,
        "mimeType": intent.content_type,
        "size": intent.size_bytes,
        "context": intent.context,
        # Phase 2 addition: the canonical asset id — what the generic
        # attach/signed-url/delete endpoints key on. `mediaId` above stays
        # the intent id (unchanged, legacy contexts already resolve
        # confirmed media by it via resolve_confirmed_intent), `assetId` is
        # new and additive.
        "assetId": str(intent.canonical_asset_id) if intent.canonical_asset_id else None,
    }


ATTACH_HANDLERS: dict[str, Callable[[MediaUploadIntent], dict]] = {
    "profile_avatar": _attach_profile_avatar,
    "profile_cover": _attach_profile_cover,
    "commerce_shop_image": _confirm_only_media_descriptor,
    "commerce_product_main_image": _confirm_only_media_descriptor,
    "commerce_product_gallery_image": _confirm_only_media_descriptor,
    "commerce_service_image": _confirm_only_media_descriptor,
    "commerce_service_gallery_image": _confirm_only_media_descriptor,
    "commerce_complaint_attachment": _confirm_only_media_descriptor,
    "status_image": _confirm_only_media_descriptor,
    "status_video": _confirm_only_media_descriptor,
    "status_audio": _confirm_only_media_descriptor,
    "education_institution_logo": _confirm_only_media_descriptor,
    "education_module_cover_image": _confirm_only_media_descriptor,
    "education_material": _confirm_only_media_descriptor,
    "testimony_media": _confirm_only_media_descriptor,
}


def resolve_confirmed_intent(*, user, media_id, expected_context: str) -> MediaUploadIntent:
    """Looks up a confirmed, not-yet-attached MediaUploadIntent owned by
    `user` for the expected context. Never trusts a storage key or object id
    supplied by the client for anything other than this opaque `media_id` —
    the actual S3 key is never read from the request.

    Generic counterpart to apps.commerce.media_uploads.resolve_confirmed_media
    (left as-is there rather than refactored onto this one, to avoid any risk
    to already-shipped marketplace behaviour). New confirm-only contexts
    (e.g. apps/statuses/status_media.py) should call this one directly."""
    if not media_id:
        raise ValidationError({"mediaId": "mediaId is required."})
    intent = MediaUploadIntent.objects.filter(id=media_id, owner_id=user.id).first()
    if not intent:
        raise NotFound("Confirmed media not found.")
    if intent.context != expected_context:
        raise ValidationError({"mediaId": "This media was not uploaded for this purpose."})
    if intent.status != MediaUploadIntent.STATUS_CONFIRMED:
        raise ValidationError({"mediaId": f"This media is not confirmed (status={intent.status})."})
    if intent.attached_at is not None:
        raise ValidationError({"mediaId": "This media has already been attached and cannot be reused."})
    return intent


# --------------------------------------------------------------------------
# Cleanup — abandoned uploads (presigned URL issued, never confirmed).
# Shared by the Celery task (apps/media/tasks.py) and the management command
# (apps/media/management/commands/expire_media_uploads.py) so there's one
# implementation, not two.
# --------------------------------------------------------------------------

def expire_abandoned_upload_intents(*, limit: int = 500) -> dict:
    """Finds pending/uploaded intents past expiry, best-effort deletes any S3
    object that may exist at their key, marks them expired. Never touches a
    confirmed intent — confirmed intents own a real, attached resource."""
    now = timezone.now()
    candidates = MediaUploadIntent.objects.filter(
        status__in=[MediaUploadIntent.STATUS_PENDING, MediaUploadIntent.STATUS_UPLOADED],
        expires_at__lte=now,
    ).order_by("expires_at")[:limit]

    expired_count = 0
    cleanup_failures = 0
    for intent in candidates:
        try:
            # S3's delete_object is idempotent — it does not error just
            # because the key was never actually uploaded (the common case
            # for a truly abandoned intent). An exception here means a real
            # problem talking to S3 (permissions, network), not "nothing to
            # delete" — worth logging, but the intent still gets marked
            # expired below either way so it stops showing as pending.
            default_storage.delete(intent.object_key)
        except Exception:
            cleanup_failures += 1
            logger.warning(
                "media_upload_intent.cleanup_failed",
                extra={"upload_id": str(intent.id), "user_id": str(intent.owner_id), "context": intent.context},
            )
        intent.status = MediaUploadIntent.STATUS_EXPIRED
        intent.save(update_fields=["status", "updated_at"])
        expired_count += 1

    logger.info(
        "media_upload_intent.cleanup_run",
        extra={"expired_count": expired_count, "cleanup_failures": cleanup_failures},
    )
    return {"expired_count": expired_count, "cleanup_failures": cleanup_failures}


def _unattached_grace_period_seconds() -> int:
    # Deliberately NOT the chat-attachment 10-day TTL — this only covers the
    # narrow window between "confirmed" and "attached" in a multi-step
    # client flow (e.g. finish creating a product with a photo already
    # uploaded), which should be minutes, not days. Generous default to
    # tolerate a slow or interrupted flow without being a long-term grant.
    return int(getattr(settings, "MEDIA_UPLOAD_UNATTACHED_GRACE_SECONDS", 24 * 60 * 60))


def expire_unattached_confirmed_intents(*, grace_period_seconds: int | None = None, limit: int = 500) -> dict:
    """Finds confirmed uploads that were never attached to anything within
    the grace period (client abandoned the flow after confirming but before
    finishing the create/attach step), best-effort deletes the S3 object,
    marks the intent expired. Deliberately scoped to `attached_at IS NULL` —
    never touches an intent that was successfully attached (that upload is
    now permanent, referenced media: a live product image, dispute
    evidence, etc.) regardless of how old it is."""
    grace_period = (
        grace_period_seconds if grace_period_seconds is not None else _unattached_grace_period_seconds()
    )
    cutoff = timezone.now() - timezone.timedelta(seconds=grace_period)
    candidates = MediaUploadIntent.objects.filter(
        status=MediaUploadIntent.STATUS_CONFIRMED,
        attached_at__isnull=True,
        confirmed_at__lte=cutoff,
    ).order_by("confirmed_at")[:limit]

    expired_count = 0
    cleanup_failures = 0
    for intent in candidates:
        try:
            default_storage.delete(intent.object_key)
        except Exception:
            cleanup_failures += 1
            logger.warning(
                "media_upload_intent.orphan_cleanup_failed",
                extra={"upload_id": str(intent.id), "user_id": str(intent.owner_id), "context": intent.context},
            )
        intent.status = MediaUploadIntent.STATUS_EXPIRED
        intent.save(update_fields=["status", "updated_at"])
        expired_count += 1

    logger.info(
        "media_upload_intent.orphan_cleanup_run",
        extra={"expired_count": expired_count, "cleanup_failures": cleanup_failures},
    )
    return {"expired_count": expired_count, "cleanup_failures": cleanup_failures}


# --------------------------------------------------------------------------
# Deliberately not implemented in this pass (survey found these during the
# full upload-surface inventory; each needs its own investigation before an
# attach handler can be written without guessing at semantics):
#
#   profile_showcase   — ProfileShowcase.file needs a target showcase row
#                         (type/title chosen by the user); unclear whether
#                         confirm should create a new row or attach to an
#                         existing draft — needs product/UX input, not a
#                         guess in a storage-migration change.
#   background_removal — BackgroundRemovalJob has no owner/user field on
#                         the model at all today; adding presigned-upload
#                         ownership enforcement for it requires an
#                         unrelated schema change to that app first.
#
# commerce_*_image / commerce_complaint_attachment — implemented; ownership
# is validated per-target in apps/commerce/media_uploads.py (shop/product/
# service: owner-or-staff; complaint: order buyer or shop admin/manager —
# see _provider_can_manage_shop in apps/commerce/services.py), matching
# exactly what the existing multipart endpoints already enforced.
#
# status_image / status_video / status_audio — implemented; unlike
# commerce, there is no pre-existing target to authorize at initiate time
# (any authenticated user posts statuses for themselves), so the generic
# confirm-only handler here is sufficient and no per-context initiate view
# was needed. See apps/statuses/status_media.py + StatusCreateSerializer's
# `media_id` field for the attach step (content moderation, binding the
# object key onto a new StatusItem only once it's actually saved).
#
# Their old multipart endpoints are untouched and fully functional. Adding
# support for any of them is: one UploadContextConfig entry above, one
# attach-handler function, one ATTACH_HANDLERS entry — no other change.
# --------------------------------------------------------------------------
