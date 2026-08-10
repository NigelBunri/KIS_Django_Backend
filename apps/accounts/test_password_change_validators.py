"""
Regression tests for PasswordChangeView using Django's real
AUTH_PASSWORD_VALIDATORS chain (10-char minimum, common-password,
similarity, numeric-only) instead of a bare len(new_pw) >= 8 check —
matching the validator chain apps.otp.views.PasswordResetView already used,
so "change password" can no longer set a materially weaker password than
"forgot password" does.

Run:
  python3 manage.py test apps.accounts.test_password_change_validators --keepdb -v 2
"""
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import User


@override_settings(SECURE_SSL_REDIRECT=False)
class PasswordChangeValidatorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone="+237699200101", country="CM", password="OriginalPass123!",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _change(self, new_password: str, current="OriginalPass123!"):
        return self.client.post("/api/v1/auth/password/change/", {
            "current_password": current,
            "new_password": new_password,
        }, format="json")

    def test_valid_strong_password_succeeds(self):
        res = self._change("Kingdom-Impact-2026!")
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Kingdom-Impact-2026!"))

    def test_too_short_password_rejected(self):
        """Below the real 10-char minimum (the old check only required 8)."""
        res = self._change("Ab1!ab1!")
        self.assertEqual(res.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("OriginalPass123!"))

    def test_common_password_rejected(self):
        res = self._change("password123")
        self.assertEqual(res.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("OriginalPass123!"))

    def test_fully_numeric_password_rejected(self):
        res = self._change("9876543210123")
        self.assertEqual(res.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("OriginalPass123!"))

    def test_password_too_similar_to_user_attribute_rejected(self):
        user = User.objects.create_user(
            phone="+237699200102", country="CM", password="OriginalPass123!",
            username="kingdomuser2026",
        )
        client = APIClient()
        client.force_authenticate(user)
        res = client.post("/api/v1/auth/password/change/", {
            "current_password": "OriginalPass123!",
            "new_password": "kingdomuser2026",
        }, format="json")
        self.assertEqual(res.status_code, 400)

    def test_wrong_current_password_still_rejected_before_validation(self):
        res = self._change("Kingdom-Impact-2026!", current="WrongPassword!")
        self.assertEqual(res.status_code, 400)
        self.assertIn("incorrect", str(res.data.get("detail", "")).lower())

    def test_missing_fields_rejected(self):
        res = self.client.post("/api/v1/auth/password/change/", {}, format="json")
        self.assertEqual(res.status_code, 400)
