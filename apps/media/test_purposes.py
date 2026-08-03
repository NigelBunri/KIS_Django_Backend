# apps/media/test_purposes.py
"""Tests for the Phase 1 declarative purpose registry (apps/media/purposes.py).

Every assertion here is about the registry preserving upload_intent.py's
EXISTING behavior, not inventing new limits — see purposes.py's module
docstring for why MIME/size values are resolved lazily rather than frozen.
"""

from django.test import TestCase, override_settings

from . import purposes, upload_intent


class PurposeRegistryCompletenessTests(TestCase):
    def test_every_upload_context_is_registered(self):
        self.assertEqual(purposes.purpose_names(), frozenset(upload_intent.UPLOAD_CONTEXTS.keys()))

    def test_registering_unknown_context_is_rejected(self):
        with self.assertRaises(ValueError):
            purposes.register_purpose(
                "not_a_real_context",
                purposes._PurposeSpec(context="x", moderation_context="x", retention_days=None),
            )

    def test_registering_duplicate_purpose_is_rejected(self):
        with self.assertRaises(ValueError):
            purposes.register_purpose(
                "profile_avatar",
                purposes._PurposeSpec(context="profile", moderation_context="profile", retention_days=None),
            )

    def test_unknown_purpose_lookup_raises(self):
        with self.assertRaises(KeyError):
            purposes.get_purpose("does_not_exist")


class PurposeRegistryPreservesExistingLimitsTests(TestCase):
    """Every registered purpose's MIME allowlist/max size must exactly equal
    what upload_intent.UPLOAD_CONTEXTS already enforces — the registry reads
    the SAME functions, so this is really a guard against ever accidentally
    duplicating/freezing those values in purposes.py."""

    def test_all_registered_purposes_match_their_upload_context(self):
        for name, config in upload_intent.UPLOAD_CONTEXTS.items():
            purpose = purposes.get_purpose(name)
            self.assertEqual(purpose.name, name)
            self.assertEqual(set(purpose.allowed_mime_types), config.allowed_content_types())
            self.assertEqual(purpose.max_bytes, config.max_bytes())
            self.assertEqual(purpose.key_prefix, config.key_prefix)

    def test_settings_override_is_reflected_in_registry(self):
        with override_settings(COMMERCE_IMAGE_MAX_UPLOAD_BYTES=1234):
            self.assertEqual(purposes.get_purpose("commerce_shop_image").max_bytes, 1234)
        # Reverts once the override context manager exits — proves the
        # registry never cached a stale value at import time.
        self.assertNotEqual(purposes.get_purpose("commerce_shop_image").max_bytes, 1234)

    def test_status_video_allowlist_matches_settings_default(self):
        purpose = purposes.get_purpose("status_video")
        self.assertEqual(set(purpose.allowed_mime_types), {"video/mp4", "video/quicktime", "video/webm"})
        self.assertEqual(purpose.max_bytes, 50 * 1024 * 1024)

    def test_profile_avatar_and_cover_share_the_same_prefix_and_limits(self):
        avatar = purposes.get_purpose("profile_avatar")
        cover = purposes.get_purpose("profile_cover")
        self.assertEqual(avatar.key_prefix, cover.key_prefix)
        self.assertEqual(avatar.allowed_mime_types, cover.allowed_mime_types)
        self.assertEqual(avatar.max_bytes, cover.max_bytes)

    def test_registry_does_not_normalize_differing_commerce_limits(self):
        """commerce_complaint_attachment (15MB, allows PDF) and
        commerce_shop_image (8MB, images only) must stay genuinely
        different — Phase 1 is explicitly not a normalization pass."""
        complaint = purposes.get_purpose("commerce_complaint_attachment")
        shop_image = purposes.get_purpose("commerce_shop_image")
        self.assertNotEqual(complaint.max_bytes, shop_image.max_bytes)
        self.assertIn("application/pdf", complaint.allowed_mime_types)
        self.assertNotIn("application/pdf", shop_image.allowed_mime_types)


