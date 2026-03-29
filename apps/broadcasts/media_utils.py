import os
import subprocess
import uuid

from django.conf import settings

from apps.broadcasts.models import BroadcastVideo


THUMBNAIL_SUBDIRECTORY = "broadcast_thumbnails"


def build_media_url(request, relative_path: str) -> str:
    media_url = getattr(settings, "MEDIA_URL", "/media/").rstrip("/")
    path = relative_path.replace(os.sep, "/")
    return request.build_absolute_uri(f"{media_url}/{path}")


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
