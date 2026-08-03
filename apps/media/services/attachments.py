# apps/media/services/attachments.py
"""
The generic attach endpoint's full dispatch flow. Phase 2 of the KIS
Universal Media Platform.

This is the ONE place a MediaAsset gets bound to an owning feature record
through the new platform surface — existing per-feature attach functions
(apps.commerce.media_uploads.attach_shop_image et al.) are NOT
reimplemented here; they're called through thin per-purpose attach_handler
adapters the owning app registers (see apps/commerce/media_hooks.py). This
module owns the parts that are the same for every purpose: resolving the
asset, checking ownership/confirmation/lifecycle state, purpose/target-type
validation, the idempotent-retry and reuse-rejection rules, and keeping
MediaUploadIntent/MediaAsset in sync afterward.
"""

from __future__ import annotations

import logging

from django.db import transaction
from rest_framework.exceptions import NotFound, ValidationError

from .. import purposes
from ..models import MediaAsset, MediaUploadIntent
from . import lifecycle

logger = logging.getLogger("apps.media.attachments")


def _get_owned_confirmed_asset(*, user, asset_id) -> tuple[MediaAsset, MediaUploadIntent]:
    try:
        asset = MediaAsset.objects.select_related("source_intent").get(id=asset_id)
    except (MediaAsset.DoesNotExist, ValueError, TypeError):
        raise NotFound("Media not found.")

    if asset.owner_id != user.id:
        # Same response as "not found" — mirrors
        # upload_intent._get_owned_intent_for_update's existing choice not
        # to reveal that a different user's asset id exists.
        raise NotFound("Media not found.")

    intent = getattr(asset, "source_intent", None)
    if intent is None:
        raise ValidationError(
            {"detail": "This media was not created through the presigned upload flow and cannot be attached generically."}
        )
    if intent.status != MediaUploadIntent.STATUS_CONFIRMED:
        raise ValidationError({"detail": f"This upload is not confirmed (status={intent.status})."})

    return asset, intent


def attach_media(
    *, user, asset_id, target_type: str, target_id: str, slot: str | None = None, position: int | None = None,
) -> MediaAsset:
    """Steps 1-15 of the platform design's generic attach flow. Raises
    NotFound/ValidationError/PermissionDenied (the latter only from a
    purpose's authorize_target hook) on rejection; returns the (possibly
    freshly re-fetched) MediaAsset on success — callers serialize it with
    the canonical asset serializer, never a feature-specific shape."""
    target_id = str(target_id)
    logger.info(
        "media_attach_started",
        extra={"asset_id": str(asset_id), "owner_id": str(user.id), "target_type": target_type, "target_id": target_id},
    )

    asset, intent = _get_owned_confirmed_asset(user=user, asset_id=asset_id)

    if not lifecycle.is_attachable(asset):
        reason = "deleted" if asset.deleted_at else "quarantined_or_blocked"
        logger.info(
            "media_attach_rejected",
            extra={"asset_id": str(asset.id), "purpose": asset.purpose, "owner_id": str(user.id), "reason_code": reason},
        )
        raise ValidationError({"detail": "This media cannot be attached (deleted, quarantined, or blocked)."})

    if not asset.purpose:
        raise ValidationError({"detail": "This media has no registered purpose and cannot be attached generically."})

    try:
        purpose = purposes.get_purpose(asset.purpose)
    except KeyError:
        raise ValidationError({"detail": "Unknown media purpose."})

    if purpose.target_types and target_type not in purpose.target_types:
        logger.info(
            "media_attach_rejected",
            extra={
                "asset_id": str(asset.id), "purpose": asset.purpose, "owner_id": str(user.id),
                "target_type": target_type, "reason_code": "wrong_target_type",
            },
        )
        raise ValidationError(
            {"targetType": f"This media purpose only supports target types: {', '.join(purpose.target_types)}."}
        )

    # Idempotent-retry / reuse rejection, checked BEFORE dispatching to the
    # attach handler — an already-attached intent must never be replayed
    # through resolve_confirmed_intent-style "attached_at must be NULL"
    # logic a second time.
    if intent.attached_at is not None and not purpose.allow_reuse:
        if asset.target_type == target_type and asset.target_id == target_id:
            logger.info(
                "media_attached",
                extra={
                    "asset_id": str(asset.id), "purpose": asset.purpose, "owner_id": str(user.id),
                    "target_type": target_type, "target_id": target_id, "reason_code": "idempotent_replay",
                },
            )
            return asset
        logger.info(
            "media_attach_rejected",
            extra={
                "asset_id": str(asset.id), "purpose": asset.purpose, "owner_id": str(user.id),
                "target_type": target_type, "target_id": target_id, "reason_code": "already_attached",
            },
        )
        raise ValidationError(
            {"detail": "This media has already been attached and cannot be reused for a different target."}
        )

    if not purpose.allow_attach or purpose.attach_handler is None:
        logger.info(
            "media_attach_rejected",
            extra={
                "asset_id": str(asset.id), "purpose": asset.purpose, "owner_id": str(user.id),
                "reason_code": "attach_not_supported",
            },
        )
        raise ValidationError(
            {
                "detail": (
                    "This media purpose is attached at creation time through its own "
                    "feature endpoint, not through the generic attach endpoint."
                )
            }
        )

    if purpose.authorize_target is not None:
        # May raise PermissionDenied/NotFound/ValidationError — propagates
        # to the caller untouched. Verifies the caller may attach media
        # onto THIS target instance (e.g. "does this user own this shop"),
        # separate from the asset-ownership check already done above.
        purpose.authorize_target(user=user, target_type=target_type, target_id=target_id)

    with transaction.atomic():
        asset = MediaAsset.objects.select_for_update().get(id=asset.id)
        intent = MediaUploadIntent.objects.select_for_update().get(id=intent.id)

        if intent.attached_at is not None and not purpose.allow_reuse:
            # Raced with another request that attached it first.
            if asset.target_type == target_type and asset.target_id == target_id:
                return asset
            raise ValidationError(
                {"detail": "This media has already been attached and cannot be reused for a different target."}
            )

        # The feature's own attach logic — may raise (PermissionDenied,
        # NotFound, ValidationError, or a domain exception), which rolls
        # back this whole transaction, leaving both records unchanged.
        purpose.attach_handler(
            user=user, asset=asset, target_type=target_type, target_id=target_id, slot=slot, position=position,
        )
        lifecycle.sync_attachment(intent=intent, target_type=target_type, target_id=target_id)

    asset.refresh_from_db()
    logger.info(
        "media_attached",
        extra={
            "asset_id": str(asset.id), "purpose": asset.purpose, "owner_id": str(user.id),
            "target_type": target_type, "target_id": target_id,
        },
    )
    return asset
