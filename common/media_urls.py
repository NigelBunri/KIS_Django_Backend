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


def strip_backend_origin(value: object, request=None) -> str:
    """Store backend-hosted media as a path so changing API hosts does not stale DB rows."""
    text = _clean_text(value)
    if not should_strip_backend_origin(text, request=request):
        return text
    parsed = urlparse(text)
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
