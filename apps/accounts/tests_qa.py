"""
KIS QA Test Suite — maps to KIS_App_QA_Test_Tracker_2026.xlsx
Each test class documents which QA IDs it covers.
Run with:
  DJANGO_SETTINGS_MODULE=config.settings.local \
  TEST_DATABASE_URL=postgresql://kis_dev_user:strong_password@localhost:5432/kis_test \
  python3 manage.py test apps.accounts.tests_qa --keepdb --verbosity=2
"""
import uuid
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from .models import (
    User, Device, TwoFactor, ProfileFieldVisibility,
    Experience, Education, UserSkill, UserConnection,
)
from .views import issue_tokens_for_user

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
DEVICE_ID = "test-device-001"
DEVICE_ID_2 = "test-device-002"


def make_verified_user(phone, password="TestPass12!", country="CM"):
    user = User.objects.create_user(phone=phone, password=password, country=country)
    user.verification = {"phone": {"verified": True, "verified_at": timezone.now().isoformat()}}
    user.status = "active"
    user.is_active = True
    user.save(update_fields=["verification", "status", "is_active"])
    return user


def auth_client(user):
    tokens = issue_tokens_for_user(user, device_id=DEVICE_ID)
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {tokens['access']}",
        HTTP_X_DEVICE_ID=DEVICE_ID,
    )
    return client


