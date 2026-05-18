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
    if getattr(upload, "size", 0) and int(upload.size) > max_bytes:
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
    if provider == "stub":
        # Stub provider: accept without quarantine but flag as unscanned.
        return MediaSafetyDecision(
            status="not_scanned",
            quarantine=False,
            provider="stub",
            reason="stub_provider_no_scanning",
            user_message="Upload accepted.",
            requires_review=False,
        )
    if explicit_scan_required():
        return MediaSafetyDecision(
            status="pending_review",
            quarantine=True,
            provider=provider,
            reason="explicit_scan_provider_not_configured",
            user_message=USER_SAFE_REVIEW_MESSAGE,
            requires_review=True,
        )
    return MediaSafetyDecision(
        status="not_configured",
        quarantine=False,
        provider=provider,
        reason="explicit_scan_not_required",
        user_message="Upload accepted.",
        requires_review=False,
    )


def scan_upload_for_explicit_content(*, filename: str, mime_type: str, context: str) -> MediaSafetyDecision:
    provider = configured_provider()
    if live_provider_calls_enabled():
        # Phase 02 deliberately keeps live calls disabled by default. Provider
        # implementations should return the same MediaSafetyDecision shape.
        return MediaSafetyDecision(
            status="pending_review",
            quarantine=True,
            provider=provider,
            reason=f"{provider}_adapter_not_implemented",
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
