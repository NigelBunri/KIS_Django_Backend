# apps/media/services/chat_voice_playback.py
"""
The chokepoint behind Django's trusted-internal chat-voice sign endpoint
(views_internal.ChatVoicePlaybackSignView). Nest calls this AFTER it has
already authenticated the human user and verified they are a member of the
conversation the voice message belongs to (see Nest's
VoicePlaybackService/DjangoConversationClient.assertMember) — this function
deliberately does not re-derive that; Django has no notion of Nest's
conversation membership and trusting Nest's internal-auth-signed request is
the whole point of this endpoint (apps.chat.internal_auth already
establishes this same internal-service trust model for other Nest->Django
calls).

What Django DOES still verify, independent of any user:
  - the media asset exists, is not deleted/expired/quarantined
    (lifecycle.is_downloadable);
  - it reached "ready" status (excludes legacy multipart uploads still
    pending/blocked by content-safety review — see apps.media.views.
    UploadFileView, which sets status="pending" for anything
    quarantined/requires_review);
  - it was uploaded through a messaging-eligible context (chat/dm/group/
    partner/status — apps.media.safety.MESSAGING_UPLOAD_CONTEXTS), so this
    endpoint can't be used to sign, say, someone's private profile avatar or
    a marketplace complaint attachment just because Nest can reach it;
  - it is actually audio (this endpoint is scoped to voice notes);
  - if Nest supplied objectKey for cross-checking, it matches the asset's
    real bucket_key exactly.
"""

from __future__ import annotations

import os
from datetime import timedelta
from urllib.parse import quote

from django.conf import settings
from django.core.files.storage import default_storage
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied

from ..models import MediaAsset
from ..safety import MESSAGING_UPLOAD_CONTEXTS
from ..signing import MEDIA_SIGNED_URL_TTL_SECONDS, sign_media_asset_token
from . import lifecycle

# Independent of MEDIA_SIGNED_URL_TTL_SECONDS (that one guards the fallback
# Django-proxy-download path below, and messages sent before this refresh
# flow existed). This is the TTL for the real, direct-to-S3 presigned GET
# a healthy S3-backed deployment actually returns.
CHAT_VOICE_PLAYBACK_TTL_SECONDS = int(os.environ.get("CHAT_VOICE_PLAYBACK_TTL_SECONDS", "900"))


def sign_chat_voice_asset(asset: MediaAsset, *, expected_object_key: str | None = None) -> dict:
    if not lifecycle.is_downloadable(asset):
        raise NotFound("Media asset not found.")

    if asset.status != "ready":
        raise PermissionDenied("This media is not available for playback.")

    context = str((asset.metadata or {}).get("context") or "").strip().lower()
    if context not in MESSAGING_UPLOAD_CONTEXTS:
        raise PermissionDenied("This media is not eligible for chat playback.")

    mime = str(asset.mime_type or "").lower()
    if not mime.startswith("audio/"):
        raise PermissionDenied("This media is not a voice attachment.")

    if not asset.bucket_key:
        raise NotFound("Media file is not available.")

    if expected_object_key and asset.bucket_key != expected_object_key:
        # A mismatched caller-supplied key is treated as "not found", not
        # "forbidden" — never confirm or deny the existence of some other,
        # different object by the error shape alone.
        raise NotFound("Media asset not found.")

    ttl = CHAT_VOICE_PLAYBACK_TTL_SECONDS
    presign = getattr(default_storage, "generate_presigned_get", None)
    if callable(presign):
        url = presign(asset.bucket_key, ttl)
        expires_at = timezone.now() + timedelta(seconds=ttl)
        return {
            "url": url,
            "expiresAt": expires_at.isoformat(),
            "expiresInSeconds": ttl,
        }

    # Storage backend has no native presigned-GET (e.g. Supabase's REST
    # backend, or local-disk dev) — fall back to Django's own short-TTL
    # proxy-download token. Still short-lived, still re-validated at
    # request time by MediaAssetViewSet.download's _token_allows_asset.
    token = sign_media_asset_token(asset)
    path = f"/api/v1/assets/{asset.id}/download/?token={quote(token)}"
    base = str(getattr(settings, "API_BASE_URL", "") or "").rstrip("/")
    real_ttl = MEDIA_SIGNED_URL_TTL_SECONDS
    expires_at = timezone.now() + timedelta(seconds=real_ttl)
    return {
        "url": f"{base}{path}" if base else path,
        "expiresAt": expires_at.isoformat(),
        "expiresInSeconds": real_ttl,
    }
