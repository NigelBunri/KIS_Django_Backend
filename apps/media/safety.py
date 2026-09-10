import hashlib
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.files.storage import default_storage
from rest_framework.exceptions import ValidationError


EXPLICIT_CONTENT_POLICY_VERSION = "kis-christian-safety-v1"

SAFE_UPLOAD_CONTEXTS = {
    "chat",
    "dm",
    "group",
    "partner",
    "broadcast",
    "channel",
    "feed",
    "comment",
    "profile",
    "commerce",
    "shop",
    "education",
    "health",
    "verification",
    "status",
    "bible",
    "general",
}

DEFAULT_BLOCKED_EXTENSIONS = {
    ".apk",
    ".app",
    ".bat",
    ".bin",
    ".cmd",
    ".com",
    ".dll",
    ".dmg",
    ".exe",
    ".html",
    ".js",
    ".mjs",
    ".msi",
    ".php",
    ".ps1",
    ".scr",
    ".sh",
    ".svg",
    ".vbs",
}

DEFAULT_ALLOWED_MIME_PREFIXES = ("image/", "video/", "audio/", "text/")
DEFAULT_ALLOWED_MIME_TYPES = {
    "application/json",
    "application/pdf",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/zip",
}
DEFAULT_ALLOWED_EXTENSIONS = {
    ".aac",
    ".csv",
    ".doc",
    ".docx",
    ".gif",
    ".jpeg",
    ".jpg",
    ".json",
    ".m4a",
    ".mov",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".txt",
    ".wav",
    ".webm",
    ".xlsx",
    ".zip",
}

MIME_EXTENSION_PREFIXES = {
    "image/": {".gif", ".jpeg", ".jpg", ".png", ".webp"},
    "video/": {".m4v", ".mov", ".mp4", ".webm"},
    "audio/": {".aac", ".m4a", ".mp3", ".ogg", ".wav", ".webm"},
    "text/": {".csv", ".txt"},
}

MIME_EXTENSION_TYPES = {
    "application/json": {".json"},
    "application/pdf": {".pdf"},
    "application/msword": {".doc"},
    "application/vnd.ms-excel": {".xls"},
    "application/vnd.ms-powerpoint": {".ppt"},
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": {".pptx"},
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {".xlsx"},
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {".docx"},
    "application/zip": {".zip"},
}

USER_SAFE_BLOCK_MESSAGE = (
    "This upload cannot be accepted on KIS. KIS is a Christian, family-safe "
    "platform and does not allow pornographic, sexually explicit, exploitative, "
    "or unsafe media anywhere."
)

USER_SAFE_REVIEW_MESSAGE = (
    "Your upload is being checked for KIS family-safety standards before it is "
    "made visible."
)

MESSAGING_UPLOAD_CONTEXTS = {"chat", "dm", "group", "partner", "status"}


@dataclass(frozen=True)
class MediaSafetyDecision:
    status: str
    quarantine: bool
    provider: str
    reason: str
    user_message: str
    requires_review: bool
    policy_version: str = EXPLICIT_CONTENT_POLICY_VERSION
    score: float | None = None

    def as_metadata(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "quarantine": self.quarantine,
            "provider": self.provider,
            "reason": self.reason,
            "requires_review": self.requires_review,
            "policy_version": self.policy_version,
            "score": self.score,
        }


def _csv_set(value: str | None) -> set[str]:
    return {item.strip().lower() for item in str(value or "").split(",") if item.strip()}


def configured_blocked_extensions() -> set[str]:
    configured = _csv_set(getattr(settings, "MEDIA_SAFETY_BLOCKED_EXTENSIONS", ""))
    return configured or DEFAULT_BLOCKED_EXTENSIONS


def configured_allowed_mime_types() -> set[str]:
    configured = _csv_set(getattr(settings, "MEDIA_SAFETY_ALLOWED_MIME_TYPES", ""))
    return configured or DEFAULT_ALLOWED_MIME_TYPES


def configured_allowed_mime_prefixes() -> tuple[str, ...]:
    configured = _csv_set(getattr(settings, "MEDIA_SAFETY_ALLOWED_MIME_PREFIXES", ""))
    return tuple(configured) if configured else DEFAULT_ALLOWED_MIME_PREFIXES


