# apps/media/test_storage_capability.py
"""Tests for the Phase 1 storage-provider capability check
(apps.media.apps.MediaConfig._validate_storage_backend_capabilities +
SupabaseStorage/S3MediaStorage.supports_presigned_uploads).

The goal these guard: selecting a storage backend that can't run the
presigned-PUT handshake must fail at Django startup with a clear error, not
with an AttributeError the first time a user tries to upload something.
"""

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings

from .storage_backends import S3MediaStorage, SupabaseStorage


class StorageCapabilityMarkerTests(TestCase):
    def test_s3_backend_declares_presigned_upload_support(self):
        self.assertTrue(S3MediaStorage.supports_presigned_uploads)

    def test_s3_backend_actually_implements_the_required_methods(self):
        # The marker must not lie — both methods upload_intent.py calls
        # have to actually exist on the class.
        self.assertTrue(hasattr(S3MediaStorage, "generate_presigned_put"))
        self.assertTrue(hasattr(S3MediaStorage, "head_object_meta"))

    def test_supabase_backend_declares_no_presigned_upload_support(self):
        self.assertFalse(SupabaseStorage.supports_presigned_uploads)

    def test_supabase_backend_is_missing_the_methods_the_marker_warns_about(self):
        # Confirms the marker reflects reality — if Supabase ever gains
        # these methods, flip the marker to True in the same change.
        self.assertFalse(hasattr(SupabaseStorage, "generate_presigned_put"))
        self.assertFalse(hasattr(SupabaseStorage, "head_object_meta"))


class StorageCapabilityStartupValidationTests(TestCase):
    """Exercises MediaConfig._validate_storage_backend_capabilities()
    directly (the same method Django calls from AppConfig.ready() at
    process startup) rather than trying to reload the whole app registry
    mid-test-run."""

    def _media_app_config(self):
        return apps.get_app_config("media")

    def test_supabase_provider_raises_improperly_configured(self):
        with override_settings(OBJECT_STORAGE_PROVIDER="supabase"):
            with self.assertRaises(ImproperlyConfigured):
                self._media_app_config()._validate_storage_backend_capabilities()

    def test_s3_provider_passes_validation(self):
        with override_settings(OBJECT_STORAGE_PROVIDER="s3"):
            # Must not raise.
            self._media_app_config()._validate_storage_backend_capabilities()

    def test_empty_provider_passes_validation(self):
        """Local/dev with no OBJECT_STORAGE_PROVIDER set falls back to
        Django's disk-based default storage — not this check's concern."""
        with override_settings(OBJECT_STORAGE_PROVIDER=""):
            self._media_app_config()._validate_storage_backend_capabilities()

    def test_unknown_provider_passes_validation_without_guessing(self):
        """Only 'supabase' is a known-unsupported case today; an unrecognized
        provider string is a different (pre-existing, unrelated) failure
        mode, not something this check should guess about."""
        with override_settings(OBJECT_STORAGE_PROVIDER="some_future_provider"):
            self._media_app_config()._validate_storage_backend_capabilities()

    def test_error_message_names_the_actual_problem(self):
        with override_settings(OBJECT_STORAGE_PROVIDER="supabase"):
            with self.assertRaises(ImproperlyConfigured) as ctx:
                self._media_app_config()._validate_storage_backend_capabilities()
        message = str(ctx.exception)
        self.assertIn("supabase", message.lower())
        self.assertIn("presigned", message.lower())