class PurposeContextGroupingTests(TestCase):
    def test_status_purposes_share_the_status_context(self):
        for name in ("status_image", "status_video", "status_audio"):
            self.assertEqual(purposes.get_purpose(name).context, "status")
            self.assertEqual(purposes.get_purpose(name).moderation_context, "status")

    def test_commerce_purposes_share_the_commerce_context(self):
        commerce_names = [
            "commerce_shop_image", "commerce_product_main_image", "commerce_product_gallery_image",
            "commerce_service_image", "commerce_service_gallery_image", "commerce_complaint_attachment",
        ]
        for name in commerce_names:
            self.assertEqual(purposes.get_purpose(name).context, "commerce")

    def test_no_processing_pipeline_exists_yet(self):
        """No purpose declares a processing requirement — Phase 2 doesn't
        implement transcoding/thumbnailing, so nothing should claim to
        need it."""
        for purpose in purposes.list_purposes():
            self.assertEqual(purpose.requires_processing, ())

    def test_every_purpose_has_an_access_authorizer_registered(self):
        """Phase 2: every one of the 11 purposes registers a real
        access_authorizer from its owning feature app's AppConfig.ready()
        (apps/accounts|commerce|statuses/media_hooks.py) — the generic
        signed-url endpoint denies-by-default for any purpose missing one,
        so this is a regression guard against silently losing a hook."""
        for purpose in purposes.list_purposes():
            self.assertIsNotNone(purpose.access_authorizer, f"{purpose.name} has no access_authorizer")

    def test_attach_handlers_only_registered_where_allow_attach_is_true(self):
        for purpose in purposes.list_purposes():
            if purpose.allow_attach:
                self.assertIsNotNone(purpose.attach_handler, f"{purpose.name} allows attach but has no handler")
                self.assertIsNotNone(purpose.authorize_target, f"{purpose.name} allows attach but has no target authorizer")
            else:
                self.assertIsNone(purpose.attach_handler, f"{purpose.name} should not have an attach handler")

    def test_status_and_complaint_purposes_are_create_with_media_not_attach(self):
        for name in ("status_image", "status_video", "status_audio", "commerce_complaint_attachment"):
            purpose = purposes.get_purpose(name)
            self.assertFalse(purpose.allow_attach)
            self.assertIsNone(purpose.attach_handler)

    def test_profile_purposes_auto_attach_and_have_no_generic_attach_handler(self):
        for name in ("profile_avatar", "profile_cover"):
            purpose = purposes.get_purpose(name)
            self.assertFalse(purpose.allow_attach)
            self.assertIsNone(purpose.attach_handler)


class HookRegistrationTests(TestCase):
    """Phase 2: register_target_authorizer/register_attach_handler/
    register_access_authorizer/register_detach_handler are append-only,
    exactly like register_purpose() itself."""

    def test_duplicate_access_authorizer_registration_rejected(self):
        # profile_avatar already has one, registered by
        # apps/accounts/media_hooks.py at app startup.
        with self.assertRaises(ValueError):
            purposes.register_access_authorizer("profile_avatar", lambda user, asset: None)

    def test_duplicate_attach_handler_registration_rejected(self):
        with self.assertRaises(ValueError):
            purposes.register_attach_handler("commerce_shop_image", lambda **kwargs: None)

    def test_duplicate_target_authorizer_registration_rejected(self):
        with self.assertRaises(ValueError):
            purposes.register_target_authorizer("commerce_shop_image", lambda **kwargs: None)

    def test_hook_registration_for_unknown_purpose_rejected(self):
        with self.assertRaises(ValueError):
            purposes.register_access_authorizer("not_a_real_purpose", lambda user, asset: None)

    def test_non_callable_hook_rejected(self):
        with self.assertRaises(TypeError):
            purposes._set_hook("status_image", "access_authorizer", "not_callable")


class PurposeRegistryImmutabilityTests(TestCase):
    def test_media_purpose_dataclass_instances_are_frozen(self):
        purpose = purposes.get_purpose("profile_avatar")
        with self.assertRaises(Exception):
            purpose.max_bytes = 1  # type: ignore[misc]

    def test_purpose_spec_dataclass_instances_are_frozen(self):
        spec = purposes._PurposeSpec(context="x", moderation_context="x", retention_days=None)
        with self.assertRaises(Exception):
            spec.context = "y"  # type: ignore[misc]
