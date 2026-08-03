# apps/media/services/lifecycle.py
"""
Lifecycle predicates and state-transition operations shared by every
generic media endpoint. Phase 2 of the KIS Universal Media Platform.

Deliberately does NOT introduce the full nine-state lifecycle from the
platform design doc yet — MediaUploadIntent.status and MediaAsset's own
status/moderation_state/deleted_at fields keep their Phase 0/1 meanings.
This module centralizes how those existing fields combine into an
attachable/downloadable/deletable decision so nothing outside it has to
re-derive that combination, per the platform design's "helper predicates"
guidance (asset.is_attachable-style checks, without changing what any
existing field means).
"""

from __future__ import annotations

import logging

from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from ..models import MediaAsset, MediaModerationState, MediaUploadIntent

logger = logging.getLogger("apps.media.lifecycle")


def _is_expired(asset: MediaAsset) -> bool:
    # MediaAsset.expires_at is a Phase 1 field nothing currently sets for
    # presigned-flow-created assets (no purpose has a retention policy at
    # the asset layer today — see purposes.py's retention_days=None on
    # every registered purpose) — this only fires for a caller that set it
    # explicitly (e.g. a future purpose, or a test).
    return bool(asset.expires_at) and asset.expires_at <= timezone.now()


def is_attachable(asset: MediaAsset) -> bool:
    """True if `asset` may be attached (freshly or idempotently) to a
    target. False for deleted, expired, quarantined, or legacy-multipart
    'blocked' assets."""
    if asset.deleted_at is not None:
        return False
    if _is_expired(asset):
        return False
    if asset.moderation_state == MediaModerationState.QUARANTINED:
        return False
    if asset.status == "blocked":
        return False
    return True


def is_downloadable(asset: MediaAsset) -> bool:
    """True if a signed URL may be issued for `asset`, independent of WHO
    is asking (see apps.media.services.access for the viewer-specific
    check). Deliberately permissive on moderation: NOT_SCANNED and
    PENDING_REVIEW are still downloadable, matching current behavior across
    every migrated flow today — nothing currently blocks *download* on scan
    state; only status's create path blocks a quarantined upload from ever
    becoming visible in the first place (apps/statuses/status_media.py).
    Only QUARANTINED, deleted, and expired assets are blocked here. See the
    Phase 2 report's "remaining permissive moderation gaps" section."""
    if asset.deleted_at is not None:
        return False
    if _is_expired(asset):
        return False
    if asset.moderation_state == MediaModerationState.QUARANTINED:
        return False
    return True


def is_deletable(asset: MediaAsset, *, purpose) -> bool:
    if asset.deleted_at is not None:
        return True  # already deleted — deletion itself must stay idempotent
    return bool(purpose.allow_delete)


def sync_attachment(*, intent: MediaUploadIntent, target_type: str, target_id: str) -> None:
    """Call exactly once a feature's attach logic (legacy or generic)
    successfully binds media to a target — keeps
    MediaUploadIntent.attached_at, MediaAsset.attached_at,
    MediaAsset.target_type, and MediaAsset.target_id in agreement, per the
    platform design doc's "Lifecycle synchronization" requirement.
    Idempotent: mark_attached() itself already no-ops if attached_at is
    already set; the asset write below is a no-op if the target already
    matches, so calling this twice with the same target changes nothing."""
    intent.mark_attached()
    asset = intent.canonical_asset
    if asset is None:
        return
    if asset.target_type == target_type and asset.target_id == target_id and asset.attached_at is not None:
        return
    asset.target_type = target_type
    asset.target_id = target_id
    asset.attached_at = intent.attached_at or timezone.now()
    asset.save(update_fields=["target_type", "target_id", "attached_at", "updated_at"])


