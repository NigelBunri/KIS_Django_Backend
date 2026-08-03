# media/apps.py
from django.apps import AppConfig

class MediaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.media"
    verbose_name = "Media & Processing"

    def ready(self):
        # ensure signals are imported
        from . import signals  # noqa: F401
        self._validate_storage_backend_capabilities()

    def _validate_storage_backend_capabilities(self) -> None:
        """Fails loudly at startup, not during a user's upload request, if
        the configured OBJECT_STORAGE_PROVIDER can't run the presigned-PUT
        handshake that profile/marketplace/status uploads always expose
        (apps/media/upload_intent.py's initiate/confirm routes are
        registered unconditionally — there's no way to disable them per
        provider). See SupabaseStorage.supports_presigned_uploads /
        S3MediaStorage.supports_presigned_uploads in storage_backends.py."""
        from django.conf import settings
        from django.core.exceptions import ImproperlyConfigured

        provider = str(getattr(settings, "OBJECT_STORAGE_PROVIDER", "") or "").strip().lower()
        if provider != "supabase":
            return

        from .storage_backends import SupabaseStorage

        if not getattr(SupabaseStorage, "supports_presigned_uploads", False):
            raise ImproperlyConfigured(
                "OBJECT_STORAGE_PROVIDER=supabase does not support the "
                "direct-to-S3 presigned-upload flow used by profile, "
                "marketplace, and status uploads (apps/media/upload_intent.py "
                "requires generate_presigned_put()/head_object_meta(), which "
                "SupabaseStorage does not implement). Set "
                "OBJECT_STORAGE_PROVIDER=s3, or implement a Supabase-compatible "
                "presigned-upload flow and set SupabaseStorage."
                "supports_presigned_uploads = True before enabling this "
                "provider in an environment where those routes are reachable."
            )
