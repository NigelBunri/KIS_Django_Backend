# apps/broadcasts/views_internal.py
"""
Trusted-internal-service endpoints for apps/broadcasts. Callers here are
other KIS backend services (currently only Nest), never end-user devices
directly — authorization is `apps.chat.internal_auth.require_internal_auth`,
the same HMAC-signed internal-token mechanism apps/media/views_internal.py
already uses for its own Nest<->Django call (chat-voice/sign).

Why this exists: video uploads used to go client -> Django -> S3, entirely
inline in one request, because BroadcastVideoUploadView's server-side work
(duration probe, video_type classification, BroadcastVideo row creation,
thumbnail generation) needs the file. Moving the byte transfer to Nest's
direct-to-S3 signed-URL flow (see uploadFileToBackend.ts / KIS RN) removes
Django from the upload path itself, but that server-side work still has to
happen somewhere — this endpoint is where it happens now: Nest calls it
AFTER confirming the object landed in S3, passing the object key instead of
a file. Django reads the object back from the SAME S3 bucket (confirmed to
be shared between the two services) to probe/process it, exactly the way
BroadcastVideoUploadView already does today for any non-local storage
backend (see _probe_video_duration_from_storage / ensure_local_thumbnail in
this app, both already S3-aware).
"""

from __future__ import annotations

import uuid

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.channels.models import Channel
from apps.chat.internal_auth import require_internal_auth
from apps.media.models import MediaSafetyScan
from apps.media.safety import (
    hash_upload,
    normalize_upload_context,
    scan_upload_for_explicit_content,
    user_safe_upload_response,
)

from .media_pipeline import prepare_channel_asset_payload
from .media_utils import build_media_url, ensure_local_thumbnail
from .models import BroadcastVideo
from .views import LONG_VIDEO_MIN_SECONDS, _probe_video_duration_from_storage

User = get_user_model()


class _ObjectKeyFile:
    """Minimal shim exposing just what hash_upload() needs (an object with
    .chunks()) — reads the already-uploaded S3 object via default_storage
    instead of an in-request Django UploadedFile. Kept separate from a real
    UploadedFile because nothing else here needs the full interface."""

    def __init__(self, object_key: str):
        self._object_key = object_key

    def chunks(self):
        with default_storage.open(self._object_key, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                yield chunk

    def tell(self):
        return 0

    def seek(self, _pos):
        return None


class ProcessBroadcastVideoUploadView(APIView):
    """POST /api/v1/broadcasts/internal/process-video-upload/

    Called by Nest immediately after confirming a direct-to-S3 video upload
    landed (see uploads/upload-intent.service.ts's confirm() on the Nest
    side). Runs the same duration-probe/classification/BroadcastVideo-
    creation/thumbnail work BroadcastVideoUploadView does inline, just
    against an already-stored S3 object instead of an in-request file.

    Body: {
      "objectKey": "<S3 key, required>",
      "mimeType": "<required>",
      "originalFilename": "<required>",
      "sizeBytes": <int, required>,
      "title": "<optional>",
      "description": "<optional>",
      "channelId": "<optional>",
      "userId": "<optional — Django user id, for the safety-scan audit row>",
    }
    Response: same shape as BroadcastVideoUploadView's payload (video_id,
    video_url, thumbnail_url, duration_seconds, scan_status, etc.), minus
    fields that only make sense in an authenticated-request context
    (transcript_segments is not accepted here — Nest doesn't have that data;
    pass it through a follow-up PATCH if ever needed).
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "broadcast_process_video_upload"

    def post(self, request):
        require_internal_auth(request)

        object_key = str(request.data.get("objectKey") or "").strip()
        mime_type = str(request.data.get("mimeType") or "").strip()
        original_filename = str(request.data.get("originalFilename") or "upload").strip()
        size_bytes = request.data.get("sizeBytes")
        title = str(request.data.get("title") or "").strip()
        description = str(request.data.get("description") or "")
        channel_id = request.data.get("channelId")
        user_id = str(request.data.get("userId") or "").strip()

        if not object_key:
            raise ValidationError({"objectKey": "This field is required."})
        if not mime_type:
            raise ValidationError({"mimeType": "This field is required."})
        try:
            size_bytes = int(size_bytes)
        except (TypeError, ValueError):
            raise ValidationError({"sizeBytes": "This field must be an integer."})

        if not default_storage.exists(object_key):
            raise NotFound("Uploaded object not found in storage.")

        owner = None
        if user_id:
            owner = User.objects.filter(id=user_id).first()

        channel = None
        if channel_id:
            channel = Channel.objects.filter(id=channel_id).first()

        normalized_context = normalize_upload_context("broadcast")
        checksum = hash_upload(_ObjectKeyFile(object_key))
        decision = scan_upload_for_explicit_content(
            filename=original_filename,
            mime_type=mime_type,
            context=normalized_context,
        )
        safety_scan = MediaSafetyScan.objects.create(
            owner=owner,
            upload_id=uuid.uuid4().hex,
            context=normalized_context,
            original_name=original_filename,
            mime_type=mime_type,
            bytes=size_bytes,
            checksum=checksum,
            provider=decision.provider,
            status=decision.status,
            quarantine=decision.quarantine,
            requires_review=decision.requires_review,
            policy_version=decision.policy_version,
            reason=decision.reason,
            result=decision.as_metadata(),
        )

        duration = _probe_video_duration_from_storage(object_key)
        video_type = "short" if duration < LONG_VIDEO_MIN_SECONDS else "video"

        video = BroadcastVideo.objects.create(
            title=title or original_filename,
            description=description,
            channel=channel,
            creator=owner,
            video_url="",
            thumbnail_url="",
            mime_type=mime_type,
            storage_path=object_key,
            type=video_type,
            duration_seconds=int(round(duration)),
        )
        video.video_url = "" if decision.quarantine else build_media_url(None, object_key)
        ensure_local_thumbnail(video)
        video.save(update_fields=["video_url"])

        payload = {
            "video_id": str(video.id),
            "video_url": video.video_url,
            "stream_url": build_media_url(None, object_key),
            "thumbnail_url": video.thumbnail_url or "",
            "duration_seconds": video.duration_seconds,
            "type": video.type,
            "mime_type": mime_type,
            "scan_status": decision.status,
            "quarantined": decision.quarantine,
            "requires_review": decision.requires_review,
            "safety_scan_id": str(safety_scan.id),
            "safety": user_safe_upload_response(decision),
            "processing_status": "pending_review" if (decision.requires_review or decision.quarantine) else "ready",
        }
        payload["pipeline"] = prepare_channel_asset_payload(
            {
                "asset_type": "short_video" if video.type == "short" else "video",
                "url": payload["video_url"],
                "storage_path": object_key,
                "mime_type": mime_type,
                "size_bytes": size_bytes,
                "duration_seconds": video.duration_seconds,
                "thumbnail_url": payload["thumbnail_url"],
                "processing_status": payload["processing_status"],
                "metadata": {
                    "safety": payload["safety"],
                    "safety_scan_id": str(safety_scan.id),
                },
            },
            content_type="short_video" if video.type == "short" else "video",
        ).get("metadata", {}).get("pipeline", {})

        return Response(payload, status=201)