def configured_allowed_extensions() -> set[str]:
    configured = _csv_set(getattr(settings, "MEDIA_SAFETY_ALLOWED_EXTENSIONS", ""))
    return configured or DEFAULT_ALLOWED_EXTENSIONS


def media_safety_enabled() -> bool:
    return str(getattr(settings, "MEDIA_SAFETY_ENABLED", "1")).strip().lower() in {"1", "true", "yes", "on"}


def explicit_scan_required() -> bool:
    return str(getattr(settings, "MEDIA_EXPLICIT_SCAN_REQUIRED", "1")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def live_provider_calls_enabled() -> bool:
    return str(getattr(settings, "MEDIA_SAFETY_LIVE_PROVIDER_CALLS_ENABLED", "0")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def configured_provider() -> str:
    provider = str(getattr(settings, "MEDIA_SAFETY_PROVIDER", "stub")).strip().lower()
    return provider or "stub"


def normalize_upload_context(value: str | None) -> str:
    normalized = str(value or "general").strip().lower().replace("-", "_")
    return normalized if normalized in SAFE_UPLOAD_CONTEXTS else "general"


def guess_mime_from_name(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or ""


def validate_upload_file_safety(upload, *, context: str = "general") -> None:
    filename = str(getattr(upload, "name", "") or "upload")
    ext = Path(filename).suffix.lower()
    content_type = str(getattr(upload, "content_type", "") or guess_mime_from_name(filename)).lower()
    max_bytes = int(getattr(settings, "MEDIA_SAFETY_MAX_UPLOAD_BYTES", 50 * 1024 * 1024))

    if ext in configured_blocked_extensions():
        raise ValidationError({"detail": "This file type is not allowed on KIS."})
    if ext and ext not in configured_allowed_extensions():
        raise ValidationError({"detail": "This file extension is not allowed on KIS."})
    if not getattr(upload, "size", 0):
        raise ValidationError({"detail": "This file is empty."})
    if int(upload.size) > max_bytes:
        raise ValidationError({"detail": "File too large."})
    if not content_type:
        raise ValidationError({"detail": "Unable to identify the upload MIME type."})
    if content_type == "application/octet-stream":
        raise ValidationError({"detail": "This generic MIME type is not allowed on KIS."})
    if content_type not in configured_allowed_mime_types() and not content_type.startswith(configured_allowed_mime_prefixes()):
        raise ValidationError({"detail": "This MIME type is not allowed on KIS."})
    allowed_for_mime = set(MIME_EXTENSION_TYPES.get(content_type, set()))
    for prefix, extensions in MIME_EXTENSION_PREFIXES.items():
        if content_type.startswith(prefix):
            allowed_for_mime.update(extensions)
    if ext and allowed_for_mime and ext not in allowed_for_mime:
        raise ValidationError({"detail": "The file extension does not match the MIME type."})


def hash_upload(upload) -> str:
    hasher = hashlib.sha256()
    current_position = None
    try:
        current_position = upload.tell()
    except Exception:
        current_position = None
    try:
        for chunk in upload.chunks():
            hasher.update(chunk)
    finally:
        try:
            upload.seek(current_position or 0)
        except Exception:
            pass
    return hasher.hexdigest()


def _run_stub_explicit_content_scan(*, filename: str, mime_type: str, context: str) -> MediaSafetyDecision:
    """
    Provider-neutral placeholder.

    When provider='stub' (no live scanning configured), we accept uploads
    but mark them as not-scanned so admins can audit. Set MEDIA_SAFETY_PROVIDER
    to a real provider (e.g. 'aws_rekognition') and enable
    MEDIA_SAFETY_LIVE_PROVIDER_CALLS_ENABLED=1 for actual content scanning.
    """
    if not media_safety_enabled():
        return MediaSafetyDecision(
            status="not_configured",
            quarantine=False,
            provider="disabled",
            reason="media_safety_disabled",
            user_message="Upload accepted.",
            requires_review=False,
        )
    provider = configured_provider()
    if explicit_scan_required():
        return MediaSafetyDecision(
            status="pending_review",
            quarantine=True,
            provider=provider,
            reason="explicit_scan_provider_not_configured",
            user_message=USER_SAFE_REVIEW_MESSAGE,
            requires_review=True,
        )
    if provider == "stub":
        # Stub provider: accept locally when scanning is not required, but mark
        # the scan provider as not configured so launch checks remain honest.
        return MediaSafetyDecision(
            status="not_configured",
            quarantine=False,
            provider="stub",
            reason="stub_provider_no_scanning",
            user_message="Upload accepted.",
            requires_review=False,
        )
    return MediaSafetyDecision(
        status="not_configured",
        quarantine=False,
        provider=provider,
        reason="explicit_scan_not_required",
        user_message="Upload accepted.",
        requires_review=False,
    )


# NudeNet detector labels considered disqualifying on this platform. NudeNet
# also emits non-explicit anatomical labels (e.g. FACE_FEMALE, ARMPITS_EXPOSED)
# that must never trigger a flag on their own — only these.
NUDENET_EXPLICIT_LABELS = {
    "FEMALE_GENITALIA_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
    "FEMALE_BREAST_EXPOSED",
    "BUTTOCKS_EXPOSED",
    "ANUS_EXPOSED",
}

# Below this confidence, a detection is too uncertain to auto-block — routed
# to manual review instead of either silently passing or auto-suspending
# someone on a marginal call.
NUDENET_AUTO_BLOCK_THRESHOLD = 0.75

# Distinct from every other "pending_review" reason (nudenet_low_confidence,
# nudenet_scan_error, nudenet_no_file_path) — this one specifically marks a
# MediaSafetyScan row as "a video async-scan task is enqueued and hasn't
# resolved it yet", which is what apps.media.tasks.scan_video_and_resolve_task
# checks before doing any work, so a redelivered/duplicate task run can never
# re-apply a result over one that already resolved (or double-apply the same
# one twice).
NUDENET_SCAN_QUEUED_REASON = "nudenet_scan_queued"


def content_safety_error_decision(exc: Exception) -> MediaSafetyDecision:
    """Same fail-closed shape run_nudenet_scan_on_file already uses on any
    scan exception — factored out so the synchronous image-scan call sites
    (which call ContentSafetyProvider directly, not through
    run_nudenet_scan_on_file, since they already have an open file handle
    in hand) get the identical shape on a service-call failure."""
    return MediaSafetyDecision(
        status="pending_review",
        quarantine=True,
        provider="nudenet",
        reason=f"nudenet_scan_error:{type(exc).__name__}",
        user_message=USER_SAFE_REVIEW_MESSAGE,
        requires_review=True,
    )


def queued_for_async_scan_decision() -> MediaSafetyDecision:
    """The placeholder decision every video-scanning call site returns
    immediately, before the real async scan resolves — same shape
    (pending_review/quarantine=True/requires_review=True) these call sites
    already returned before this fix existed (they used to return this
    permanently; now it's genuinely temporary)."""
    return MediaSafetyDecision(
        status="pending_review",
        quarantine=True,
        provider="nudenet",
        reason=NUDENET_SCAN_QUEUED_REASON,
        user_message=USER_SAFE_REVIEW_MESSAGE,
        requires_review=True,
    )

_nudenet_detector = None


def _get_nudenet_detector():
    """Lazily loads the NudeDetector model once per process (Celery worker),
    not once per scan — model load is the expensive part. Import is deferred
    so nothing outside a real scan call ever needs the nudenet/onnxruntime
    dependency installed (e.g. this module is imported by request-path code
    that never scans anything itself)."""
    global _nudenet_detector
    if _nudenet_detector is None:
        from nudenet import NudeDetector  # type: ignore[import-not-found]

        _nudenet_detector = NudeDetector()
    return _nudenet_detector


def _highest_explicit_detection(detections: list[dict]) -> tuple[str | None, float]:
    best_label: str | None = None
    best_score = 0.0
    for det in detections:
        label = str(det.get("class") or det.get("label") or "")
        if label not in NUDENET_EXPLICIT_LABELS:
            continue
        score = float(det.get("score") or 0.0)
        if score > best_score:
            best_label, best_score = label, score
    return best_label, best_score


def _scan_image_file(path: str) -> tuple[str | None, float]:
    detector = _get_nudenet_detector()
    detections = detector.detect(path)
    return _highest_explicit_detection(detections)


def _scan_video_file(path: str, *, sample_count: int = 5) -> tuple[str | None, float]:
    """Samples frames across the video's duration rather than scanning every
    frame — NudeNet inference per-frame is too slow to run on a full video,
    and a handful of evenly-spaced samples is the standard tradeoff this kind
    of screening uses in practice. Any single sampled frame tripping the
    threshold flags the whole video."""
    import subprocess
    import tempfile

    from apps.broadcasts.views import _probe_video_duration  # local import: only needed for video scans

    duration = _probe_video_duration(path)
    if duration <= 0:
        duration = 1.0

    best_label: str | None = None
    best_score = 0.0
    with tempfile.TemporaryDirectory() as tmp_dir:
        for i in range(sample_count):
            timestamp = duration * (i + 1) / (sample_count + 1)
            frame_path = os.path.join(tmp_dir, f"frame_{i}.jpg")
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-ss", str(timestamp), "-i", path, "-frames:v", "1", frame_path],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                )
            except Exception:
                continue
            if not os.path.exists(frame_path):
                continue
            label, score = _scan_image_file(frame_path)
            if score > best_score:
                best_label, best_score = label, score
    return best_label, best_score


def run_nudenet_scan_on_file(file_path: str, mime_type: str) -> MediaSafetyDecision:
    """Real, self-hosted explicit-content scan — no external API, no per-call
    cost, model weights and inference run entirely on our own infrastructure.
    Only called when MEDIA_SAFETY_PROVIDER=nudenet and
    MEDIA_SAFETY_LIVE_PROVIDER_CALLS_ENABLED=1, and only ever given a file
    that already passed the metadata-only checks in validate_upload_file_safety
    (extension/size/declared-type) — this is the layer that actually looks at
    pixels.

    When MEDIA_SAFETY_SERVICE_ENABLED is on, the actual detection runs in the
    kis-content-safety service instead of in-process — everything below this
    point (the confidence-threshold decision, status/quarantine/reason
    mapping) is unchanged either way; only where the raw (label, score) comes
    from differs. Off by default, so this function's behavior is identical
    to before that service existed unless explicitly turned on.

    Fails CLOSED: any error loading the model, calling the service, or
    running inference routes to manual review, never to a silent pass — an
    upload that couldn't be verified is not the same as one confirmed clean.
    """
    try:
        from .content_safety_provider import ContentSafetyProvider, content_safety_service_enabled

        if content_safety_service_enabled():
            with open(file_path, "rb") as fh:
                filename = os.path.basename(file_path)
                label, score = ContentSafetyProvider().scan(fh, filename=filename, content_type=mime_type)
        elif mime_type.startswith("video/"):
            label, score = _scan_video_file(file_path)
        else:
            label, score = _scan_image_file(file_path)
    except Exception as exc:
        return MediaSafetyDecision(
            status="pending_review",
            quarantine=True,
            provider="nudenet",
            reason=f"nudenet_scan_error:{type(exc).__name__}",
            user_message=USER_SAFE_REVIEW_MESSAGE,
            requires_review=True,
        )

    return build_nudenet_decision(label, score)


def build_nudenet_decision(label: str | None, score: float) -> MediaSafetyDecision:
    """The confidence-threshold policy layer, factored out of
    run_nudenet_scan_on_file so the async video-scan task (apps/media/
    tasks.py) can apply the exact same threshold/status mapping to a
    (label, score) it obtained from the content-safety service directly,
    without duplicating this ladder."""
    if label and score >= NUDENET_AUTO_BLOCK_THRESHOLD:
        return MediaSafetyDecision(
            status="blocked",
            quarantine=True,
            provider="nudenet",
            reason=f"nudenet_explicit:{label}",
            user_message=USER_SAFE_BLOCK_MESSAGE,
            requires_review=False,
            score=score,
        )
    if label:
        # Detected but below the auto-block confidence bar — hold for a
        # human to decide rather than guessing either direction.
        return MediaSafetyDecision(
            status="pending_review",
            quarantine=True,
            provider="nudenet",
            reason=f"nudenet_low_confidence:{label}",
            user_message=USER_SAFE_REVIEW_MESSAGE,
            requires_review=True,
            score=score,
        )
    return MediaSafetyDecision(
        status="passed",
        quarantine=False,
        provider="nudenet",
        reason="nudenet_clean",
        user_message="Upload accepted.",
        requires_review=False,
        score=score,
    )


def scan_saved_upload_for_explicit_content(
    *, storage_path: str, filename: str, mime_type: str, context: str,
) -> MediaSafetyDecision:
    """Single entry point for every call site that has an already-saved
    (default_storage-relative) file to scan — the shared routing logic
    behind the fix for the 4 call sites that used to call
    scan_upload_for_explicit_content(file_path=None) and never got a real
    verdict. With MEDIA_SAFETY_SERVICE_ENABLED off, behaves exactly like
    the old call (metadata-only, routes to scan_upload_for_explicit_content
    with no file_path — unchanged production behavior). With it on:
    images get a real synchronous scan via the content-safety service;
    videos get queued_for_async_scan_decision() instead — the caller is
    responsible for enqueueing apps.media.tasks.scan_video_and_resolve_task
    once it has a target row id to resolve into (see that task's docstring
    for the resolution_target/resolution_id contract)."""
    from .content_safety_provider import ContentSafetyProvider, ContentSafetyProviderError, content_safety_service_enabled

    if not content_safety_service_enabled():
        return scan_upload_for_explicit_content(filename=filename, mime_type=mime_type, context=context)

    if mime_type.startswith("video/"):
        return queued_for_async_scan_decision()

    try:
        with default_storage.open(storage_path, "rb") as fh:
            label, score = ContentSafetyProvider().scan(fh, filename=filename, content_type=mime_type)
    except ContentSafetyProviderError as exc:
        return content_safety_error_decision(exc)
    return build_nudenet_decision(label, score)


def scan_upload_for_explicit_content(
    *, filename: str, mime_type: str, context: str, file_path: str | None = None,
) -> MediaSafetyDecision:
    provider = configured_provider()
    if live_provider_calls_enabled():
        if provider == "nudenet" and file_path:
            return run_nudenet_scan_on_file(file_path, mime_type)
        # Any other configured provider without an adapter implemented yet,
        # or nudenet called without a file (metadata-only caller) — route to
        # manual review rather than fabricating a pass/fail with no evidence.
        return MediaSafetyDecision(
            status="pending_review",
            quarantine=True,
            provider=provider,
            reason=f"{provider}_adapter_not_implemented" if provider != "nudenet" else "nudenet_no_file_path",
            user_message=USER_SAFE_REVIEW_MESSAGE,
            requires_review=True,
        )
    return _run_stub_explicit_content_scan(filename=filename, mime_type=mime_type, context=context)


def user_safe_upload_response(decision: MediaSafetyDecision) -> dict[str, Any]:
    return {
        "status": decision.status,
        "quarantined": decision.quarantine,
        "requiresReview": decision.requires_review,
        "message": decision.user_message,
        "policyVersion": decision.policy_version,
    }


def attachment_requires_safety_review(attachment: Any) -> bool:
    if not isinstance(attachment, dict):
        return False
    safety = attachment.get("safety") if isinstance(attachment.get("safety"), dict) else {}
    status = str(
        attachment.get("scanStatus")
        or attachment.get("scan_status")
        or safety.get("status")
        or ""
    ).strip().lower()
    return bool(
        attachment.get("quarantined")
        or attachment.get("requiresReview")
        or attachment.get("requires_review")
        or safety.get("quarantined")
        or safety.get("requiresReview")
        or status in {"pending_review", "blocked", "failed"}
    )


def validate_attachment_metadata_for_safe_messaging(attachments: Any) -> None:
    if not attachments:
        return
    if not isinstance(attachments, list):
        raise ValidationError({"attachments": "Invalid attachment payload."})
    for attachment in attachments:
        if attachment_requires_safety_review(attachment):
            raise ValidationError({"attachments": USER_SAFE_REVIEW_MESSAGE})


# Community.avatar_url / Partner.avatar_url / Partner.logo_url are plain
# client-writable URLField's with no backing MediaUploadIntent/MediaAsset
# at all - found during an AI-moderation coverage audit as a structural
# bypass distinct from every other gap that audit closed: there is no
# owned file for the platform to have forgotten to scan, only a client-
# supplied string. Fetching an arbitrary client-supplied URL server-side
# is an SSRF surface, so this enforces real guards (https only, DNS-
# resolved IP must not be private/loopback/link-local/reserved, capped
# response size, capped timeout, image content-types only) before ever
# handing the bytes to the scanner - never trust a URL merely because it
# looks well-formed.
_EXTERNAL_IMAGE_FETCH_TIMEOUT_SECONDS = 10
_EXTERNAL_IMAGE_FETCH_MAX_BYTES = 15 * 1024 * 1024


def _hostname_resolves_only_to_public_addresses(hostname: str) -> bool:
    import ipaddress
    import socket

    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    for info in infos:
        raw_ip = info[4][0]
        try:
            ip = ipaddress.ip_address(raw_ip.split("%")[0])
        except ValueError:
            return False
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_multicast or ip.is_reserved or ip.is_unspecified
        ):
            return False
    return bool(infos)


def fetch_and_scan_external_image_url(url: str) -> "MediaSafetyDecision":
    """Real network fetch + explicit-content scan of a client-supplied
    image URL, with SSRF guards. Returns a fail-closed MediaSafetyDecision
    (pending_review) for anything that can't be safely fetched/verified -
    a URL server owners must reject on principle (private IP, wrong
    scheme, oversized, wrong content-type, fetch failure) is exactly as
    unverifiable as a real content-safety-provider outage, and gets the
    same treatment."""
    import requests as _requests
    from urllib.parse import urlparse

    from .content_safety_provider import content_safety_service_enabled

    def _closed(reason: str) -> "MediaSafetyDecision":
        return MediaSafetyDecision(
            status="pending_review", quarantine=True, provider="url_fetch",
            reason=reason, user_message=USER_SAFE_REVIEW_MESSAGE, requires_review=True,
        )

    parsed = urlparse(str(url or "").strip())
    if parsed.scheme != "https" or not parsed.hostname:
        return _closed("external_url_scheme_rejected")
    if not _hostname_resolves_only_to_public_addresses(parsed.hostname):
        return _closed("external_url_private_address_rejected")

    try:
        resp = _requests.get(
            url, timeout=_EXTERNAL_IMAGE_FETCH_TIMEOUT_SECONDS, stream=True,
            headers={"User-Agent": "KIS-ContentSafety/1.0"},
        )
    except Exception:
        return _closed("external_url_fetch_failed")

    try:
        if not resp.ok:
            return _closed("external_url_fetch_failed")
        content_type = str(resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if not content_type.startswith("image/"):
            return _closed("external_url_not_an_image")

        body = bytearray()
        for chunk in resp.iter_content(chunk_size=65536):
            body.extend(chunk)
            if len(body) > _EXTERNAL_IMAGE_FETCH_MAX_BYTES:
                return _closed("external_url_too_large")
    finally:
        resp.close()

    filename = (parsed.path.rsplit("/", 1)[-1] or "image")[:255]
    if not content_safety_service_enabled():
        return scan_upload_for_explicit_content(filename=filename, mime_type=content_type, context="general")

    from .content_safety_provider import ContentSafetyProvider, ContentSafetyProviderError
    import io

    try:
        label, score = ContentSafetyProvider().scan(io.BytesIO(bytes(body)), filename=filename, content_type=content_type)
    except ContentSafetyProviderError as exc:
        return content_safety_error_decision(exc)
    except Exception as exc:
        return content_safety_error_decision(exc)
    return build_nudenet_decision(label, score)


def reject_external_image_url_if_unsafe(url: str) -> str:
    """Validator entry point for Community/Partner avatar_url/logo_url -
    unlike an upload with a quarantine slot to hold ambiguous content in,
    these fields have no separate "pending" storage and are default
    publicly visible, so anything short of a clean pass is rejected
    outright (stricter than the upload-quarantine flows elsewhere) rather
    than saved in an unreviewed state."""
    text = str(url or "").strip()
    if not text or not text.startswith("http"):
        return url

    decision = fetch_and_scan_external_image_url(text)
    if decision.status not in ("passed", "not_configured"):
        raise ValidationError({"detail": decision.user_message})
    return url
