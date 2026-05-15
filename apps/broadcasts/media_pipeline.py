from __future__ import annotations

from copy import deepcopy
from typing import Any

from django.conf import settings
from rest_framework.exceptions import ValidationError

from apps.media.safety import USER_SAFE_REVIEW_MESSAGE, attachment_requires_safety_review


PIPELINE_VERSION = "2026-05-14.phase08"
READY_STATUSES = {"ready", "processed", "available", "not_configured", ""}
BLOCKED_STATUSES = {"blocked", "failed", "quarantined", "pending_review", "review", "unsafe"}
PROCESSING_STATUSES = {"queued", "processing", "transcoding", "scanning", "uploaded"}
VIDEO_ASSET_TYPES = {"video", "short_video", "live_stream", "replay"}
CAPTION_KEYS = {"captions", "caption_tracks", "captionTracks", "transcript_segments", "transcriptSegments"}


def live_provider_calls_enabled() -> bool:
    return bool(getattr(settings, "KIS_CHANNEL_MEDIA_LIVE_PROVIDER_CALLS_ENABLED", False))


def configured_media_provider() -> str:
    return str(getattr(settings, "KIS_CHANNEL_MEDIA_PROVIDER", "local_stub") or "local_stub").strip() or "local_stub"


def configured_transcode_provider() -> str:
    return str(getattr(settings, "KIS_CHANNEL_TRANSCODE_PROVIDER", configured_media_provider()) or "local_stub").strip() or "local_stub"


def normalize_caption_payload(raw: Any) -> list[dict[str, Any]]:
    if not raw:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        return [{"kind": "plain_text", "language": "", "text": text[:20000]}] if text else []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("href") or "").strip()
        text = str(item.get("text") or item.get("body") or "").strip()
        language = str(item.get("language") or item.get("lang") or "").strip()[:16]
        kind = str(item.get("kind") or item.get("type") or ("url" if url else "plain_text")).strip()[:32]
        if not url and not text:
            continue
        rows.append(
            {
                "kind": kind or "plain_text",
                "language": language,
                "label": str(item.get("label") or "").strip()[:80],
                "url": url,
                "text": text[:20000],
            }
        )
    return rows


def normalize_transcript_segments(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        try:
            start = max(0, int(float(item.get("start_seconds") or item.get("start") or 0)))
            end = max(start, int(float(item.get("end_seconds") or item.get("end") or start)))
        except (TypeError, ValueError):
            start = 0
            end = 0
        rows.append({"text": text[:1000], "start_seconds": start, "end_seconds": end})
    return rows


def _asset_type(payload: dict[str, Any]) -> str:
    return str(payload.get("asset_type") or payload.get("media_type") or payload.get("kind") or "document").strip().lower()


def _processing_status(payload: dict[str, Any]) -> str:
    return str(payload.get("processing_status") or payload.get("processingStatus") or "ready").strip().lower()[:24]


def _safe_asset_dict(asset_or_payload: Any) -> dict[str, Any]:
    if isinstance(asset_or_payload, dict):
        return deepcopy(asset_or_payload)
    metadata = getattr(asset_or_payload, "metadata", None)
    return {
        "asset_type": getattr(asset_or_payload, "asset_type", ""),
        "url": getattr(asset_or_payload, "url", ""),
        "storage_path": getattr(asset_or_payload, "storage_path", ""),
        "mime_type": getattr(asset_or_payload, "mime_type", ""),
        "duration_seconds": getattr(asset_or_payload, "duration_seconds", None),
        "thumbnail_url": getattr(asset_or_payload, "thumbnail_url", ""),
        "processing_status": getattr(asset_or_payload, "processing_status", ""),
        "metadata": metadata if isinstance(metadata, dict) else {},
    }


def build_asset_pipeline_metadata(payload: dict[str, Any], *, content_type: str = "") -> dict[str, Any]:
    asset_type = _asset_type(payload)
    metadata = deepcopy(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {})
    captions = normalize_caption_payload(
        metadata.get("captions")
        or metadata.get("caption_tracks")
        or payload.get("captions")
        or payload.get("caption_tracks")
    )
    transcript_segments = normalize_transcript_segments(
        metadata.get("transcript_segments") or payload.get("transcript_segments")
    )
    processing_status = _processing_status(payload)
    metadata["pipeline"] = {
        **(metadata.get("pipeline") if isinstance(metadata.get("pipeline"), dict) else {}),
        "version": PIPELINE_VERSION,
        "provider": configured_media_provider(),
        "transcode_provider": configured_transcode_provider(),
        "live_provider_calls_enabled": live_provider_calls_enabled(),
        "asset_type": asset_type,
        "content_type": str(content_type or asset_type or "").strip().lower(),
        "processing_status": processing_status or "ready",
        "needs_transcoding": asset_type in VIDEO_ASSET_TYPES or asset_type == "audio",
        "derivatives": {
            "thumbnail_url": str(payload.get("thumbnail_url") or metadata.get("thumbnail_url") or "").strip(),
            "captions_ready": bool(captions),
            "transcript_ready": bool(transcript_segments),
        },
        "provider_reference": str(metadata.get("provider_reference") or metadata.get("provider_ref") or "").strip()[:160],
    }
    if captions:
        metadata["captions"] = captions
    if transcript_segments:
        metadata["transcript_segments"] = transcript_segments
    return metadata


def prepare_channel_asset_payload(payload: dict[str, Any], *, content_type: str = "") -> dict[str, Any]:
    next_payload = dict(payload)
    next_payload["processing_status"] = _processing_status(next_payload) or "ready"
    next_payload["metadata"] = build_asset_pipeline_metadata(next_payload, content_type=content_type)
    return next_payload


def validate_asset_ready_for_publish(asset_or_payload: Any) -> None:
    payload = _safe_asset_dict(asset_or_payload)
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    pipeline = metadata.get("pipeline") if isinstance(metadata.get("pipeline"), dict) else {}
    status = (
        str(payload.get("scan_status") or payload.get("scanStatus") or "").strip().lower()
        or str(payload.get("processing_status") or payload.get("processingStatus") or "").strip().lower()
        or str(pipeline.get("processing_status") or "").strip().lower()
    )
    if attachment_requires_safety_review(payload) or attachment_requires_safety_review(metadata):
        raise ValidationError({"attachments": USER_SAFE_REVIEW_MESSAGE})
    if status in BLOCKED_STATUSES:
        raise ValidationError({"attachments": USER_SAFE_REVIEW_MESSAGE})
    if status in PROCESSING_STATUSES:
        raise ValidationError({"attachments": "This media is still processing. Please wait before publishing or broadcasting."})


def validate_channel_content_ready_for_publish(content) -> None:
    content_metadata = getattr(content, "metadata", None)
    if isinstance(content_metadata, dict) and attachment_requires_safety_review(content_metadata):
        raise ValidationError({"attachments": USER_SAFE_REVIEW_MESSAGE})
    assets = list(getattr(content, "assets", []).all()) if getattr(content, "pk", None) else []
    for asset in assets:
        validate_asset_ready_for_publish(asset)


def validate_feed_entry_ready_for_broadcast(entry: dict[str, Any]) -> None:
    if not isinstance(entry, dict):
        return
    raw_assets = []
    if isinstance(entry.get("attachment"), dict):
        raw_assets.append(entry["attachment"])
    if isinstance(entry.get("attachments"), list):
        raw_assets.extend(item for item in entry["attachments"] if isinstance(item, dict))
    for asset in raw_assets:
        validate_asset_ready_for_publish(asset)
