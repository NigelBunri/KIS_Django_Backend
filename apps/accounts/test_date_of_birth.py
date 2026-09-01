"""
User.date_of_birth (self-reported only - see the field's docstring) and
the minimum-age check it enables at signup and profile-update time. The
field is optional deliberately: the currently-published mobile app doesn't
send it yet, so requiring it would reject every signup from an un-updated
client - see MINIMUM_SIGNUP_AGE's comment in serializers.py.

Run:
  python3 manage.py test apps.accounts.test_date_of_birth --keepdb -v 2
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

User = get_user_model()

REGISTER_URL = "/api/v1/auth/register/"


def _dob_for_age(years: int) -> datetime.date:
    today = timezone.now().date()
    return today.replace(year=today.year - years)


class UserAgePropertiesTests(TestCase):
    def test_age_and_is_minor_are_none_when_dob_unknown(self):
        user = User.objects.create_user(phone="+2348500000001", password="pw123456", country="NG")

        self.assertIsNone(user.age)
        self.assertIsNone(user.is_minor)
        self.assertIsNone(user.is_under_13)

    def test_adult_dob(self):
        user = User.objects.create_user(
            phone="+2348500000002", password="pw123456", country="NG", date_of_birth=_dob_for_age(30),
        )

        self.assertEqual(user.age, 30)
        self.assertFalse(user.is_minor)
        self.assertFalse(user.is_under_13)

    def test_minor_dob(self):
        user = User.objects.create_user(
            phone="+2348500000003", password="pw123456", country="NG", date_of_birth=_dob_for_age(15),
        )

        self.assertEqual(user.age, 15)
        self.assertTrue(user.is_minor)
        self.assertFalse(user.is_under_13)

    def test_under_13_dob(self):
        user = User.objects.create_user(
            phone="+2348500000004", password="pw123456", country="NG", date_of_birth=_dob_for_age(10),
        )

        self.assertTrue(user.is_minor)
        self.assertTrue(user.is_under_13)


class RegistrationAgeGateTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def _payload(self, phone, **overrides):
        payload = {
            "password": "StrongPass123!",
            "password2": "StrongPass123!",
            "display_name": "Test User",
            "phone": phone,
            "country": "NG",
            "device_id": f"test-device-{phone}",
        }
        payload.update(overrides)
        return payload

    def test_signup_without_date_of_birth_still_succeeds(self):
        res = self.client.post(REGISTER_URL, self._payload("+2348500000010"), format="json")

        self.assertEqual(res.status_code, 201)
        user = User.objects.get(phone_number="8500000010")
        self.assertIsNone(user.date_of_birth)

    def test_signup_with_adult_date_of_birth_succeeds(self):
        res = self.client.post(
            REGISTER_URL,
            self._payload("+2348500000011", date_of_birth=_dob_for_age(25).isoformat()),
            format="json",
        )

        self.assertEqual(res.status_code, 201)
        user = User.objects.get(phone_number="8500000011")
        self.assertEqual(user.date_of_birth, _dob_for_age(25))

    def test_signup_with_thirteen_year_old_date_of_birth_succeeds(self):
        res = self.client.post(
            REGISTER_URL,
            self._payload("+2348500000012", date_of_birth=_dob_for_age(13).isoformat()),
            format="json",
        )

        self.assertEqual(res.status_code, 201)

    def test_signup_with_under_thirteen_date_of_birth_is_rejected(self):
        res = self.client.post(
            REGISTER_URL,
            self._payload("+2348500000013", date_of_birth=_dob_for_age(12).isoformat()),
            format="json",
        )

        self.assertEqual(res.status_code, 400)
        self.assertIn("date_of_birth", res.data)
        self.assertFalse(User.objects.filter(phone_number="8500000013").exists())

    def test_signup_with_future_date_of_birth_is_rejected(self):
        future = (timezone.now().date() + datetime.timedelta(days=1)).isoformat()
        res = self.client.post(
            REGISTER_URL,
            self._payload("+2348500000014", date_of_birth=future),
            format="json",
        )

        self.assertEqual(res.status_code, 400)

    def test_signup_with_implausible_date_of_birth_is_rejected(self):
        res = self.client.post(
            REGISTER_URL,
            self._payload("+2348500000015", date_of_birth=_dob_for_age(150).isoformat()),
            format="json",
        )

        self.assertEqual(res.status_code, 400)


class ProfileUpdateAgeGateTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(phone="+2348500000020", password="pw123456", country="NG")
        self.client.force_authenticate(self.user)

    def test_can_set_an_adult_date_of_birth_after_signup(self):
        res = self.client.patch(
            f"/api/v1/users/{self.user.id}/",
            {"date_of_birth": _dob_for_age(40).isoformat()},
            format="json",
        )

        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.date_of_birth, _dob_for_age(40))

    def test_cannot_set_an_under_thirteen_date_of_birth(self):
        res = self.client.patch(
            f"/api/v1/users/{self.user.id}/",
            {"date_of_birth": _dob_for_age(9).isoformat()},
            format="json",
        )

        self.assertEqual(res.status_code, 400)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.date_of_birth)
