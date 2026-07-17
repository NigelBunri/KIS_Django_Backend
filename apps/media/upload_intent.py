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
one function to ATTACH_HANDLERS — nothing else in this module changes. Only
two contexts (profile_avatar, profile_cover) have a real attach handler
today; see the bottom of this file for why the others found during the
codebase survey (profile showcase, status attachments, commerce images,
background-removal) were deliberately left out of this pass rather than
guessed at.
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

from .models import MediaUploadIntent

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
}


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
    return {
        "upload_id": str(intent.id),
        "upload_url": upload_url,
        "object_key": intent.object_key,
        "expires_in": expires_in,
        "required_headers": {"Content-Type": intent.content_type},
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
    return {"upload_id": str(intent.id), "status": intent.status, "profile": serialized}


def _attach_profile_avatar(intent: MediaUploadIntent, *, already_confirmed: bool) -> dict:
    return _attach_profile_image(intent, "avatar_file", already_confirmed=already_confirmed)


def _attach_profile_cover(intent: MediaUploadIntent, *, already_confirmed: bool) -> dict:
    return _attach_profile_image(intent, "cover_file", already_confirmed=already_confirmed)


ATTACH_HANDLERS: dict[str, Callable[[MediaUploadIntent], dict]] = {
    "profile_avatar": _attach_profile_avatar,
    "profile_cover": _attach_profile_cover,
}


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
#   status_attachment  — StatusItem.file is set at creation time alongside
#                         type/visibility/etc., not attached after the fact
#                         to a pre-existing row; also carries video, which
#                         is explicitly out of scope for this task.
#   commerce_*_image   — Shop/Product/Service ownership is not a plain
#                         `request.user == obj.owner` check (partner/shop
#                         staff permissions) — needs that permission model
#                         confirmed before "ownership checks" can be
#                         implemented correctly here.
#   background_removal — BackgroundRemovalJob has no owner/user field on
#                         the model at all today; adding presigned-upload
#                         ownership enforcement for it requires an
#                         unrelated schema change to that app first.
#
# Their old multipart endpoints are untouched and fully functional. Adding
# support for any of them is: one UploadContextConfig entry above, one
# attach-handler function, one ATTACH_HANDLERS entry — no other change.
# --------------------------------------------------------------------------
