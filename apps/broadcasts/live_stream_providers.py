"""
Live-streaming provider adapters.

Set LIVE_STREAM_PROVIDER=mux (plus MUX_TOKEN_ID, MUX_TOKEN_SECRET, MUX_WEBHOOK_SECRET)
to enable real Mux integration.  All other values keep the disabled/dev-URL behaviour.
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


def get_live_stream_provider(provider_name: str) -> Optional[MuxProvider]:
    name = str(provider_name or "").strip().lower()
    if name == "mux":
        return MuxProvider()
    return None
