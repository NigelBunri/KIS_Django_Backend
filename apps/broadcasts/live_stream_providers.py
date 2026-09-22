"""
Live-streaming provider adapters.

Set LIVE_STREAM_PROVIDER=kisvideo (plus KIS_VIDEO_LIVE_SERVICE_URL,
KIS_VIDEO_LIVE_SERVICE_INTERNAL_TOKEN) to use the self-hosted kisvideo-live
RTMP+WHIP ingest service - the intended long-term provider.

Set LIVE_STREAM_PROVIDER=mux (plus MUX_TOKEN_ID, MUX_TOKEN_SECRET, MUX_WEBHOOK_SECRET)
to enable the legacy Mux integration, kept only for migration/rollback.

All other values keep the disabled/dev-URL behaviour.
"""

import hashlib
import hmac
import os
from typing import Any, Dict, Optional

import requests as _requests


class LiveStreamProviderError(Exception):
    pass


class MuxProvider:
    """
    Thin wrapper around the Mux Video Live-Stream API.

    Docs: https://docs.mux.com/api-reference/video#operation/create-live-stream
    """

    _API_BASE = "https://api.mux.com"
    _INGEST_BASE = "rtmps://global-live.mux.com:443/app"

    def __init__(self) -> None:
        self.token_id = os.environ.get("MUX_TOKEN_ID", "")
        self.token_secret = os.environ.get("MUX_TOKEN_SECRET", "")
        self.webhook_secret = os.environ.get("MUX_WEBHOOK_SECRET", "")

    def _auth(self):
        return (self.token_id, self.token_secret)

    def _require_credentials(self) -> None:
        if not self.token_id or not self.token_secret:
            raise LiveStreamProviderError(
                "MUX_TOKEN_ID and MUX_TOKEN_SECRET environment variables must be set."
            )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_live_stream(
        self,
        *,
        reduced_latency: bool = True,
        reconnect_window: int = 30,
    ) -> Dict[str, Any]:
        self._require_credentials()

        payload: Dict[str, Any] = {
            "playback_policy": ["public"],
            "reconnect_window": reconnect_window,
            "new_asset_settings": {"playback_policy": ["public"]},
        }
        if reduced_latency:
            payload["latency_mode"] = "reduced"

        resp = _requests.post(
            f"{self._API_BASE}/video/v1/live-streams",
            json=payload,
            auth=self._auth(),
            timeout=15,
        )
        if not resp.ok:
            raise LiveStreamProviderError(
                f"Mux API returned {resp.status_code}: {resp.text[:500]}"
            )

        data = resp.json().get("data", {})
        stream_id: str = data["id"]
        stream_key: str = data["stream_key"]
        playback_ids = data.get("playback_ids") or []
        playback_id: str = playback_ids[0]["id"] if playback_ids else ""

        return {
            "provider": "mux",
            "provider_stream_id": stream_id,
            "ingest_url": f"{self._INGEST_BASE}/{stream_key}",
            "playback_url": f"https://stream.mux.com/{playback_id}.m3u8" if playback_id else "",
            "stream_key": stream_key,
            "playback_id": playback_id,
            "raw": data,
        }

    def delete_live_stream(self, provider_stream_id: str) -> bool:
        if not self.token_id or not self.token_secret:
            return False
        try:
            resp = _requests.delete(
                f"{self._API_BASE}/video/v1/live-streams/{provider_stream_id}",
                auth=self._auth(),
                timeout=10,
            )
            return resp.ok
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    def verify_webhook_signature(self, raw_body: bytes, mux_signature_header: str) -> bool:
        """
        Validate an incoming Mux webhook using the HMAC-SHA256 signature.
        Header format: "t=<unix_ts>,v1=<hex_signature>"
        Returns True when valid or when MUX_WEBHOOK_SECRET is not configured.
        """
        if not self.webhook_secret:
            return True

        parts: Dict[str, str] = {}
        for segment in mux_signature_header.split(","):
            if "=" in segment:
                k, v = segment.split("=", 1)
                parts[k.strip()] = v.strip()

        timestamp = parts.get("t", "")
        expected_sig = parts.get("v1", "")
        if not timestamp or not expected_sig:
            return False

        signed_payload = f"{timestamp}.".encode() + raw_body
        computed = hmac.new(
            self.webhook_secret.encode("utf-8"),
            signed_payload,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(computed, expected_sig)

    # Map Mux event types to KIS ChannelLiveStream.Status values
    _MUX_STATUS_MAP: Dict[str, str] = {
        "video.live_stream.active":       "live",
        "video.live_stream.recording":    "live",
        "video.live_stream.idle":         "ended",
        "video.live_stream.disconnected": "ended",
        "video.live_stream.deleted":      "cancelled",
        "video.live_stream.connected":    "scheduled",
        "video.live_stream.created":      "scheduled",
    }

    def map_webhook_status(self, event_type: str) -> Optional[str]:
        return self._MUX_STATUS_MAP.get(event_type)

    def extract_webhook_stream_id(self, payload: dict) -> str:
        """Return the provider stream ID from a Mux webhook payload."""
        return str(
            (payload.get("object") or {}).get("id")
            or payload.get("data", {}).get("id")
            or ""
        ).strip()

    def extract_viewer_count(self, payload: dict) -> Optional[int]:
        try:
            return int((payload.get("data") or {}).get("viewer_seconds_sum") or 0)
        except (TypeError, ValueError):
            return None


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

        resp = _requests.post(
            f"{self.base_url}/streams",
            headers=self._headers(),
            timeout=15,
        )
        if not resp.ok:
            raise LiveStreamProviderError(
                f"kisvideo-live API returned {resp.status_code}: {resp.text[:500]}"
            )

        data = resp.json()
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
            return False
        try:
            resp = _requests.delete(
                f"{self.base_url}/streams/{provider_stream_id}",
                headers=self._headers(),
                timeout=10,
            )
            return resp.ok
        except Exception:
            return False

    def sync_targets(self, provider_stream_id: str, targets: Any) -> bool:
        """Pushes the current simulcast target list (ChannelLiveStreamTarget
        rows) to kisvideo-live, which diffs it against its own running
        per-target ffmpeg relay processes and starts/stops them to match.
        Called after any ChannelLiveStreamTarget CRUD - see
        ChannelLiveStreamTargetsView/ChannelLiveStreamTargetDetailView."""
        if not self.base_url or not self.internal_token:
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
            resp = _requests.put(
                f"{self.base_url}/streams/{provider_stream_id}/targets",
                json=payload,
                headers=self._headers(),
                timeout=10,
            )
            return resp.ok
        except Exception:
            return False


def get_live_stream_provider(provider_name: str) -> Optional[Any]:
    name = str(provider_name or "").strip().lower()
    if name == "kisvideo":
        return KisVideoLiveProvider()
    if name == "mux":
        return MuxProvider()
    return None
