"""
Shared public-web helpers: settings readers and sanitizers used by any
app that serves genuinely public, unauthenticated content (broadcasts'
channel/content public landing pages, websites' site/page public API).

Extracted from apps.broadcasts.views (where these were originally
defined alongside the broadcasts-specific public-web views) so a second
app (apps.websites) doesn't have to duplicate them. apps.broadcasts.views
re-exports these under their original private names as a thin aliasing
import, so every existing call site and any test that patches
`apps.broadcasts.views._safe_public_media_url` (etc.) keeps working
unchanged.
"""
import os
import re
from urllib.parse import urlparse

from django.conf import settings


def public_web_enabled() -> bool:
    value = getattr(settings, "KIS_PUBLIC_WEB_ENABLED", os.getenv("KIS_PUBLIC_WEB_ENABLED", "True"))
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def public_web_base_url(request=None) -> str:
    configured = str(getattr(settings, "KIS_PUBLIC_WEB_BASE_URL", os.getenv("KIS_PUBLIC_WEB_BASE_URL", "")) or "").strip().rstrip("/")
    if configured:
        return configured
    if request is not None:
        return request.build_absolute_uri("/").rstrip("/")
    return "https://kis.app"


def public_indexing_enabled() -> bool:
    value = getattr(settings, "KIS_PUBLIC_WEB_INDEXING_ENABLED", os.getenv("KIS_PUBLIC_WEB_INDEXING_ENABLED", "False"))
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def safe_public_description(*values) -> str:
    text = " ".join(str(value or "").strip() for value in values if str(value or "").strip())
    text = re.sub(r"\s+", " ", text).strip()
    return text[:260]


def safe_public_media_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        return ""
    path = (parsed.path or "").lower()
    if any(marker in path for marker in ("/private/", "/raw/", "/tmp/", "/temp/")):
        return ""
    return raw
