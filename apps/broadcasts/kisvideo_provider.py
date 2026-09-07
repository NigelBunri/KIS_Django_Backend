"""kisvideo client — self-hosted VOD transcode service, replaces Mux for
video-on-demand only (live streaming stays on Mux, see
live_stream_providers.py, which this module does not touch).

Mirrors MuxProvider's shape one file over: env/settings-driven constructor,
a _require_credentials() guard, a dedicated *ProviderError exception, and
one public method per provider operation returning a small normalized dict.

kisvideo's upload API (app/api/uploads.py in the kisvideo repo) implements
the tus resumable-upload protocol (tus.io) rather than a single
"give me a URL" endpoint, because a real end-user client (mobile app)
needs true resumability. Django's own call here is server-to-server —
the file is already sitting in S3 by the time this runs — so there is
nothing to resume from; this pushes the whole object in one Creation
(POST) + Upload (PATCH) round trip rather than implementing tus's client
retry/resume logic, which server-to-server has no use for.

kisvideo's own auth (app/api/deps.py::require_internal_auth) is a bare
shared-token comparison, not Nest's HMAC-signed scheme
(apps.chat.internal_signing) — so only X-Internal-Auth is sent here,
nothing more.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Any

import requests as _requests
from django.conf import settings
from django.core.files.storage import default_storage
from requests.exceptions import RequestException


class KisVideoProviderError(Exception):
    pass


def kisvideo_enabled() -> bool:
    return bool(getattr(settings, "KIS_VIDEO_SERVICE_ENABLED", False))


def sign_kisvideo_callback_token(asset_id: str) -> str:
    """kisvideo's own webhook POST (app/workers/transcode.py::_send_webhook)
    sends no auth header at all — it just POSTs to whatever callback_url
    Django supplied at upload-creation time. So the receiver
    (KisVideoJobCallbackView) can't check a header; instead this signs a
    token INTO that callback_url as a query param, which kisvideo blindly
    echoes back verbatim when it fires the webhook. Verifying the token on
    receipt confirms the request is replaying a URL Django itself minted
    for this specific asset, without requiring any change to kisvideo's
    (already-shipped, out of my scope) webhook sender."""
    secret = str(getattr(settings, "KIS_VIDEO_SERVICE_INTERNAL_TOKEN", "") or "")
    return hmac.new(secret.encode("utf-8"), asset_id.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_kisvideo_callback_token(asset_id: str, token: str) -> bool:
    expected = sign_kisvideo_callback_token(asset_id)
    return bool(expected) and hmac.compare_digest(expected, str(token or ""))


def _b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


class KisVideoProvider:
    def __init__(self) -> None:
        self.base_url = str(getattr(settings, "KIS_VIDEO_SERVICE_BASE_URL", "") or "").rstrip("/")
        self.internal_token = str(getattr(settings, "KIS_VIDEO_SERVICE_INTERNAL_TOKEN", "") or "")

    def _require_credentials(self) -> None:
        if not self.base_url or not self.internal_token:
            raise KisVideoProviderError(
                "KIS_VIDEO_SERVICE_BASE_URL and KIS_VIDEO_SERVICE_INTERNAL_TOKEN must be set."
            )

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"X-Internal-Auth": self.internal_token}
        if extra:
            headers.update(extra)
        return headers

    def create_transcode_job(
        self,
        *,
        storage_path: str,
        filename: str,
        content_type: str,
        owner_user_id: str,
        callback_url: str,
        caller_reference: str,
        chunk_size_bytes: int = 8 * 1024 * 1024,
    ) -> dict[str, Any]:
        """Pushes the already-uploaded S3 object at storage_path through
        kisvideo's tus upload API in one call. Returns
        {"upload_id": ...} on success — kisvideo does not hand back a
        TranscodeJob id from this flow (see app/api/uploads.py's
        patch_upload — job creation happens server-side once the upload
        completes, invisibly to the caller); caller_reference is the only
        correlation key the eventual webhook callback carries, which is
        exactly why callback_url/caller_reference are required args here,
        not optional ones.
        """
        self._require_credentials()
        if not default_storage.exists(storage_path):
            raise KisVideoProviderError(f"storage_path does not exist in default_storage: {storage_path}")
        total_bytes = default_storage.size(storage_path)

        try:
            create_resp = _requests.post(
                f"{self.base_url}/uploads",
                headers=self._headers(
                    {
                        "Upload-Length": str(total_bytes),
                        "Upload-Metadata": f"filename {_b64(filename)},filetype {_b64(content_type)}",
                        "X-Owner-User-Id": str(owner_user_id),
                        "X-Callback-Url": callback_url,
                        "X-Caller-Reference": caller_reference,
                    }
                ),
                timeout=15,
            )
            if not create_resp.ok:
                raise KisVideoProviderError(
                    f"kisvideo POST /uploads failed ({create_resp.status_code}): {create_resp.text[:500]}"
                )
            location = create_resp.headers.get("Location", "")
            upload_id = location.rstrip("/").rsplit("/", 1)[-1] if location else ""
            if not upload_id:
                raise KisVideoProviderError("kisvideo POST /uploads did not return a Location header.")

            offset = 0
            with default_storage.open(storage_path, "rb") as remote_file:
                while offset < total_bytes:
                    chunk = remote_file.read(chunk_size_bytes)
                    if not chunk:
                        break
                    patch_resp = _requests.patch(
                        f"{self.base_url}/uploads/{upload_id}",
                        headers=self._headers(
                            {
                                "Upload-Offset": str(offset),
                                "Content-Type": "application/offset+octet-stream",
                            }
                        ),
                        data=chunk,
                        timeout=60,
                    )
                    if not patch_resp.ok:
                        raise KisVideoProviderError(
                            f"kisvideo PATCH /uploads/{upload_id} failed at offset {offset} "
                            f"({patch_resp.status_code}): {patch_resp.text[:500]}"
                        )
                    offset += len(chunk)
        except RequestException as exc:
            # A raw network failure (timeout, connection reset, DNS blip -
            # very plausible on a 60s-timeout PATCH loop streaming a full
            # video) must funnel through KisVideoProviderError like every
            # other failure mode here, since push_asset_to_kisvideo's
            # retry/failure handling only catches that one exception type -
            # an uncaught RequestException would bypass retries entirely
            # and strand the asset at 'queued' forever.
            raise KisVideoProviderError(f"kisvideo request failed: {exc}") from exc

        if offset != total_bytes:
            raise KisVideoProviderError(
                f"Uploaded {offset} bytes but storage_path reported {total_bytes} bytes for {storage_path}."
            )

        return {"upload_id": upload_id}
