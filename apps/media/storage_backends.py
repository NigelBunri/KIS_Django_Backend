"""Remote media storage backends for KIS uploads.

The Supabase backend is intentionally small and dependency-light: it uses the
Supabase Storage REST API through requests so Render can persist uploads without
requiring boto3/django-storages. Secrets are read only from environment via
Django settings and are never logged.
"""

from __future__ import annotations

import os
from io import BytesIO
from urllib.parse import quote, urlparse

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile, File
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _normalize_supabase_storage_api_url(value: str) -> str:
    """Accept either Supabase REST or S3 endpoint and use REST internally."""
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return raw
    if raw.endswith("/storage/v1/s3"):
        raw = raw[: -len("/s3")]
    parsed = urlparse(raw)
    if parsed.netloc.endswith(".storage.supabase.co") and parsed.path.rstrip("/") == "/storage/v1":
        project_ref = parsed.netloc.split(".storage.supabase.co", 1)[0]
        return f"https://{project_ref}.supabase.co/storage/v1"
    return raw


@deconstructible
class SupabaseStorage(Storage):
    """Django Storage backend for Supabase Storage buckets.

    Required env:
      SUPABASE_URL=https://<project-ref>.supabase.co
      SUPABASE_SERVICE_ROLE_KEY=<server-only service role key>
      SUPABASE_STORAGE_BUCKET=kis-media

    Optional env:
      SUPABASE_STORAGE_PUBLIC_BUCKET=True
      SUPABASE_STORAGE_API_URL=https://<project-ref>.storage.supabase.co/storage/v1
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        base_url = _env("SUPABASE_STORAGE_API_URL") or f"{_env('SUPABASE_URL').rstrip('/')}/storage/v1"
        self.base_url = _normalize_supabase_storage_api_url(base_url)
        self.bucket = _env("SUPABASE_STORAGE_BUCKET", "kis-media")
        self.service_key = _env("SUPABASE_SERVICE_ROLE_KEY")
        self.public_bucket = _env("SUPABASE_STORAGE_PUBLIC_BUCKET", "false").lower() in {"1", "true", "yes", "on"}
        self.timeout = float(_env("SUPABASE_STORAGE_TIMEOUT_SECONDS", "30") or "30")
        if not self.base_url or not self.bucket or not self.service_key:
            raise ImproperlyConfigured(
                "Supabase storage requires SUPABASE_URL or SUPABASE_STORAGE_API_URL, "
                "SUPABASE_STORAGE_BUCKET, and SUPABASE_SERVICE_ROLE_KEY."
            )

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.service_key}",
            "apikey": self.service_key,
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _object_path(self, name: str) -> str:
        clean = str(name or "").replace("\\", "/").lstrip("/")
        if not clean:
            raise ValueError("Storage object name cannot be empty.")
        return quote(clean, safe="/")

    def _object_url(self, name: str, public: bool = False) -> str:
        segment = "object/public" if public else "object"
        return f"{self.base_url}/{segment}/{quote(self.bucket, safe='')}/{self._object_path(name)}"

    def _save(self, name: str, content) -> str:
        clean_name = self.get_available_name(name)
        content_type = getattr(content, "content_type", None) or "application/octet-stream"
        try:
            content.seek(0)
        except Exception:
            pass
        data = b"".join(content.chunks()) if hasattr(content, "chunks") else content.read()
        response = requests.post(
            self._object_url(clean_name),
            headers={**self._headers(content_type), "x-upsert": "false"},
            data=data,
            timeout=self.timeout,
        )
        if response.status_code == 409:
            clean_name = self.get_alternative_name(clean_name, "")
            response = requests.post(
                self._object_url(clean_name),
                headers={**self._headers(content_type), "x-upsert": "false"},
                data=data,
                timeout=self.timeout,
            )
        if response.status_code >= 400:
            raise IOError(f"Supabase storage upload failed with status {response.status_code}.")
        return clean_name

    def _open(self, name: str, mode: str = "rb") -> File:
        response = requests.get(self._object_url(name), headers=self._headers(), timeout=self.timeout)
        if response.status_code >= 400:
            raise FileNotFoundError(name)
        return File(ContentFile(response.content, name=os.path.basename(name)), name=name)

    def exists(self, name: str) -> bool:
        try:
            response = requests.head(self._object_url(name), headers=self._headers(), timeout=self.timeout)
        except requests.RequestException:
            return False
        if response.status_code == 405:
            try:
                response = requests.get(self._object_url(name), headers=self._headers(), timeout=self.timeout, stream=True)
            except requests.RequestException:
                return False
        return response.status_code == 200

    def delete(self, name: str) -> None:
        requests.delete(self._object_url(name), headers=self._headers(), timeout=self.timeout)

    def url(self, name: str) -> str:
        if not self.public_bucket:
            raise ValueError("This Supabase bucket is private; use the KIS signed media endpoint.")
        return self._object_url(name, public=True)

    def size(self, name: str) -> int:
        response = requests.head(self._object_url(name), headers=self._headers(), timeout=self.timeout)
        if response.status_code == 405:
            response = requests.get(self._object_url(name), headers=self._headers(), timeout=self.timeout, stream=True)
        if response.status_code >= 400:
            raise FileNotFoundError(name)
        return int(response.headers.get("content-length") or 0)
