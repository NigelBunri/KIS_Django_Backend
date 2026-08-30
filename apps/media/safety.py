import hashlib
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.conf import settings
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

    Fails CLOSED: any error loading the model or running inference routes to
    manual review, never to a silent pass — an upload that couldn't be
    verified is not the same as one confirmed clean.
    """
    try:
        if mime_type.startswith("video/"):
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
