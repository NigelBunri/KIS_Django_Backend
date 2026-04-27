import os
import subprocess
import uuid
from urllib.parse import urljoin, urlparse

from django.conf import settings

from apps.broadcasts.models import BroadcastVideo
from common.media_urls import absolutize_backend_media, strip_backend_origin


THUMBNAIL_SUBDIRECTORY = "broadcast_thumbnails"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}


def _host_is_loopback(value: str | None) -> bool:
    if not value:
        return False
    return value.strip().lower() in LOOPBACK_HOSTS


def _get_preferred_public_base_url(request) -> str | None:
    configured_base = (
        str(getattr(settings, "API_BASE_URL", "") or "").strip()
        or str(getattr(settings, "SITE_URL", "") or "").strip()
    ).rstrip("/")
    request_base = None
    request_host = None
    if request is not None:
        request_base = request.build_absolute_uri("/").rstrip("/")
        request_host = urlparse(request_base).hostname

    configured_host = urlparse(configured_base).hostname if configured_base else None

    if request_base and not _host_is_loopback(request_host):
        return request_base
    if configured_base and not _host_is_loopback(configured_host):
        return configured_base
    return request_base or configured_base or None


def build_absolute_url(request, value: str) -> str:
    return absolutize_backend_media(value, request=request)


def normalize_media_reference(value: str, request=None) -> str:
    return strip_backend_origin(value, request=request)


def build_media_url(request, relative_path: str) -> str:
    media_url = getattr(settings, "MEDIA_URL", "/media/").rstrip("/")
    path = relative_path.replace(os.sep, "/")
    return build_absolute_url(request, f"{media_url}/{path}")


def _absolute_media_path(relative_path: str) -> str:
    media_root = getattr(settings, "MEDIA_ROOT", "media")
    return os.path.join(media_root, relative_path)


def _create_thumbnail(source_path: str, dest_path: str) -> bool:
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        source_path,
        "-ss",
        "00:00:01",
        "-frames:v",
        "1",
        "-vf",
        "scale=320:-1",
        dest_path,
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return os.path.exists(dest_path)
    except Exception:
        if os.path.exists(dest_path):
            os.remove(dest_path)
        return False


def ensure_local_thumbnail(video: BroadcastVideo) -> str | None:
    rel = (video.thumbnail_url or "").strip()
    if rel and rel.startswith("http"):
        return None
    if rel:
        cleaned = rel.lstrip("/")
        abs_path = _absolute_media_path(cleaned)
        if os.path.exists(abs_path):
            return cleaned
    source_path = _absolute_media_path(video.storage_path)
    if not os.path.exists(source_path):
        return None
    rel_name = os.path.join(THUMBNAIL_SUBDIRECTORY, f"{uuid.uuid4().hex}.jpg")
    abs_target = _absolute_media_path(rel_name)
    os.makedirs(os.path.dirname(abs_target), exist_ok=True)
    success = _create_thumbnail(source_path, abs_target)
    if not success:
        return None
    video.thumbnail_url = rel_name
    video.save(update_fields=["thumbnail_url"])
    return rel_name
