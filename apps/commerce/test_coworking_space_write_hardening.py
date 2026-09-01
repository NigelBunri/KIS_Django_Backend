"""
CoWorkingSpace has no owner/created_by field - it's a curated directory,
not user-submitted listings - so there's no "your own space" to scope a
self-service update/delete to. get_permissions() previously granted
IsAuthenticated for write actions, letting ANY authenticated user modify
or delete ANY business's listing (full BOLA). See the SECURITY comment
on CoWorkingSpaceViewSet.get_permissions() in business_views.py.

Run:
  python3 manage.py test apps.commerce.test_coworking_space_write_hardening --keepdb -v 2
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.commerce.business_models import CoWorkingSpace

User = get_user_model()

LIST_URL = "/api/v1/business/coworking/"


class CoWorkingSpaceWriteHardeningTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.regular_user = User.objects.create_user(phone="+2348300000001", password="pw123456", country="NG")
        self.staff_user = User.objects.create_user(
            phone="+2348300000002", password="pw123456", country="NG", is_staff=True,
        )
        self.space = CoWorkingSpace.objects.create(
            name="Existing Space", address="1 Main St", city="Lagos", country="NG",
        )

    def _detail_url(self):
        return f"{LIST_URL}{self.space.id}/"

    def test_anonymous_can_still_list_and_read(self):
        res = self.client.get(LIST_URL)
        self.assertEqual(res.status_code, 200)

        res = self.client.get(self._detail_url())
        self.assertEqual(res.status_code, 200)

    def test_a_regular_authenticated_user_cannot_modify_someone_elses_listing(self):
        self.client.force_authenticate(self.regular_user)

        res = self.client.patch(self._detail_url(), {"name": "Hijacked"}, format="json")

        self.assertEqual(res.status_code, 403)
        self.space.refresh_from_db()
        self.assertEqual(self.space.name, "Existing Space")

    def test_a_regular_authenticated_user_cannot_delete_any_listing(self):
        self.client.force_authenticate(self.regular_user)

        res = self.client.delete(self._detail_url())

        self.assertEqual(res.status_code, 403)
        self.assertTrue(CoWorkingSpace.objects.filter(id=self.space.id).exists())

    def test_a_regular_authenticated_user_cannot_create_a_listing(self):
        self.client.force_authenticate(self.regular_user)

        res = self.client.post(
            LIST_URL, {"name": "New Space", "address": "2 Main St", "city": "Abuja", "country": "NG"}, format="json",
        )

        self.assertEqual(res.status_code, 403)

    def test_staff_can_modify_a_listing(self):
        self.client.force_authenticate(self.staff_user)

        res = self.client.patch(self._detail_url(), {"name": "Updated by staff"}, format="json")

        self.assertEqual(res.status_code, 200)
        self.space.refresh_from_db()
        self.assertEqual(self.space.name, "Updated by staff")
