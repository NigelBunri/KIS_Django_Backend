# apps/media/services/access.py
"""
The one access chokepoint every generic download/signed-URL path uses.
Phase 2 of the KIS Universal Media Platform.

can_user_access_media() deliberately does NOT implement one generic
owner-or-staff rule for every purpose — status visibility (audience
exclusions, blocks, mutual contacts), complaint authorization (buyer or
shop staff), and marketplace catalog visibility are genuinely different
rules that stay owned by their feature apps, dispatched by purpose via
`access_authorizer` hooks registered on apps.media.purposes. This module
only owns: lifecycle-state gating (centralized, the same for every
purpose), the owner-always-allowed shortcut, and dispatch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.core.files.storage import default_storage
from rest_framework.exceptions import NotFound, PermissionDenied

from .. import purposes
from ..models import MediaAsset, MediaModerationState
from . import lifecycle

logger = logging.getLogger("apps.media.access")


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason_code: str = ""

    @classmethod
    def allow(cls) -> "AccessDecision":
        return cls(allowed=True)

    @classmethod
    def deny(cls, reason_code: str) -> "AccessDecision":
        return cls(allowed=False, reason_code=reason_code)


def can_user_access_media(user, asset: MediaAsset) -> AccessDecision:
    """The chokepoint. Order of checks:
    1. Lifecycle state (deleted/quarantined) — same for every purpose.
    2. Owner shortcut — an owner may always see their own media, regardless
       of purpose-specific visibility rules (you can always view your own
       shop image, status, or complaint attachment).
    3. Dispatch to the purpose's registered access_authorizer.
    A purpose with no registry entry (legacy multipart MediaAsset rows,
    purpose="") or no registered access_authorizer denies by default —
    correctness over convenience: a missing hook must never silently mean
    "everyone can see this."
    """
    if not lifecycle.is_downloadable(asset):
        if asset.deleted_at is not None:
            return AccessDecision.deny("not_found")
        if asset.moderation_state == MediaModerationState.QUARANTINED:
            return AccessDecision.deny("quarantined")
        return AccessDecision.deny("not_available")

    if user is not None and getattr(user, "is_authenticated", False) and asset.owner_id == user.id:
        return AccessDecision.allow()

    if not asset.purpose:
        return AccessDecision.deny("no_authorizer")

    try:
        purpose = purposes.get_purpose(asset.purpose)
    except KeyError:
        return AccessDecision.deny("unknown_purpose")

    if purpose.access_authorizer is None:
        return AccessDecision.deny("no_authorizer")

    try:
        decision = purpose.access_authorizer(user, asset)
    except Exception:
        logger.exception(
            "media_access_authorizer_error",
            extra={"asset_id": str(asset.id), "purpose": asset.purpose},
        )
        return AccessDecision.deny("authorizer_error")

    if not isinstance(decision, AccessDecision):
        logger.error(
            "media_access_authorizer_bad_return",
            extra={"asset_id": str(asset.id), "purpose": asset.purpose},
        )
        return AccessDecision.deny("authorizer_error")
    return decision


def get_signed_url(*, user, asset: MediaAsset) -> dict:
    """Runs the access chokepoint, then issues a fresh short-lived
    presigned GET. Never logs the URL itself — only asset/purpose/owner
    metadata and the expiry, matching the platform design's "the full
    signed query string must not be written to logs" requirement."""
    decision = can_user_access_media(user, asset)
    owner_id = str(asset.owner_id) if asset.owner_id else ""

    if not decision.allowed:
        logger.info(
            "media_access_denied",
            extra={
                "asset_id": str(asset.id), "purpose": asset.purpose, "owner_id": owner_id,
                "reason_code": decision.reason_code,
            },
        )
        if decision.reason_code == "not_found":
            raise NotFound("Media not found.")
        raise PermissionDenied("You do not have access to this media.")

    url = default_storage.url(asset.bucket_key)
    expires_in = int(getattr(default_storage, "presigned_expiry", 3600))

    logger.info(
        "media_signed_url_issued",
        extra={
            "asset_id": str(asset.id), "purpose": asset.purpose, "owner_id": owner_id,
            "expires_in": expires_in,
        },
    )
    return {
        "assetId": str(asset.id),
        "url": url,
        "expiresInSeconds": expires_in,
        "mimeType": asset.mime_type,
        "size": asset.size,
        "originalFilename": asset.original_filename,
        "width": asset.width,
        "height": asset.height,
        "durationMs": asset.duration_ms,
    }
