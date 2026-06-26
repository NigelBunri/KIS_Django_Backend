"""Remote media storage backends for KIS uploads.

The Supabase backend is intentionally small and dependency-light: it uses the
Supabase Storage REST API through requests so Render can persist uploads without
requiring boto3/django-storages. Secrets are read only from environment via
Django settings and are never logged.
"""

from __future__ import annotations

import mimetypes
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


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env(name)
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


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
        self.timeout = float(_env("SUPABASE_STORAGE_TIMEOUT_SECONDS", "180") or "180")
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


@deconstructible
class S3MediaStorage(Storage):
    """Django Storage backend for AWS S3-compatible media buckets.

    Required env:
      AWS_STORAGE_BUCKET_NAME=<bucket>
      AWS_ACCESS_KEY_ID=<server-only key>
      AWS_SECRET_ACCESS_KEY=<server-only secret>
      AWS_S3_REGION_NAME=<region>

    Optional env:
      AWS_S3_ENDPOINT_URL=<S3-compatible endpoint>
      AWS_S3_CUSTOM_DOMAIN=<cdn-or-bucket-domain>
      AWS_S3_PUBLIC_BUCKET=True
      AWS_S3_PRESIGNED_EXPIRY_SECONDS=3600
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bucket = _env("AWS_STORAGE_BUCKET_NAME") or _env("AWS_S3_BUCKET_NAME")
        self.region_name = _env("AWS_S3_REGION_NAME", "eu-west-2")
        self.endpoint_url = _env("AWS_S3_ENDPOINT_URL")
        self.custom_domain = _env("AWS_S3_CUSTOM_DOMAIN").strip().strip("/")
        self.public_bucket = _env_bool("AWS_S3_PUBLIC_BUCKET", False)
        self.presigned_expiry = int(_env("AWS_S3_PRESIGNED_EXPIRY_SECONDS", "3600") or "3600")
        self.addressing_style = _env("AWS_S3_ADDRESSING_STYLE")
        if not self.bucket:
            raise ImproperlyConfigured("S3 storage requires AWS_STORAGE_BUCKET_NAME.")

    def _client(self):
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise ImproperlyConfigured("S3 storage requires boto3. Add boto3 to requirements.") from exc

        config_kwargs = {}
        if self.addressing_style:
            config_kwargs["s3"] = {"addressing_style": self.addressing_style}
        return boto3.client(
            "s3",
            region_name=self.region_name,
            endpoint_url=self.endpoint_url or None,
            config=Config(**config_kwargs) if config_kwargs else None,
        )

    def _object_key(self, name: str) -> str:
        clean = str(name or "").replace("\\", "/").lstrip("/")
        if not clean:
            raise ValueError("Storage object name cannot be empty.")
        return clean

    def _save(self, name: str, content) -> str:
        clean_name = self.get_available_name(name)
        key = self._object_key(clean_name)
        content_type = (
            getattr(content, "content_type", None)
            or mimetypes.guess_type(key)[0]
            or "application/octet-stream"
        )
        try:
            content.seek(0)
        except Exception:
            pass
        extra_args = {"ContentType": content_type}
        if self.public_bucket:
            extra_args["ACL"] = "public-read"
        self._client().upload_fileobj(content, self.bucket, key, ExtraArgs=extra_args)
        return key

    def _open(self, name: str, mode: str = "rb") -> File:
        key = self._object_key(name)
        try:
            response = self._client().get_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise FileNotFoundError(name) from exc
        body = response["Body"].read()
        return File(ContentFile(body, name=os.path.basename(key)), name=key)

    def exists(self, name: str) -> bool:
        key = self._object_key(name)
        try:
            self._client().head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def delete(self, name: str) -> None:
        key = self._object_key(name)
        self._client().delete_object(Bucket=self.bucket, Key=key)

    def url(self, name: str) -> str:
        key = self._object_key(name)
        if self.public_bucket:
            if self.custom_domain:
                return f"https://{self.custom_domain}/{quote(key, safe='/')}"
            if self.endpoint_url:
                endpoint = self.endpoint_url.rstrip("/")
                return f"{endpoint}/{quote(self.bucket, safe='')}/{quote(key, safe='/')}"
            return f"https://{self.bucket}.s3.{self.region_name}.amazonaws.com/{quote(key, safe='/')}"
        return self._client().generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=self.presigned_expiry,
        )

    def size(self, name: str) -> int:
        key = self._object_key(name)
        try:
            response = self._client().head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise FileNotFoundError(name) from exc
        return int(response.get("ContentLength") or 0)