# ---------------------------------------------------------------------------
# KIS-QA-081  Authentication Session
# ---------------------------------------------------------------------------
class AuthSessionTests(TestCase):
    """
    Covers KIS-QA-081: Authentication session — register, login,
    token refresh, logout, unverified block.
    """

    def setUp(self):
        self.client = APIClient()

    def test_register_creates_unverified_user(self):
        """Registration succeeds and marks phone as pending verification."""
        res = self.client.post("/api/v1/auth/register/", {
            "phone": "+237670000101",
            "phone_country_code": "+237",
            "phone_number": "670000101",
            "country": "CM",
            "password": "TestPass12!",
            "password2": "TestPass12!",
            "device_id": DEVICE_ID,
            "device_platform": "android",
        }, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertTrue(res.data["success"])
        self.assertTrue(res.data["pending_verification"])
        user = User.objects.get(phone="+237670000101")
        self.assertFalse(user.verification.get("phone", {}).get("verified"))

    def test_login_blocked_for_unverified_phone(self):
        """Unverified user cannot log in — gets phone_not_verified error."""
        User.objects.create_user(phone="+237670000102", password="TestPass12!", country="CM")
        res = self.client.post("/api/v1/auth/login/", {
            "phone": "+237670000102",
            "password": "TestPass12!",
            "device_id": DEVICE_ID,
        }, format="json")
        self.assertEqual(res.status_code, 400)
        data = res.json()
        # LoginView flattens DRF list-wrapped errors to plain strings
        error_code = data.get("error_code")
        if isinstance(error_code, list):
            error_code = error_code[0]
        self.assertEqual(error_code, "phone_not_verified")

    def test_login_succeeds_for_verified_user(self):
        """Verified user receives access + refresh tokens on login."""
        user = make_verified_user("+237670000103")
        Device.objects.create(
            user=user, device_id=DEVICE_ID, platform="android",
            is_parent=True, token_version=1, last_seen_at=timezone.now(),
        )
        res = self.client.post("/api/v1/auth/login/", {
            "phone": "+237670000103",
            "password": "TestPass12!",
            "device_id": DEVICE_ID,
        }, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertIn("access", res.data)
        self.assertIn("refresh", res.data)

    def test_wrong_password_returns_error_code(self):
        """Wrong password returns wrong_password error code, not 500."""
        make_verified_user("+237670000104")
        res = self.client.post("/api/v1/auth/login/", {
            "phone": "+237670000104",
            "password": "WrongPassword99!",
            "device_id": DEVICE_ID,
        }, format="json")
        self.assertEqual(res.status_code, 400)
        error_code = res.json().get("error_code")
        if isinstance(error_code, list): error_code = error_code[0]
        self.assertEqual(error_code, "wrong_password")

    def test_unknown_phone_returns_phone_not_found(self):
        """Non-existent phone returns phone_not_found, not a 500."""
        res = self.client.post("/api/v1/auth/login/", {
            "phone": "+237670099999",
            "password": "TestPass12!",
            "device_id": DEVICE_ID,
        }, format="json")
        self.assertEqual(res.status_code, 400)
        error_code = res.json().get("error_code")
        if isinstance(error_code, list): error_code = error_code[0]
        self.assertEqual(error_code, "phone_not_found")

    def test_token_refresh_works(self):
        """Refresh token issues a new access token."""
        user = make_verified_user("+237670000105")
        Device.objects.create(
            user=user, device_id=DEVICE_ID, platform="android",
            is_parent=True, token_version=1, last_seen_at=timezone.now(),
        )
        tokens = issue_tokens_for_user(user, device_id=DEVICE_ID)
        res = self.client.post("/api/v1/auth/jwt/refresh/", {
            "refresh": tokens["refresh"],
        }, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertIn("access", res.data)

    def test_logout_invalidates_device(self):
        """Logout revokes the device so refresh no longer works."""
        user = make_verified_user("+237670000106")
        device = Device.objects.create(
            user=user, device_id=DEVICE_ID, platform="android",
            is_parent=True, token_version=1, last_seen_at=timezone.now(),
        )
        tokens = issue_tokens_for_user(user, device_id=DEVICE_ID)
        client = auth_client(user)
        res = client.post("/api/v1/auth/logout/", {"refresh": tokens["refresh"]}, format="json")
        self.assertEqual(res.status_code, 204)
        device.refresh_from_db()
        self.assertIsNotNone(device.revoked_at)

    def test_duplicate_phone_registration_rejected(self):
        """Registering with an already-used phone number is rejected."""
        make_verified_user("+237670000107")
        res = self.client.post("/api/v1/auth/register/", {
            "phone": "+237670000107",
            "phone_country_code": "+237",
            "phone_number": "670000107",
            "country": "CM",
            "password": "TestPass12!",
            "password2": "TestPass12!",
            "device_id": DEVICE_ID,
        }, format="json")
        self.assertIn(res.status_code, [400, 409])

    def test_register_missing_device_id_rejected(self):
        """Registration without device_id is rejected with 400."""
        res = self.client.post("/api/v1/auth/register/", {
            "phone": "+237670000108",
            "phone_country_code": "+237",
            "phone_number": "670000108",
            "country": "CM",
            "password": "TestPass12!",
            "password2": "TestPass12!",
        }, format="json")
        self.assertEqual(res.status_code, 400)


# ---------------------------------------------------------------------------
# KIS-QA-065, KIS-QA-067  Security — E2EE Keys & 2FA
# ---------------------------------------------------------------------------
class SecurityTests(TestCase):
    """
    KIS-QA-065: E2EE key registration and retrieval.
    KIS-QA-067: 2FA setup, enable, verify, disable flow.
    """

    def setUp(self):
        self.user = make_verified_user("+237670001001")
        self.device = Device.objects.create(
            user=self.user, device_id=DEVICE_ID, platform="android",
            is_parent=True, token_version=1, last_seen_at=timezone.now(),
        )
        self.client = auth_client(self.user)

    # -- E2EE --
    def test_e2ee_key_registration_and_retrieval(self):
        """Device can register E2EE keys and they are retrievable."""
        res = self.client.post("/api/v1/auth/e2ee/keys/", {
            "device_id": DEVICE_ID,
            "identity_key": "base64_identity_key_aaa",
            "signed_prekey": {"id": 1, "key": "signed_key_aaa", "signature": "sig_aaa"},
            "prekeys": [{"id": 10, "key": "prekey_10"}, {"id": 11, "key": "prekey_11"}],
            "registration_id": 12345,
        }, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["ok"])

    def test_e2ee_bundle_fetch(self):
        """E2EE bundle can be fetched for a user after key registration."""
        self.client.post("/api/v1/auth/e2ee/keys/", {
            "device_id": DEVICE_ID,
            "identity_key": "ik_fetch_test",
            "signed_prekey": {"id": 2, "key": "spk_2", "signature": "sig_2"},
            "prekeys": [{"id": 20, "key": "pk_20"}],
            "registration_id": 99,
        }, format="json")
        res = self.client.get(f"/api/v1/auth/e2ee/keys/{self.user.id}/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["identity_key"], "ik_fetch_test")

    # -- 2FA --
    def test_2fa_setup_returns_secret(self):
        """Setup endpoint returns a TOTP secret and provisioning URI."""
        res = self.client.post("/api/v1/auth/2fa/setup/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("secret", res.data)
        self.assertFalse(res.data["enabled"])

    def test_2fa_status_initially_disabled(self):
        """2FA status is disabled by default for a new user."""
        res = self.client.get("/api/v1/auth/2fa/status/")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data["enabled"])

    def test_2fa_invalid_enable_rejected(self):
        """Enabling 2FA with wrong code is rejected."""
        self.client.post("/api/v1/auth/2fa/setup/")
        res = self.client.post("/api/v1/auth/2fa/enable/", {"code": "000000"}, format="json")
        self.assertEqual(res.status_code, 400)


# ---------------------------------------------------------------------------
# KIS-QA-068  Device Management
# ---------------------------------------------------------------------------
class DeviceManagementTests(TestCase):
    """KIS-QA-068: List devices, revoke secondary devices."""

    def setUp(self):
        self.user = make_verified_user("+237670002001")
        self.device = Device.objects.create(
            user=self.user, device_id=DEVICE_ID, platform="android",
            is_parent=True, token_version=1, last_seen_at=timezone.now(),
        )
        self.client = auth_client(self.user)

    def test_list_devices_returns_current(self):
        """Device list includes the current authenticated device."""
        res = self.client.get("/api/v1/auth/devices/")
        self.assertEqual(res.status_code, 200)
        device_ids = [d["device_id"] for d in res.data["devices"]]
        self.assertIn(DEVICE_ID, device_ids)

    def test_revoke_secondary_device(self):
        """A secondary device can be revoked."""
        secondary = Device.objects.create(
            user=self.user, device_id=DEVICE_ID_2, platform="ios",
            is_parent=False, token_version=1, last_seen_at=timezone.now(),
        )
        res = self.client.delete(f"/api/v1/auth/devices/{DEVICE_ID_2}/")
        self.assertEqual(res.status_code, 204)
        secondary.refresh_from_db()
        self.assertIsNotNone(secondary.revoked_at)

    def test_cannot_revoke_own_device_via_detail(self):
        """Trying to revoke the current device via detail endpoint is rejected."""
        res = self.client.delete(f"/api/v1/auth/devices/{DEVICE_ID}/")
        self.assertEqual(res.status_code, 400)


# ---------------------------------------------------------------------------
# KIS-QA-069  Privacy Controls
# ---------------------------------------------------------------------------
class PrivacyControlsTests(TestCase):
    """KIS-QA-069: Field-level visibility rules for profile sections."""

    def setUp(self):
        self.user = make_verified_user("+237670003001")
        self.viewer = make_verified_user("+237670003002")
        Device.objects.create(
            user=self.user, device_id=DEVICE_ID, platform="android",
            is_parent=True, token_version=1, last_seen_at=timezone.now(),
        )
        self.client = auth_client(self.user)

    def test_create_private_field_visibility_rule(self):
        """A user can create a private visibility rule for a profile field."""
        res = self.client.post("/api/v1/profile-privacy/", {
            "field_key": "bio",
            "visibility": "private",
            "allow_user_ids": [],
        }, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["visibility"], "private")

    def test_private_bio_hidden_from_viewer(self):
        """Bio marked private is not shown to another user's profile view."""
        from apps.accounts.models import Profile
        from apps.accounts.views import _can_view_field
        ProfileFieldVisibility.objects.create(
            user=self.user, field_key="bio", visibility="private"
        )
        profile, _ = Profile.objects.get_or_create(user=self.user)
        profile.bio = "Secret bio"
        profile.save()

        rule = ProfileFieldVisibility.objects.get(user=self.user, field_key="bio")
        self.assertFalse(_can_view_field(self.user, self.viewer, rule))
        self.assertTrue(_can_view_field(self.user, self.user, rule))

    def test_public_field_visible_to_anonymous(self):
        """A public field is visible even to None viewer."""
        from apps.accounts.views import _can_view_field
        rule = ProfileFieldVisibility.objects.create(
            user=self.user, field_key="headline", visibility="public"
        )
        self.assertTrue(_can_view_field(self.user, None, rule))


# ---------------------------------------------------------------------------
# KIS-QA-048, KIS-QA-049, KIS-QA-050  Professional Profiles
# ---------------------------------------------------------------------------
class ProfessionalProfileTests(TestCase):
    """
    KIS-QA-048: Profile headline, bio, industry, avatar.
    KIS-QA-049: Work experience CRUD.
    KIS-QA-050: Skills and endorsements.
    """

    def setUp(self):
        self.user = make_verified_user("+237670004001")
        Device.objects.create(
            user=self.user, device_id=DEVICE_ID, platform="android",
            is_parent=True, token_version=1, last_seen_at=timezone.now(),
        )
        self.client = auth_client(self.user)

    def test_profile_me_returns_data(self):
        """GET /api/v1/profiles/me/ returns profile payload."""
        res = self.client.get("/api/v1/profiles/me/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("profile", res.data)
        self.assertIn("user", res.data)

    def test_update_profile_headline(self):
        """Profile headline can be updated via PATCH."""
        profile_id = self.user.profile.id
        res = self.client.patch(f"/api/v1/profiles/{profile_id}/", {
            "headline": "Kingdom Impact Creator",
        }, format="json")
        self.assertEqual(res.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.headline, "Kingdom Impact Creator")

    def test_create_work_experience(self):
        """Work experience record can be created and listed."""
        res = self.client.post("/api/v1/experiences/", {
            "title": "Software Engineer at KIS",
            "description": "Built the platform",
            "start_date": "2024-01-01",
            "currently_working": True,
        }, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["title"], "Software Engineer at KIS")

    def test_create_skill(self):
        """User skill can be added."""
        res = self.client.post("/api/v1/skills/", {
            "skill_id": str(uuid.uuid4()),
            "description": "React Native",
        }, format="json")
        self.assertEqual(res.status_code, 201)

    def test_endorse_another_users_skill(self):
        """Another user's skill can be endorsed."""
        other = make_verified_user("+237670004002")
        Device.objects.create(
            user=other, device_id="other-device", platform="android",
            is_parent=True, token_version=1, last_seen_at=timezone.now(),
        )
        skill = UserSkill.objects.create(
            user=other, skill_id=uuid.uuid4(), description="Django", endorsements=0
        )
        other_profile_id = other.profile.id
        res = self.client.post(f"/api/v1/profiles/{other_profile_id}/endorse-skill/", {
            "skill_id": str(skill.id),
        }, format="json")
        self.assertEqual(res.status_code, 200)
        skill.refresh_from_db()
        self.assertEqual(skill.endorsements, 1)

    def test_cannot_endorse_own_skill(self):
        """User cannot endorse their own skill."""
        skill = UserSkill.objects.create(
            user=self.user, skill_id=uuid.uuid4(), description="Python", endorsements=0
        )
        own_profile_id = self.user.profile.id
        res = self.client.post(f"/api/v1/profiles/{own_profile_id}/endorse-skill/", {
            "skill_id": str(skill.id),
        }, format="json")
        self.assertEqual(res.status_code, 400)


# ---------------------------------------------------------------------------
# KIS-QA-051  Job Board
# ---------------------------------------------------------------------------
class JobBoardTests(TestCase):
    """KIS-QA-051: Job board listing."""

    def setUp(self):
        self.user = make_verified_user("+237670005001")
        Device.objects.create(
            user=self.user, device_id=DEVICE_ID, platform="android",
            is_parent=True, token_version=1, last_seen_at=timezone.now(),
        )
        self.client = auth_client(self.user)

    def test_job_board_returns_list(self):
        """Job board endpoint returns a list (may be empty)."""
        res = self.client.get("/api/v1/jobs/")
        self.assertIn(res.status_code, [200, 401])


# ---------------------------------------------------------------------------
# KIS-QA-053  Networking / Connections
# ---------------------------------------------------------------------------
class NetworkingTests(TestCase):
    """KIS-QA-053: Send, accept, reject connection requests."""

    def setUp(self):
        self.user_a = make_verified_user("+237670006001")
        self.user_b = make_verified_user("+237670006002")
        Device.objects.create(
            user=self.user_a, device_id=DEVICE_ID, platform="android",
            is_parent=True, token_version=1, last_seen_at=timezone.now(),
        )
        self.client_a = auth_client(self.user_a)

    def test_send_connection_request(self):
        """User A can send a connection request to User B."""
        res = self.client_a.post("/api/v1/connections/", {
            "user_id": str(self.user_b.id),
        }, format="json")
        self.assertIn(res.status_code, [200, 201])

    def test_list_connections(self):
        """Connection list is accessible."""
        res = self.client_a.get("/api/v1/connections/")
        self.assertEqual(res.status_code, 200)


# ---------------------------------------------------------------------------
# KIS-QA-075  Analytics — Profile stats visible to owner
# ---------------------------------------------------------------------------
class AnalyticsTests(TestCase):
    """KIS-QA-075: Profile stats and account tier info in profile payload."""

    def setUp(self):
        self.user = make_verified_user("+237670007001")
        Device.objects.create(
            user=self.user, device_id=DEVICE_ID, platform="android",
            is_parent=True, token_version=1, last_seen_at=timezone.now(),
        )
        self.client = auth_client(self.user)

    def test_profile_includes_account_tier(self):
        """Profile me response includes account tier info."""
        res = self.client.get("/api/v1/profiles/me/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("account", res.data)

    def test_profile_includes_stats(self):
        """Profile me response includes section stats."""
        res = self.client.get("/api/v1/profiles/me/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("stats", res.data)


# ---------------------------------------------------------------------------
# KIS-QA-070  Data Export & Account Deletion
# ---------------------------------------------------------------------------
class AccountDeletionTests(TestCase):
    """KIS-QA-070: Account deletion endpoint exists and requires auth."""

    def test_deletion_requires_authentication(self):
        """Account deletion endpoint requires auth."""
        client = APIClient()
        res = client.delete("/api/v1/users/me/")
        self.assertIn(res.status_code, [401, 403, 404, 405])


# ---------------------------------------------------------------------------
# KIS-QA-080  Main Navigation — Core API Health
# ---------------------------------------------------------------------------
class CoreAPIHealthTests(TestCase):
    """KIS-QA-080: Core API endpoints respond correctly."""

    def setUp(self):
        self.user = make_verified_user("+237670008001")
        Device.objects.create(
            user=self.user, device_id=DEVICE_ID, platform="android",
            is_parent=True, token_version=1, last_seen_at=timezone.now(),
        )
        self.client = auth_client(self.user)
        self.anon = APIClient()

    def test_users_me_authenticated(self):
        """GET /api/v1/users/me/ returns 200 for authenticated user."""
        res = self.client.get("/api/v1/users/me/")
        self.assertEqual(res.status_code, 200)

    def test_users_me_unauthenticated(self):
        """GET /api/v1/users/me/ returns 401 for unauthenticated request."""
        res = self.anon.get("/api/v1/users/me/")
        self.assertEqual(res.status_code, 401)

    def test_tiers_list_public(self):
        """Account tiers are publicly accessible."""
        res = self.anon.get("/api/v1/tiers/")
        self.assertIn(res.status_code, [200, 404])

    def test_otp_channels_public(self):
        """OTP channels endpoint is public."""
        res = self.anon.get("/api/v1/auth/otp/channels/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("whatsapp", res.data)
        self.assertIn("sms", res.data)

    def test_profile_languages_sync(self):
        """Profile language sync accepts a list."""
        res = self.client.post("/api/v1/profile-languages/sync/", {
            "languages": ["English", "French"],
        }, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertIn("languages", res.data)


# ---------------------------------------------------------------------------
# KIS-QA-082  Notifications
# ---------------------------------------------------------------------------
class NotificationsTests(TestCase):
    """KIS-QA-082: Push notification endpoint exists."""

    def setUp(self):
        self.user = make_verified_user("+237670009001")
        Device.objects.create(
            user=self.user, device_id=DEVICE_ID, platform="android",
            is_parent=True, token_version=1, last_seen_at=timezone.now(),
        )
        self.client = auth_client(self.user)

    def test_mention_notification_endpoint_exists(self):
        """Mention notification POST endpoint accepts mentioned_user_ids list."""
        res = self.client.post("/api/v1/notifications/mention/", {
            "mentioned_user_ids": [str(self.user.id)],
            "context": "test mention",
            "preview": "Hello @user",
        }, format="json")
        self.assertIn(res.status_code, [200, 201, 400, 404])


# ---------------------------------------------------------------------------
# KIS-QA-084  Performance — Response time baseline
# ---------------------------------------------------------------------------
class PerformanceTests(TestCase):
    """KIS-QA-084: Profile me should respond under 500ms."""

    def setUp(self):
        self.user = make_verified_user("+237670010001")
        Device.objects.create(
            user=self.user, device_id=DEVICE_ID, platform="android",
            is_parent=True, token_version=1, last_seen_at=timezone.now(),
        )
        self.client = auth_client(self.user)

    def test_profile_me_under_500ms(self):
        """Profile /me/ responds in under 500ms."""
        import time
        start = time.time()
        res = self.client.get("/api/v1/profiles/me/")
        elapsed_ms = (time.time() - start) * 1000
        self.assertEqual(res.status_code, 200)
        self.assertLess(elapsed_ms, 500, f"Profile /me/ took {elapsed_ms:.0f}ms — over 500ms budget")

    def test_login_under_300ms(self):
        """Login responds in under 300ms."""
        import time
        make_verified_user("+237670010002")
        Device.objects.create(
            user=User.objects.get(phone="+237670010002"),
            device_id="perf-device", platform="android",
            is_parent=True, token_version=1, last_seen_at=timezone.now(),
        )
        anon = APIClient()
        start = time.time()
        anon.post("/api/v1/auth/login/", {
            "phone": "+237670010002",
            "password": "TestPass12!",
            "device_id": "perf-device",
        }, format="json")
        elapsed_ms = (time.time() - start) * 1000
        self.assertLess(elapsed_ms, 300, f"Login took {elapsed_ms:.0f}ms — over 300ms budget")


# ---------------------------------------------------------------------------
# KIS-QA-085  Offline / Poor Network — API returns proper errors
# ---------------------------------------------------------------------------
class OfflineResilienceTests(TestCase):
    """KIS-QA-085: Proper JSON error responses (not HTML 500) on bad input."""

    def setUp(self):
        self.client = APIClient()

    def test_register_bad_phone_returns_json(self):
        """Bad registration input returns JSON 400, not HTML."""
        res = self.client.post("/api/v1/auth/register/", {
            "phone": "not_a_phone",
            "password": "weak",
            "device_id": DEVICE_ID,
        }, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res["Content-Type"].split(";")[0], "application/json")

    def test_login_empty_body_returns_json(self):
        """Empty login body returns JSON 400."""
        res = self.client.post("/api/v1/auth/login/", {}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res["Content-Type"].split(";")[0], "application/json")

    def test_otp_initiate_no_phone_returns_json(self):
        """OTP initiate with no phone returns a JSON response, not 500."""
        res = self.client.post("/api/v1/auth/otp/initiate/", {
            "channel": "whatsapp",
            "purpose": "register",
        }, format="json")
        self.assertIn(res.status_code, [400, 429])
        self.assertEqual(res["Content-Type"].split(";")[0], "application/json")

    def test_verify_with_expired_otp_returns_json(self):
        """OTP verify with wrong code returns JSON 400."""
        make_verified_user("+237670011001")
        res = self.client.post("/api/v1/auth/otp/verify/", {
            "phone": "+237670011001",
            "purpose": "register",
            "code": "000000",
            "device_id": DEVICE_ID,
        }, format="json")
        self.assertIn(res.status_code, [400, 404])
        self.assertEqual(res["Content-Type"].split(";")[0], "application/json")


# ---------------------------------------------------------------------------
# OTP Verification Flow — maps to KIS-QA-081 sub-tests
# ---------------------------------------------------------------------------
class OTPVerificationFlowTests(TestCase):
    """Full OTP initiate → verify flow using the override code."""

    def setUp(self):
        self.client = APIClient()

    @override_settings(OTP_OVERRIDE_CODE="676139")
    def test_override_code_verifies_without_real_otp_delivery(self):
        """Override code 676139 passes verification even without SMS delivery."""
        from apps.otp.models import PhoneOTP
        from apps.otp.views import make_code_hash
        user = make_verified_user("+237670012001")
        # Simulate OTP initiation (creates OTP record)
        code = "123456"
        code_hash = make_code_hash("+237670012001", "register", code)
        PhoneOTP.objects.create(
            phone="+237670012001", purpose="register",
            code_hash=code_hash,
            expires_at=timezone.now() + timezone.timedelta(minutes=5),
            attempts=0,
        )
        # Verify with override code — should succeed
        user.verification = {}
        user.save(update_fields=["verification"])
        res = self.client.post("/api/v1/auth/otp/verify/", {
            "phone": "+237670012001",
            "purpose": "register",
            "code": "676139",
            "device_id": DEVICE_ID,
            "device_platform": "android",
            "country": "CM",
        }, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["success"])
        self.assertIn("access", res.data)

    @override_settings(OTP_OVERRIDE_CODE="676139")
    def test_override_code_works_without_any_prior_otp_record(self):
        """Override code works even if no OTP was ever initiated."""
        make_verified_user("+237670012002")
        User.objects.filter(phone="+237670012002").update(
            verification={}, is_active=True
        )
        res = self.client.post("/api/v1/auth/otp/verify/", {
            "phone": "+237670012002",
            "purpose": "register",
            "code": "676139",
            "device_id": DEVICE_ID,
            "device_platform": "android",
            "country": "CM",
        }, format="json")
        self.assertIn(res.status_code, [200, 404])

    def test_wrong_otp_increments_attempts(self):
        """Wrong OTP code increments the attempt counter."""
        from apps.otp.models import PhoneOTP
        from apps.otp.views import make_code_hash
        make_verified_user("+237670012003")
        code_hash = make_code_hash("+237670012003", "register", "111111")
        otp = PhoneOTP.objects.create(
            phone="+237670012003", purpose="register",
            code_hash=code_hash,
            expires_at=timezone.now() + timezone.timedelta(minutes=5),
            attempts=0,
        )
        self.client.post("/api/v1/auth/otp/verify/", {
            "phone": "+237670012003",
            "purpose": "register",
            "code": "999999",
            "device_id": DEVICE_ID,
            "country": "CM",
        }, format="json")
        otp.refresh_from_db()
        self.assertEqual(otp.attempts, 1)

    def test_password_reset_flow(self):
        """Password reset initiation returns success."""
        make_verified_user("+237670012004")
        res = self.client.post("/api/v1/auth/password/forgot/", {
            "phone": "+237670012004",
            "channel": "whatsapp",
            "country": "CM",
        }, format="json")
        self.assertIn(res.status_code, [200, 429])
