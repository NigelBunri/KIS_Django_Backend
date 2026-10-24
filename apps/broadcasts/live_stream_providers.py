"""
Live-streaming provider adapters.

Set LIVE_STREAM_PROVIDER=kisvideo (plus KIS_VIDEO_LIVE_SERVICE_URL,
KIS_VIDEO_LIVE_SERVICE_INTERNAL_TOKEN) to use the self-hosted kisvideo-live
RTMP+WHIP ingest service - the only provider now that Mux has been fully
retired (production cut over 2026-09-23, validated end-to-end with real
external RTMP/HLS/WHIP-WebRTC clients against the live deployment first -
see kisvideo-live's README for the full validation record). MuxProvider
and its MUX_TOKEN_ID/MUX_TOKEN_SECRET/MUX_WEBHOOK_SECRET env vars are
gone - if live streaming is ever ported to a different external provider
again, git history has the old adapter as a reference, but there's no
reason to keep dead code around "just in case."

All other LIVE_STREAM_PROVIDER values keep the disabled/dev-URL behaviour.
"""

import logging
import os
from typing import Any, Dict, Optional

import requests as _requests

logger = logging.getLogger(__name__)


def redact_stream_ref(value: Any) -> str:
    """Logging helper: provider_stream_id/ingest_url for the kisvideo
    provider embed the raw RTMP/WHIP stream key as their last path segment
    (see kisvideo-live's app/main.py::create_stream) - never log either in
    full. Mirrors kisvideo-live's own redact_stream_id (app/redact.py) so a
    redacted id grepped in one service's logs matches the other's."""
    text = str(value or "")
    if not text:
        return text
    prefix, sep, key = text.rpartition("/")
    if not sep:
        return "***" if len(text) > 4 else text
    if len(key) <= 4:
        return f"{prefix}/***"
    return f"{prefix}/...{key[-4:]}"


class LiveStreamProviderError(Exception):
    pass


