"""
Temporary, explicitly-requested exception: the GO (General Overseer)
identity and the dedicated Google Play / Apple App Review test account are
exempt from device-binding while settings.GO_DEVICE_BINDING_EXEMPT is on —
no QR-link requirement at password login (password_login_requires_qr), and
no live-Device-row/token_version requirement on subsequent API calls
(validate_device_bound_token). Every other account must be completely
unaffected, and the exemption itself must be removable by flipping the env
var alone (no redeploy). See apps.accounts.device_binding_exemptions for
the shared identity check both call sites use.

Run:
  python3 manage.py test apps.accounts.test_go_device_binding_exemption --keepdb -v 2
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.exceptions import AuthenticationFailed

from apps.partners.seed import GO_EMAIL, GO_PHONE
from .device_binding_exemptions import APP_REVIEW_EMAIL, APP_REVIEW_PHONE
from .jwt_auth import validate_device_bound_token
from .views import password_login_requires_qr

User = get_user_model()


class GoDeviceBindingExemptionTests(TestCase):
    def setUp(self):
        self.go_user = User.objects.create_user(
            phone=GO_PHONE, email=GO_EMAIL, password="TestPass123!", country="CM",
        )
        self.review_user = User.objects.create_user(
            phone=APP_REVIEW_PHONE, email=APP_REVIEW_EMAIL, password="TestPass123!", country="CM",
        )
        self.regular_user = User.objects.create_user(
            phone="+237670009001", email="someone@example.com", password="TestPass123!", country="CM",
        )

    # -- password_login_requires_qr -----------------------------------

    @override_settings(GO_DEVICE_BINDING_EXEMPT=True)
    def test_exempt_identities_never_require_qr_even_with_an_active_device_on_a_different_id(self):
        from .models import Device

        for exempt_user in (self.go_user, self.review_user):
            Device.objects.create(user=exempt_user, device_id="old-device", platform="ios")
            self.assertFalse(password_login_requires_qr(exempt_user, "brand-new-device"))

    @override_settings(GO_DEVICE_BINDING_EXEMPT=True)
    def test_regular_user_still_requires_qr_when_a_different_device_is_active(self):
        from .models import Device

        Device.objects.create(user=self.regular_user, device_id="old-device", platform="ios")

        self.assertTrue(password_login_requires_qr(self.regular_user, "brand-new-device"))

    @override_settings(GO_DEVICE_BINDING_EXEMPT=False)
    def test_flag_off_restores_normal_qr_enforcement_for_exempt_identities_too(self):
        from .models import Device

        for exempt_user in (self.go_user, self.review_user):
            Device.objects.create(user=exempt_user, device_id="old-device", platform="ios")
            self.assertTrue(password_login_requires_qr(exempt_user, "brand-new-device"))

    # -- validate_device_bound_token -----------------------------------

    @override_settings(GO_DEVICE_BINDING_EXEMPT=True)
    def test_exempt_identities_authenticate_with_no_registered_device_row_at_all(self):
        validated_token = {"device_id": "never-registered-device"}

        for exempt_user in (self.go_user, self.review_user):
            # Must not raise, despite no Device row existing for this id.
            result = validate_device_bound_token(
                exempt_user, validated_token, header_device_id="never-registered-device", require_header=True,
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
    def test_flag_off_restores_normal_device_row_enforcement_for_exempt_identities_too(self):
        validated_token = {"device_id": "never-registered-device"}

        for exempt_user in (self.go_user, self.review_user):
            with self.assertRaises(AuthenticationFailed):
                validate_device_bound_token(
                    exempt_user, validated_token, header_device_id="never-registered-device", require_header=True,
                )

    @override_settings(GO_DEVICE_BINDING_EXEMPT=True)
    def test_exempt_identities_still_require_a_device_id_claim_to_exist_on_the_token(self):
        # The exemption skips the Device-row/token_version checks, not
        # authentication itself — a token with no device_id claim at all
        # is still rejected, for these identities exactly as for anyone else.
        for exempt_user in (self.go_user, self.review_user):
            with self.assertRaises(AuthenticationFailed):
                validate_device_bound_token(exempt_user, {}, header_device_id=None, require_header=True)
