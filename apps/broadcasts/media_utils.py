import os
import subprocess
import tempfile
import uuid
from urllib.parse import urljoin, urlparse

from django.conf import settings
from django.core.files.base import File
from django.core.files.storage import FileSystemStorage, default_storage

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
    path = relative_path.replace(os.sep, "/")
    if not isinstance(default_storage, FileSystemStorage):
        # Remote backend (S3/Supabase) — the object doesn't live under this
        # server's MEDIA_URL at all, so hand back storage's own URL (public
        # bucket URL, or a presigned GET for a private bucket) instead of a
        # same-domain /media/... link that nothing actually serves.
        return default_storage.url(path)
    media_url = getattr(settings, "MEDIA_URL", "/media/").rstrip("/")
    return build_absolute_url(request, f"{media_url}/{path}")


def _dhash_frame(image_path: str, hash_size: int = 8) -> str:
    """8x8 difference hash (dHash) of one frame - a real perceptual hash,
    not a byte-exact one. Resizing to a tiny grid and comparing adjacent
    pixel brightness means the result is stable across re-encoding,
    bitrate/resolution changes and minor compression artifacts, which is
    the entire point of a perceptual match (a sha256 of the file bytes
    changes completely the moment a video is re-encoded even slightly)."""
    from PIL import Image

    with Image.open(image_path) as img:
        gray = img.convert("L").resize((hash_size + 1, hash_size))
        pixels = list(gray.getdata())

    bits = []
    for row in range(hash_size):
        row_offset = row * (hash_size + 1)
        for col in range(hash_size):
            bits.append(pixels[row_offset + col] > pixels[row_offset + col + 1])

    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return format(value, f"0{hash_size * hash_size // 4}x")


def compute_video_perceptual_fingerprint(storage_path: str, *, sample_count: int = 5) -> str | None:
    """Samples `sample_count` evenly-spaced frames from the video at
    `storage_path` (a default_storage-relative path) and returns a
    combined fingerprint: sample_count dHashes joined by "-". Returns
    None if the source can't be read or ffmpeg isn't available - this is
    an enhancement layer over the exact-hash fingerprint, not a required
    dependency for uploads to succeed.
    """
    if not default_storage.exists(storage_path):
        return None

    try:
        source_local_path = default_storage.path(storage_path)
    except NotImplementedError:
        source_local_path = None

    with tempfile.TemporaryDirectory() as tmp_dir:
        if source_local_path:
            local_source = source_local_path
        else:
            suffix = os.path.splitext(storage_path)[1] or ".mp4"
            local_source = os.path.join(tmp_dir, f"source{suffix}")
            with default_storage.open(storage_path, "rb") as remote_file, open(local_source, "wb") as fh:
                fh.write(remote_file.read())

        from apps.broadcasts.views import _probe_video_duration
        duration = _probe_video_duration(local_source)
        if duration <= 0:
            return None

        frame_hashes = []
        for i in range(sample_count):
            timestamp = duration * (i + 1) / (sample_count + 1)
            frame_path = os.path.join(tmp_dir, f"fp_frame_{i}.jpg")
            cmd = [
                "ffmpeg", "-y", "-ss", str(timestamp), "-i", local_source,
                "-frames:v", "1", "-vf", "scale=32:32", frame_path,
            ]
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                frame_hashes.append(_dhash_frame(frame_path))
            except Exception:
                return None

    return "-".join(frame_hashes)


def perceptual_fingerprint_similarity(fingerprint_a: str, fingerprint_b: str) -> float:
    """1.0 = identical frames at every sampled position, 0.0 = completely
    different. Compares position-aligned frame hashes (both fingerprints
    sample at the same relative timestamps - i/(N+1) of duration - so
    frame i in one lines up with frame i in the other for genuinely the
    same underlying video) rather than doing an all-pairs comparison.
    """
    frames_a = fingerprint_a.split("-")
    frames_b = fingerprint_b.split("-")
    if not frames_a or not frames_b or len(frames_a) != len(frames_b):
        return 0.0
    try:
        total_bits = 0
        total_distance = 0
        for hex_a, hex_b in zip(frames_a, frames_b):
            bits = len(hex_a) * 4
            total_bits += bits
            total_distance += bin(int(hex_a, 16) ^ int(hex_b, 16)).count("1")
    except ValueError:
        return 0.0
    if total_bits == 0:
        return 0.0
    return max(0.0, 1.0 - (total_distance / total_bits))


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
        if default_storage.exists(cleaned):
            return cleaned

    if not default_storage.exists(video.storage_path):
        return None

    rel_name = os.path.join(THUMBNAIL_SUBDIRECTORY, f"{uuid.uuid4().hex}.jpg").replace(os.sep, "/")

    # ffmpeg needs real files on disk — for a remote backend (S3/Supabase)
    # neither the source video nor the generated thumbnail have a local
    # path, so both are staged through a temp dir and the result is pushed
    # back through default_storage instead of written straight to
    # MEDIA_ROOT (which would silently skip S3 the way this used to).
    try:
        source_local_path = default_storage.path(video.storage_path)
    except NotImplementedError:
        source_local_path = None

    with tempfile.TemporaryDirectory() as tmp_dir:
        if source_local_path:
            local_source = source_local_path
        else:
            suffix = os.path.splitext(video.storage_path)[1] or ".mp4"
            local_source = os.path.join(tmp_dir, f"source{suffix}")
            with default_storage.open(video.storage_path, "rb") as remote_file, open(local_source, "wb") as fh:
                fh.write(remote_file.read())

        local_thumb = os.path.join(tmp_dir, "thumb.jpg")
        if not _create_thumbnail(local_source, local_thumb):
            return None

        with open(local_thumb, "rb") as thumb_fh:
            saved_name = default_storage.save(rel_name, File(thumb_fh, name=os.path.basename(rel_name)))

    video.thumbnail_url = saved_name
    video.save(update_fields=["thumbnail_url"])
    return saved_name