class KisVideoLiveProvider:
    """
    Thin wrapper around kisvideo-live, the self-hosted MediaMTX-based
    RTMP+WHIP ingest service that replaces Mux for live streaming
    (mirrors kisvideo_provider.py's KisVideoProvider - the VOD-side
    client one file over - which uses the same X-Internal-Auth header
    scheme against kisvideo's app/api/deps.py::require_internal_auth;
    reused here for consistency rather than inventing a second auth
    convention for the same service family).

    Deliberately does NOT implement verify_webhook_signature /
    map_webhook_status / extract_webhook_stream_id / extract_viewer_count -
    ChannelLiveStreamWebhookView (views.py) hasattr()-guards every one of
    those calls and falls back to its generic X-Live-Webhook-Secret +
    {provider_stream_id, status, viewer_count} JSON contract whenever
    they're absent, which is exactly the shape kisvideo-live's webhook
    hooks POST. No Mux-style HMAC envelope needed for this provider.
    """

    def __init__(self) -> None:
        self.base_url = os.environ.get("KIS_VIDEO_LIVE_SERVICE_URL", "").rstrip("/")
        self.internal_token = os.environ.get("KIS_VIDEO_LIVE_SERVICE_INTERNAL_TOKEN", "")

    def _require_credentials(self) -> None:
        if not self.base_url or not self.internal_token:
            logger.error(
                "[kisvideo-live] not configured: base_url=%r internal_token_set=%s",
                self.base_url, bool(self.internal_token),
            )
            raise LiveStreamProviderError(
                "KIS_VIDEO_LIVE_SERVICE_URL and KIS_VIDEO_LIVE_SERVICE_INTERNAL_TOKEN environment variables must be set."
            )

    def _headers(self) -> Dict[str, str]:
        return {"X-Internal-Auth": self.internal_token}

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_live_stream(
        self,
        *,
        reduced_latency: bool = True,
        reconnect_window: int = 30,
    ) -> Dict[str, Any]:
        # reduced_latency/reconnect_window are Mux-specific tuning knobs
        # with no kisvideo-live equivalent in the v1 passthrough design -
        # accepted for call-site compatibility with MuxProvider.create_live_stream
        # (ChannelLiveStreamListCreateView.post calls this with no args
        # either way) and otherwise ignored.
        self._require_credentials()

        logger.info("[kisvideo-live] POST %s/streams", self.base_url)
        try:
            resp = _requests.post(
                f"{self.base_url}/streams",
                headers=self._headers(),
                timeout=15,
            )
        except Exception as exc:
            logger.error("[kisvideo-live] create_live_stream request failed: %s", exc, exc_info=True)
            raise LiveStreamProviderError(f"kisvideo-live request failed: {exc}") from exc
        if not resp.ok:
            logger.error(
                "[kisvideo-live] create_live_stream returned %s: %s",
                resp.status_code, resp.text[:500],
            )
            raise LiveStreamProviderError(
                f"kisvideo-live API returned {resp.status_code}: {resp.text[:500]}"
            )

        data = resp.json()
        logger.info(
            "[kisvideo-live] stream created provider_stream_id=%s whip_url=%s playback_url=%s",
            redact_stream_ref(data.get("provider_stream_id", "")),
            redact_stream_ref(data.get("whip_url", "")),
            data.get("playback_url", ""),
        )
        return {
            "provider": "kisvideo",
            "provider_stream_id": data.get("provider_stream_id", ""),
            "ingest_url": data.get("ingest_url", ""),
            "whip_url": data.get("whip_url", ""),
            "playback_url": data.get("playback_url", ""),
            "stream_key": data.get("stream_key", ""),
            "raw": data,
        }

    def delete_live_stream(self, provider_stream_id: str) -> bool:
        if not self.base_url or not self.internal_token:
            logger.warning("[kisvideo-live] delete_live_stream skipped — service not configured")
            return False
        try:
            logger.info("[kisvideo-live] DELETE %s/streams/%s", self.base_url, redact_stream_ref(provider_stream_id))
            resp = _requests.delete(
                f"{self.base_url}/streams/{provider_stream_id}",
                headers=self._headers(),
                timeout=10,
            )
            if not resp.ok:
                logger.warning(
                    "[kisvideo-live] delete_live_stream for %s returned %s: %s",
                    redact_stream_ref(provider_stream_id), resp.status_code, resp.text[:300],
                )
            return resp.ok
        except Exception as exc:
            logger.warning(
                "[kisvideo-live] delete_live_stream for %s raised: %s",
                redact_stream_ref(provider_stream_id), exc, exc_info=True,
            )
            return False

    def sync_targets(self, provider_stream_id: str, targets: Any) -> bool:
        """Pushes the current simulcast target list (ChannelLiveStreamTarget
        rows) to kisvideo-live, which diffs it against its own running
        per-target ffmpeg relay processes and starts/stops them to match.
        Called after any ChannelLiveStreamTarget CRUD - see
        ChannelLiveStreamTargetsView/ChannelLiveStreamTargetDetailView."""
        if not self.base_url or not self.internal_token:
            logger.warning("[kisvideo-live] sync_targets skipped — service not configured")
            return False
        payload = {
            "targets": [
                {
                    "platform": t.platform,
                    "rtmp_url": t.rtmp_url,
                    "stream_key": t.stream_key,
                }
                for t in targets
            ]
        }
        try:
            logger.info(
                "[kisvideo-live] PUT %s/streams/%s/targets platforms=%s",
                self.base_url, redact_stream_ref(provider_stream_id),
                [t.platform for t in targets],
            )
            resp = _requests.put(
                f"{self.base_url}/streams/{provider_stream_id}/targets",
                json=payload,
                headers=self._headers(),
                timeout=10,
            )
            if not resp.ok:
                logger.warning(
                    "[kisvideo-live] sync_targets for %s returned %s: %s",
                    redact_stream_ref(provider_stream_id), resp.status_code, resp.text[:300],
                )
            return resp.ok
        except Exception as exc:
            logger.warning(
                "[kisvideo-live] sync_targets for %s raised: %s",
                redact_stream_ref(provider_stream_id), exc, exc_info=True,
            )
            return False


def get_live_stream_provider(provider_name: str) -> Optional[Any]:
    name = str(provider_name or "").strip().lower()
    if name == "kisvideo":
        return KisVideoLiveProvider()
    return None
