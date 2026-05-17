from io import StringIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from .models import (
    Device,
    E2EDeviceKey,
    E2EPreKey,
    ProfileArticle,
    ProfileFieldVisibility,
    ProfileLanguage,
    ProfileShowcase,
    User,
    UserContact,
)
from .serializers import ProfileSerializer
from .views import issue_tokens_for_user


class FamilyAccessibilityPreferencesTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(phone="+237670040001", password="TestPass123!", country="CM")
        self.client.force_authenticate(user=self.user)

    def test_family_accessibility_preferences_default_and_child_mode_are_safe(self):
        default_response = self.client.get(reverse("family-accessibility-preferences"))
        self.assertEqual(default_response.status_code, status.HTTP_200_OK)
        self.assertEqual(default_response.data["preferences"]["age_mode"], "adult")
        self.assertTrue(default_response.data["family_safety"]["pornography_blocked_everywhere"])

        child_response = self.client.patch(
            reverse("family-accessibility-preferences"),
            {"preferences": {"age_mode": "child", "hide_sensitive_commerce": False, "navigation_mode": "standard"}},
            format="json",
        )
        self.assertEqual(child_response.status_code, status.HTTP_200_OK)
        self.assertEqual(child_response.data["preferences"]["age_mode"], "child")
        self.assertTrue(child_response.data["preferences"]["hide_sensitive_commerce"])
        self.assertTrue(child_response.data["preferences"]["guardian_review_required"])
        self.assertEqual(child_response.data["preferences"]["navigation_mode"], "guided")
        self.assertGreaterEqual(child_response.data["accessibility"]["min_touch_target"], 52)

    def test_verify_profile_launch_command_passes_safe_defaults(self):
        output = StringIO()

        call_command("verify_profile_launch", stdout=output)

        rendered = output.getvalue()
        self.assertIn("Profile launch guardrails ready: True", rendered)
        self.assertIn("PASS: route:profile_me", rendered)
        self.assertIn("PASS: profile_media_serializer_validation", rendered)
        self.assertIn("PASS: family_accessibility_defaults", rendered)

    def test_profile_media_serializer_rejects_blocked_file_types(self):
        serializer = ProfileSerializer()
        upload = SimpleUploadedFile("unsafe-profile.svg", b"<svg></svg>", content_type="image/svg+xml")

        with self.assertRaises(Exception):
            serializer.validate_avatar_file(upload)


class AccountsDeviceSessionTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone="+237670000001",
            password="TestPass123!",
            country="CM",
        )
        self.current_device = Device.objects.create(
            user=self.user,
            device_id="dev_current",
            platform="ios",
            name="Current iPhone",
            last_seen_at=timezone.now(),
        )
        self.other_device = Device.objects.create(
            user=self.user,
            device_id="dev_other",
            platform="android",
            name="Old Pixel",
            last_seen_at=timezone.now(),
        )
        self.client.force_authenticate(user=self.user)
        self.client.credentials(HTTP_X_DEVICE_ID="dev_current")

    def test_lists_devices_with_current_marker_and_e2ee_state(self):
        E2EDeviceKey.objects.create(
            user=self.user,
            device=self.other_device,
            identity_key="identity",
            signed_prekey_id=1,
            signed_prekey="signed",
            signed_prekey_signature="sig",
            registration_id=7,
        )

        response = self.client.get(reverse("auth-devices"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = response.data["devices"]
        self.assertEqual(len(rows), 2)
        current = next(item for item in rows if item["device_id"] == "dev_current")
        other = next(item for item in rows if item["device_id"] == "dev_other")
        self.assertTrue(current["current"])
        self.assertFalse(current["has_e2ee_keys"])
        self.assertFalse(other["current"])
        self.assertTrue(other["has_e2ee_keys"])

    def test_revoking_other_device_marks_device_revoked_and_removes_keys(self):
        E2EDeviceKey.objects.create(
            user=self.user,
            device=self.other_device,
            identity_key="identity",
            signed_prekey_id=1,
            signed_prekey="signed",
            signed_prekey_signature="sig",
            registration_id=7,
        )
        E2EPreKey.objects.create(
            user=self.user,
            device=self.other_device,
            prekey_id=11,
            prekey="prekey",
        )

        response = self.client.delete(reverse("auth-device-detail", kwargs={"device_id": "dev_other"}))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.other_device.refresh_from_db()
        self.assertIsNotNone(self.other_device.revoked_at)
        self.assertEqual(self.other_device.revoke_reason, "user_device_revoke")
        self.assertFalse(E2EDeviceKey.objects.filter(user=self.user, device=self.other_device).exists())
        self.assertFalse(E2EPreKey.objects.filter(user=self.user, device=self.other_device).exists())

    def test_cannot_revoke_current_device_from_device_endpoint(self):
        response = self.client.delete(reverse("auth-device-detail", kwargs={"device_id": "dev_current"}))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(Device.objects.filter(user=self.user, device_id="dev_current").exists())

    def test_revoked_device_refresh_token_is_rejected(self):
        tokens = issue_tokens_for_user(self.user, device_id="dev_other")

        response = self.client.delete(reverse("auth-device-detail", kwargs={"device_id": "dev_other"}))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        refresh_response = self.client.post(reverse("jwt-refresh"), {"refresh": tokens["refresh"]}, format="json")

        self.assertEqual(refresh_response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(refresh_response.data["detail"], "Device session revoked.")


class AccountsE2EEBundleTests(APITestCase):
    def setUp(self):
        self.viewer = User.objects.create_user(
            phone="+237670000010",
            password="TestPass123!",
            country="CM",
        )
        self.target = User.objects.create_user(
            phone="+237670000011",
            password="TestPass123!",
            country="CM",
        )
        self.viewer_device = Device.objects.create(
            user=self.viewer,
            device_id="viewer_dev",
            platform="ios",
            last_seen_at=timezone.now(),
        )
        self.target_a = Device.objects.create(
            user=self.target,
            device_id="target_a",
            platform="ios",
            last_seen_at=timezone.now(),
        )
        self.target_b = Device.objects.create(
            user=self.target,
            device_id="target_b",
            platform="android",
            last_seen_at=timezone.now(),
        )
        for idx, device in enumerate([self.target_a, self.target_b], start=1):
            E2EDeviceKey.objects.create(
                user=self.target,
                device=device,
                identity_key=f"identity_{idx}",
                signed_prekey_id=idx,
                signed_prekey=f"signed_{idx}",
                signed_prekey_signature=f"sig_{idx}",
                registration_id=idx * 10,
            )
            E2EPreKey.objects.create(
                user=self.target,
                device=device,
                prekey_id=idx * 100,
                prekey=f"prekey_{idx}",
            )
        self.client.force_authenticate(user=self.viewer)
        self.client.credentials(HTTP_X_DEVICE_ID="viewer_dev")

    def test_lists_all_device_bundles_for_target_user(self):
        response = self.client.get(
            reverse("auth-e2ee-keys-user-devices", kwargs={"user_id": self.target.id})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(str(response.data["user_id"]), str(self.target.id))
        devices = response.data["devices"]
        self.assertEqual(len(devices), 2)
        returned_ids = {item["device_id"] for item in devices}
        self.assertEqual(returned_ids, {"target_a", "target_b"})
        for item in devices:
            self.assertIn("signed_prekey", item)
            self.assertIn("one_time_prekey", item)


class AccountsProfileCoreTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            phone="+237670000020",
            password="TestPass123!",
            country="CM",
            display_name="Owner User",
            email="owner@example.com",
        )
        self.owner.profile.headline = "Builder"
        self.owner.profile.bio = "Building with KIS."
        self.owner.profile.industry = "Technology"
        self.owner.profile.save(update_fields=["headline", "bio", "industry", "updated_at"])

        self.viewer = User.objects.create_user(
            phone="+237670000021",
            password="TestPass123!",
            country="CM",
            display_name="Viewer User",
            email="viewer@example.com",
        )

    def test_profiles_me_returns_core_payload(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(reverse("profiles-me"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(str(response.data["user"]["id"]), str(self.owner.id))
        self.assertEqual(response.data["profile"]["headline"], "Builder")
        self.assertIn("sections", response.data)
        self.assertIn("preferences", response.data)
        self.assertIn("privacy", response.data)
        self.assertIn("tiers", response.data)
        self.assertIn("partner_profiles", response.data)

    def test_profile_patch_updates_headline_bio_and_industry(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.patch(
            reverse("profiles-detail", kwargs={"pk": self.owner.profile.id}),
            {
                "headline": "Senior Builder",
                "bio": "Updated profile bio",
                "industry": "Education",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.owner.profile.refresh_from_db()
        self.assertEqual(self.owner.profile.headline, "Senior Builder")
        self.assertEqual(self.owner.profile.bio, "Updated profile bio")
        self.assertEqual(self.owner.profile.industry, "Education")
        self.assertGreaterEqual(self.owner.profile.completion_score, 0)

    def test_profile_language_sync_normalizes_and_replaces_languages(self):
        self.client.force_authenticate(user=self.owner)
        ProfileLanguage.objects.create(user=self.owner, name="French")
        ProfileLanguage.objects.create(user=self.owner, name="German")

        response = self.client.post(
            reverse("profile-languages-sync"),
            {
                "languages": [" english ", {"label": "French"}, "English", "Unsupported"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["languages"], ["English", "French"])
        saved = list(
            ProfileLanguage.objects.filter(user=self.owner).order_by("created_at").values_list("name", flat=True)
        )
        self.assertEqual(saved, ["English", "French"])

    def test_profile_language_sync_rejects_non_list_payloads(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.post(
            reverse("profile-languages-sync"),
            {
                "languages": "English,French",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["detail"], "languages must be a list.")

    def test_profile_view_hides_private_email_and_shows_contact_phone_for_mutual_contacts(self):
        ProfileFieldVisibility.objects.create(
            user=self.owner,
            field_key="contact_email",
            visibility="private",
        )
        ProfileFieldVisibility.objects.create(
            user=self.owner,
            field_key="contact_phone",
            visibility="contacts",
        )
        UserContact.objects.create(
            user=self.owner,
            contact_user=self.viewer,
            contact_phone=self.viewer.phone,
            contact_phone_number=self.viewer.phone_number or "670000021",
        )
        UserContact.objects.create(
            user=self.viewer,
            contact_user=self.owner,
            contact_phone=self.owner.phone,
            contact_phone_number=self.owner.phone_number or "670000020",
        )

        self.client.force_authenticate(user=self.viewer)
        response = self.client.get(reverse("profiles-view", kwargs={"pk": self.owner.profile.id}))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["user"]["phone"], self.owner.phone)
        self.assertIsNone(response.data["user"]["email"])

    def test_profile_view_hides_contacts_only_phone_for_anonymous_viewer(self):
        ProfileFieldVisibility.objects.create(
            user=self.owner,
            field_key="contact_phone",
            visibility="contacts",
        )

        response = self.client.get(reverse("profiles-view", kwargs={"pk": self.owner.profile.id}))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["user"]["phone"])

    def test_profile_view_allows_custom_email_for_explicit_viewer_only(self):
        rule = ProfileFieldVisibility.objects.create(
            user=self.owner,
            field_key="contact_email",
            visibility="custom",
        )
        rule.allow_targets.create(
            target_user=self.viewer,
            target_phone=self.viewer.phone,
            target_phone_number=self.viewer.phone_number or "670000021",
        )
        other_viewer = User.objects.create_user(
            phone="+237670000022",
            password="TestPass123!",
            country="CM",
            display_name="Other Viewer",
            email="other@example.com",
        )

        self.client.force_authenticate(user=self.viewer)
        allowed_response = self.client.get(reverse("profiles-view", kwargs={"pk": self.owner.profile.id}))

        self.assertEqual(allowed_response.status_code, status.HTTP_200_OK)
        self.assertEqual(allowed_response.data["user"]["email"], self.owner.email)

        self.client.force_authenticate(user=other_viewer)
        denied_response = self.client.get(reverse("profiles-view", kwargs={"pk": self.owner.profile.id}))

        self.assertEqual(denied_response.status_code, status.HTTP_200_OK)
        self.assertIsNone(denied_response.data["user"]["email"])

    def test_profile_showcase_create_and_delete(self):
        self.client.force_authenticate(user=self.owner)

        create_response = self.client.post(
            reverse("profile-showcases-list"),
            {
                "type": "highlight",
                "title": "KIS Launch",
                "summary": "Launch highlight",
                "payload": {"cta": "Read more"},
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        showcase_id = create_response.data["id"]
        self.assertTrue(ProfileShowcase.objects.filter(id=showcase_id, user=self.owner).exists())

        delete_response = self.client.delete(
            reverse("profile-showcases-detail", kwargs={"pk": showcase_id})
        )
        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ProfileShowcase.objects.filter(id=showcase_id, user=self.owner).exists())

    def test_profile_showcase_update_keeps_ownership_and_applies_changes(self):
        self.client.force_authenticate(user=self.owner)
        showcase = ProfileShowcase.objects.create(
            user=self.owner,
            type="highlight",
            title="Original title",
            summary="Original summary",
            payload={"cta": "Open"},
        )

        response = self.client.patch(
            reverse("profile-showcases-detail", kwargs={"pk": showcase.id}),
            {
                "title": "Updated title",
                "summary": "Updated summary",
                "payload": {"cta": "Read"},
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        showcase.refresh_from_db()
        self.assertEqual(showcase.user_id, self.owner.id)
        self.assertEqual(showcase.title, "Updated title")
        self.assertEqual(showcase.summary, "Updated summary")
        self.assertEqual(showcase.payload, {"cta": "Read"})

    def test_profile_view_hides_contacts_only_articles_from_anonymous_viewer(self):
        ProfileArticle.objects.create(
            user=self.owner,
            title="Contacts article",
            summary="Visible to contacts only",
            body="Private body",
            status="published",
            visibility="contacts",
        )

        response = self.client.get(reverse("profiles-view", kwargs={"pk": self.owner.profile.id}))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["sections"]["articles"], [])

    def test_profile_view_allows_custom_article_for_explicit_viewer_only(self):
        article = ProfileArticle.objects.create(
            user=self.owner,
            title="Custom article",
            summary="Visible to explicit viewer",
            body="Visible body",
            status="published",
            visibility="custom",
        )
        article.allow_targets.create(
            target_user=self.viewer,
            target_phone=self.viewer.phone,
            target_phone_number=self.viewer.phone_number or "670000021",
        )
        other_viewer = User.objects.create_user(
            phone="+237670000023",
            password="TestPass123!",
            country="CM",
            display_name="Third Viewer",
            email="third@example.com",
        )

        self.client.force_authenticate(user=self.viewer)
        allowed_response = self.client.get(reverse("profiles-view", kwargs={"pk": self.owner.profile.id}))

        self.assertEqual(allowed_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(allowed_response.data["sections"]["articles"]), 1)
        self.assertEqual(allowed_response.data["sections"]["articles"][0]["title"], "Custom article")

        self.client.force_authenticate(user=other_viewer)
        denied_response = self.client.get(reverse("profiles-view", kwargs={"pk": self.owner.profile.id}))

        self.assertEqual(denied_response.status_code, status.HTTP_200_OK)
        self.assertEqual(denied_response.data["sections"]["articles"], [])
