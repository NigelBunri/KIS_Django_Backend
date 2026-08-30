# apps/media/views_internal.py
"""
Trusted-internal-service endpoints for apps/media. Callers here are other
KIS backend services (currently only Nest), never end-user devices directly
— authorization is `apps.chat.internal_auth.require_internal_auth` (the same
HMAC-signed internal-token mechanism apps/broadcasts and apps/notifications
already reuse for their own Nest<->Django calls), not a Django user session.
"""

from __future__ import annotations

from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.chat.internal_auth import require_internal_auth

from .models import MediaAsset
from .services.chat_voice_playback import sign_chat_voice_asset


class ChatVoicePlaybackSignView(APIView):
    """POST /api/v1/media/internal/chat-voice/sign/

    Called by Nest's VoicePlaybackService AFTER Nest has already
    authenticated the requesting human user and verified conversation
    membership. Django's job here is narrower and independent of any
    end-user: confirm the media object is real, attached to a
    messaging-eligible upload, not deleted/quarantined/pending, and sign a
    short-lived playback URL for it. See
    apps.media.services.chat_voice_playback for the full eligibility rules.

    Body: {"mediaAssetId": "<uuid>", "objectKey": "<optional cross-check>"}
    Response: {"url": "...", "expiresAt": "<ISO-8601>", "expiresInSeconds": N}
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "chat_voice_sign"

    def post(self, request):
        require_internal_auth(request)

        media_asset_id = str(
            request.data.get("mediaAssetId") or request.data.get("media_asset_id") or ""
        ).strip()
        object_key = str(
            request.data.get("objectKey") or request.data.get("object_key") or ""
        ).strip()
        if not media_asset_id:
            raise ValidationError({"mediaAssetId": "This field is required."})

        try:
            asset = MediaAsset.objects.get(id=media_asset_id, is_deleted=False)
        except (MediaAsset.DoesNotExist, ValueError, TypeError):
            raise NotFound("Media asset not found.")

        result = sign_chat_voice_asset(asset, expected_object_key=object_key or None)
        return Response(result)


class ScanUploadedObjectView(APIView):
    """POST /api/v1/media/internal/scan-upload/

    Called by Nest right after confirming ANY direct-to-S3 upload (see
    UploadIntentService.confirm() on the Nest side — every context, not
    just broadcast video). Enqueues the async explicit-content scan and
    returns immediately; this deliberately does NOT wait for the scan to
    finish, so it never adds latency to the upload confirm response the
    end user is waiting on. See apps/media/tasks.py's
    scan_uploaded_object_task for what actually runs the model.

    Body: {"objectKey", "mimeType", "originalFilename", "sizeBytes",
           "context", "userId"}
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "media_scan_upload"

    def post(self, request):
        require_internal_auth(request)

        object_key = str(request.data.get("objectKey") or "").strip()
        mime_type = str(request.data.get("mimeType") or "").strip()
        original_filename = str(request.data.get("originalFilename") or "upload").strip()
        context = str(request.data.get("context") or "general").strip()
        user_id = str(request.data.get("userId") or "").strip() or None
        try:
            size_bytes = int(request.data.get("sizeBytes") or 0)
        except (TypeError, ValueError):
            size_bytes = 0

        if not object_key:
            raise ValidationError({"objectKey": "This field is required."})

        from .tasks import scan_uploaded_object_task

        scan_uploaded_object_task.delay(
            object_key=object_key,
            mime_type=mime_type,
            original_filename=original_filename,
            size_bytes=size_bytes,
            context=context,
            owner_id=user_id,
        )
        return Response({"ok": True}, status=202)