def cancel_upload(*, user, upload_id) -> dict:
    """Cancels a not-yet-confirmed upload. Operates on MediaUploadIntent
    (via uploadId), not MediaAsset — a pending upload has no MediaAsset yet
    (_ensure_canonical_asset only ever runs at confirm time), so there is
    nothing for an assetId-keyed endpoint to act on. See the Phase 2 report
    for why POST /api/v1/media/uploads/:uploadId/cancel/ was chosen over a
    MediaAsset-keyed route.

    Idempotent: cancelling an already-aborted intent returns success
    without re-running cleanup. Only PENDING/UPLOADED/FAILED/EXPIRED
    intents may be cancelled — a CONFIRMED intent (which may already have a
    real, attached resource depending on it) must go through delete
    instead, never cancel."""
    from .. import upload_intent as upload_intent_module

    with transaction.atomic():
        intent = upload_intent_module._get_owned_intent_for_update(user=user, upload_id=upload_id)

        if intent.status == MediaUploadIntent.STATUS_ABORTED:
            logger.info(
                "media_upload_cancelled",
                extra={
                    "upload_id": str(intent.id), "owner_id": str(user.id),
                    "purpose": intent.context, "reason_code": "already_cancelled",
                },
            )
            return {"uploadId": str(intent.id), "status": intent.status, "cancelled": True}

        if intent.status == MediaUploadIntent.STATUS_CONFIRMED:
            raise ValidationError(
                {"detail": "This upload is already confirmed. Use delete, not cancel, to remove it."}
            )

        object_key = intent.object_key
        intent.mark_aborted()

    # Best-effort storage cleanup after the state transition is committed —
    # matches expire_abandoned_upload_intents' existing "delete is
    # idempotent/best-effort, never blocks the status transition" pattern.
    try:
        default_storage.delete(object_key)
    except Exception:
        logger.warning(
            "media_upload_cancel_cleanup_failed",
            extra={"upload_id": str(intent.id), "owner_id": str(user.id), "purpose": intent.context},
        )

    logger.info(
        "media_upload_cancelled",
        extra={
            "upload_id": str(intent.id), "owner_id": str(user.id),
            "purpose": intent.context, "reason_code": "user_requested",
        },
    )
    return {"uploadId": str(intent.id), "status": intent.status, "cancelled": True}


def delete_asset(*, user, asset: MediaAsset, purpose) -> dict:
    """Soft-deletes a canonical MediaAsset: never hard-deletes the row
    (audit metadata — MediaSafetyScan rows, the intent, the asset itself —
    all remain queryable), marks `deleted_at`, dispatches the purpose's
    detach_handler (if registered) inside the same transaction so a
    feature-side failure rolls back the deletion too, then attempts
    best-effort storage cleanup after commit. Idempotent."""
    if asset.owner_id != user.id and not getattr(user, "is_staff", False):
        raise NotFound("Media not found.")

    if asset.deleted_at is not None:
        logger.info(
            "media_deleted",
            extra={
                "asset_id": str(asset.id), "purpose": asset.purpose, "owner_id": str(user.id),
                "reason_code": "already_deleted",
            },
        )
        return {"assetId": str(asset.id), "deleted": True}

    logger.info(
        "media_delete_requested",
        extra={"asset_id": str(asset.id), "purpose": asset.purpose, "owner_id": str(user.id)},
    )

    if not purpose.allow_delete:
        logger.info(
            "media_delete_requested",
            extra={
                "asset_id": str(asset.id), "purpose": asset.purpose, "owner_id": str(user.id),
                "reason_code": "protected_evidence",
            },
        )
        raise ValidationError(
            {"detail": "This media is protected (retention policy) and cannot be deleted."}
        )

    with transaction.atomic():
        asset = MediaAsset.objects.select_for_update().get(id=asset.id)
        if asset.deleted_at is not None:
            return {"assetId": str(asset.id), "deleted": True}

        if purpose.detach_handler is not None:
            # May raise — rolls back the whole transaction, asset stays
            # attached/undeleted, matching "failed feature detach rolls
            # back asset state."
            purpose.detach_handler(user=user, asset=asset)

        asset.deleted_at = timezone.now()
        # Keeps the pre-Phase-1 BaseEntity.is_deleted boolean (which
        # MediaAssetViewSet.get_queryset() already filters list/retrieve
        # on) in agreement with the new deleted_at timestamp — otherwise a
        # soft-deleted asset would still appear in that existing endpoint.
        asset.is_deleted = True
        asset.save(update_fields=["deleted_at", "is_deleted", "updated_at"])
        storage_key = asset.bucket_key

    try:
        default_storage.delete(storage_key)
    except Exception:
        logger.warning(
            "media_delete_cleanup_failed",
            extra={"asset_id": str(asset.id), "purpose": asset.purpose, "owner_id": str(user.id)},
        )

    logger.info(
        "media_deleted",
        extra={"asset_id": str(asset.id), "purpose": asset.purpose, "owner_id": str(user.id)},
    )
    return {"assetId": str(asset.id), "deleted": True}
