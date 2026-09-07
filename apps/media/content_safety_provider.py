"""kis-content-safety client — self-hosted NudeNet explicit-content
detection, extracted out of this process into its own service. Mirrors
apps/broadcasts/kisvideo_provider.py's shape one file over: settings-driven
constructor, a _require_credentials() guard, a dedicated *ProviderError
exception, network failures funneled through that one exception type so
callers only ever need to catch one thing.

This is a "dumb detector" client: it returns the model's raw (label, score)
detection, filtered to the platform's explicit-label set by the service
itself. The confidence-threshold decision (blocked vs. pending_review vs.
passed) stays in Django (apps/media/safety.py::run_nudenet_scan_on_file),
unchanged in shape from before this service existed — this client is a
drop-in replacement for that function's in-process _scan_image_file/
_scan_video_file calls, nothing more.
"""

from __future__ import annotations

import requests as _requests
from django.conf import settings
from requests.exceptions import RequestException

# Video scans involve ffmpeg frame extraction + several inference calls on
# the service side (its own scan_timeout_seconds bounds that, ~45s default);
# this just needs headroom on top for network transfer of a real video file.
# Images are a single fast inference call.
_IMAGE_TIMEOUT_SECONDS = 30
_VIDEO_TIMEOUT_SECONDS = 300


class ContentSafetyProviderError(Exception):
    pass


def content_safety_service_enabled() -> bool:
    return bool(getattr(settings, "MEDIA_SAFETY_SERVICE_ENABLED", False))


class ContentSafetyProvider:
    def __init__(self) -> None:
        self.base_url = str(getattr(settings, "MEDIA_SAFETY_SERVICE_BASE_URL", "") or "").rstrip("/")
        self.internal_token = str(getattr(settings, "MEDIA_SAFETY_SERVICE_INTERNAL_TOKEN", "") or "")

    def _require_credentials(self) -> None:
        if not self.base_url or not self.internal_token:
            raise ContentSafetyProviderError(
                "MEDIA_SAFETY_SERVICE_BASE_URL and MEDIA_SAFETY_SERVICE_INTERNAL_TOKEN must be set."
            )

    def _headers(self) -> dict[str, str]:
        return {"X-Internal-Auth": self.internal_token}

    def scan(self, file_obj, *, filename: str, content_type: str) -> tuple[str | None, float]:
        """`file_obj` is any already-open, read-from-start file-like object
        — a Django UploadedFile, a default_storage.open() handle, or a
        plain local `open(path, "rb")`. Dispatches to /scan/image or
        /scan/video by content_type, since that's exactly the same
        video/-prefix check run_nudenet_scan_on_file already makes today."""
        self._require_credentials()
        is_video = (content_type or "").startswith("video/")
        route = "/scan/video" if is_video else "/scan/image"
        timeout = _VIDEO_TIMEOUT_SECONDS if is_video else _IMAGE_TIMEOUT_SECONDS

        try:
            resp = _requests.post(
                f"{self.base_url}{route}",
                headers=self._headers(),
                files={"file": (filename, file_obj, content_type or "application/octet-stream")},
                timeout=timeout,
            )
        except RequestException as exc:
            # Funnel every network failure mode (timeout, connection reset,
            # DNS blip) through the one exception type callers handle -
            # found the hard way on kisvideo_provider.py's first version,
            # not repeating that gap here.
            raise ContentSafetyProviderError(f"content-safety request failed: {exc}") from exc
        if not resp.ok:
            raise ContentSafetyProviderError(
                f"content-safety {route} failed ({resp.status_code}): {resp.text[:500]}"
            )
        body = resp.json()
        return body.get("label"), float(body.get("score") or 0.0)
