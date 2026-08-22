from __future__ import annotations

import ipaddress
from urllib.parse import urljoin, urlparse, urlunparse

from django.conf import settings


LOCAL_MEDIA_BASE_URLS = {
    "http://10.112.162.99:8000",
}


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _configured_base_urls() -> set[str]:
    urls = set(LOCAL_MEDIA_BASE_URLS)
    for setting_name in ("API_BASE_URL", "SITE_URL"):
        value = _clean_text(getattr(settings, setting_name, ""))
        if value:
            urls.add(value.rstrip("/"))
    return urls


def _host_is_private_or_loopback(host: str | None) -> bool:
    if not host:
        return False
    normalized = host.strip().lower()
    if normalized in {"localhost", "0.0.0.0"}:
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return address.is_private or address.is_loopback


def _request_base_url(request) -> str:
    if request is None:
        return ""
    try:
        return request.build_absolute_uri("/").rstrip("/")
    except Exception:
        return ""


def _configured_public_base_url() -> str:
    configured = _clean_text(getattr(settings, "API_BASE_URL", "")) or _clean_text(getattr(settings, "SITE_URL", ""))
    return configured.rstrip("/")


def should_strip_backend_origin(value: object, request=None) -> bool:
    text = _clean_text(value)
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False

    bases = _configured_base_urls()
    request_base = _request_base_url(request)
    if request_base:
        bases.add(request_base)

    normalized_value_origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    if normalized_value_origin in bases:
        return True

    configured_hosts = {
        urlparse(base).netloc.lower()
        for base in bases
        if urlparse(base).scheme in {"http", "https"} and urlparse(base).netloc
    }
    if parsed.netloc.lower() in configured_hosts:
        return True

    return _host_is_private_or_loopback(parsed.hostname)


def _is_presigned_object_storage_url(parsed) -> bool:
    """S3 (and S3-compatible) presigned GET URLs carry their signature and
    expiry entirely in the query string — that query string alone is
    proof the value was resolved for one temporary read, never something
    safe to persist as if it were the stable object key. Detected by the
    SigV4 marker (X-Amz-Signature) rather than by host, so this catches
    any bucket/region/CNAME rather than needing a host allowlist."""
    query = parsed.query or ""
    return "X-Amz-Signature=" in query


def _presigned_object_key(parsed) -> str:
    """Recovers the raw object key from a presigned S3 URL's path,
    stripping the bucket name if it's present as a path segment.
    boto3 (apps.media.storage_backends) generates path-style URLs —
    https://s3.<region>.amazonaws.com/<bucket>/<key> — where the host is
    the generic S3 endpoint and the bucket is the FIRST path segment,
    unlike virtual-hosted-style URLs (https://<bucket>.s3.<region>.
    amazonaws.com/<key>) where the whole path already IS the key.
    Distinguishing the two only by whether the host itself starts with
    "s3." (path-style) avoids needing to know the actual configured
    bucket name here — this module doesn't otherwise depend on
    apps.media's settings."""
    path = (parsed.path or "").lstrip("/")
    host = (parsed.hostname or "").lower()
    if host.startswith("s3.") and host.endswith("amazonaws.com"):
        # Path-style: first segment is the bucket, the rest is the key.
        _bucket, _sep, key = path.partition("/")
        return key
    return path


def strip_backend_origin(value: object, request=None) -> str:
    """Store backend-hosted media as a path so changing API hosts does not
    stale DB rows.

    Also self-heals a presigned object-storage URL (S3 X-Amz-Signature
    query params) back down to its stable object key. Without this, a
    value resolved for temporary display (e.g.
    EducationInstitutionSerializer.to_representation resolving
    branding.logo_url into a signed URL, or any equivalent
    resolve-for-display step elsewhere) that gets round-tripped back
    through an edit form's unmodified save would otherwise be persisted
    as a frozen, soon-to-expire URL instead of the reusable key it
    started as — confirmed in production for
    EducationInstitution.branding.logo_url/logoUrl (a signed URL over 7
    hours past its 1-hour expiry had been silently written back in,
    which is exactly what stopped the logo from displaying after an
    edit that never touched it)."""
    text = _clean_text(value)
    if not text:
        return text
    parsed = urlparse(text)
    if _is_presigned_object_storage_url(parsed):
        return _presigned_object_key(parsed)
    if not should_strip_backend_origin(text, request=request):
        return text
    path = parsed.path or "/"
    return urlunparse(("", "", path, "", parsed.query, parsed.fragment))


def absolutize_backend_media(value: object, request=None) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    if should_strip_backend_origin(text, request=request):
        text = strip_backend_origin(text, request=request)
    elif text.startswith(("http://", "https://")):
        return text
    if text.startswith("//"):
        return f"https:{text}"

    base = _configured_public_base_url() or _request_base_url(request)
    if not base:
        return text
    return urljoin(f"{base.rstrip('/')}/", text.lstrip("/"))


def normalize_image_payload(value: object, request=None) -> str:
    return strip_backend_origin(value, request=request)
