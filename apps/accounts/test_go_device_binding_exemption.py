"""
Temporary, explicitly-requested exception: the GO (General Overseer)
identity is exempt from device-binding while settings.GO_DEVICE_BINDING_
EXEMPT is on — no QR-link requirement at password login
(password_login_requires_qr), and no live-Device-row/token_version
requirement on subsequent API calls (validate_device_bound_token). Every
other account must be completely unaffected, and the exemption itself
must be removable by flipping the env var alone (no redeploy).

Run:
  python3 manage.py test apps.accounts.test_go_device_binding_exemption --keepdb -v 2
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.exceptions import AuthenticationFailed

from apps.partners.seed import GO_EMAIL, GO_PHONE
from .jwt_auth import validate_device_bound_token
from .views import password_login_requires_qr

User = get_user_model()


class GoDeviceBindingExemptionTests(TestCase):
    def setUp(self):
        self.go_user = User.objects.create_user(
            phone=GO_PHONE, email=GO_EMAIL, password="TestPass123!", country="CM",
        )
        self.regular_user = User.objects.create_user(
            phone="+237670009001", email="someone@example.com", password="TestPass123!", country="CM",
        )

    # -- password_login_requires_qr -----------------------------------

    @override_settings(GO_DEVICE_BINDING_EXEMPT=True)
    def test_go_never_requires_qr_even_with_an_active_device_on_a_different_id(self):
        from .models import Device

        Device.objects.create(user=self.go_user, device_id="old-device", platform="ios")

        self.assertFalse(password_login_requires_qr(self.go_user, "brand-new-device"))

    @override_settings(GO_DEVICE_BINDING_EXEMPT=True)
    def test_regular_user_still_requires_qr_when_a_different_device_is_active(self):
        from .models import Device

        Device.objects.create(user=self.regular_user, device_id="old-device", platform="ios")

        self.assertTrue(password_login_requires_qr(self.regular_user, "brand-new-device"))

    @override_settings(GO_DEVICE_BINDING_EXEMPT=False)
    def test_flag_off_restores_normal_qr_enforcement_for_go_too(self):
        from .models import Device

        Device.objects.create(user=self.go_user, device_id="old-device", platform="ios")

        self.assertTrue(password_login_requires_qr(self.go_user, "brand-new-device"))

    # -- validate_device_bound_token -----------------------------------

    @override_settings(GO_DEVICE_BINDING_EXEMPT=True)
    def test_go_authenticates_with_no_registered_device_row_at_all(self):
        validated_token = {"device_id": "never-registered-device"}

        # Must not raise, despite no Device row existing for this id.
        result = validate_device_bound_token(
            self.go_user, validated_token, header_device_id="never-registered-device", require_header=True,
        )
        self.assertIsNone(result)

    @override_settings(GO_DEVICE_BINDING_EXEMPT=True)
    def test_regular_user_still_rejected_with_no_registered_device_row(self):
        validated_token = {"device_id": "never-registered-device"}

        with self.assertRaises(AuthenticationFailed):
            validate_device_bound_token(
                self.regular_user, validated_token, header_device_id="never-registered-device", require_header=True,
            )

    @override_settings(GO_DEVICE_BINDING_EXEMPT=False)
    def test_flag_off_restores_normal_device_row_enforcement_for_go_too(self):
        validated_token = {"device_id": "never-registered-device"}

        with self.assertRaises(AuthenticationFailed):
            validate_device_bound_token(
                self.go_user, validated_token, header_device_id="never-registered-device", require_header=True,
            )

    @override_settings(GO_DEVICE_BINDING_EXEMPT=True)
    def test_go_still_requires_a_device_id_claim_to_exist_on_the_token(self):
        # The exemption skips the Device-row/token_version checks, not
        # authentication itself — a token with no device_id claim at all
        # is still rejected, for GO exactly as for anyone else.
        with self.assertRaises(AuthenticationFailed):
            validate_device_bound_token(self.go_user, {}, header_device_id=None, require_header=True)
