"""
14-day testimony expiration - previously UserTestimony had no expiry
field at all, so a testimony stayed publicly listed forever unless the
author manually turned is_available off. See
apps.testimony.tasks.expire_stale_testimonies and
TestimonyDetailView.perform_update's fresh-lifecycle-on-re-enable logic.

Run:
  python3 manage.py test apps.testimony.test_expiration --keepdb -v 2
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import UserTestimony
from .tasks import expire_stale_testimonies

User = get_user_model()

LIST_URL = "/api/v1/testimonies/"


class ExpireStaleTestimoniesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(phone="+2348900000001", password="pw123456", country="NG")

    def _make(self, expires_at, is_available=True):
        return UserTestimony.objects.create(
            user=self.user, category="faith", title="My story", story="...",
            is_available=is_available, expires_at=expires_at,
        )

    def test_expires_a_testimony_past_its_expiry(self):
        testimony = self._make(timezone.now() - datetime.timedelta(days=1))

        expired_count = expire_stale_testimonies()

        self.assertEqual(expired_count, 1)
        testimony.refresh_from_db()
        self.assertFalse(testimony.is_available)
        self.assertIsNotNone(testimony.expired_at)

    def test_does_not_touch_the_story_or_endorsement_count(self):
        testimony = self._make(timezone.now() - datetime.timedelta(days=1))
        testimony.endorsement_count = 5
        testimony.save(update_fields=["endorsement_count"])

        expire_stale_testimonies()

        testimony.refresh_from_db()
        self.assertEqual(testimony.story, "...")
        self.assertEqual(testimony.endorsement_count, 5)
        self.assertTrue(UserTestimony.objects.filter(id=testimony.id).exists())

    def test_leaves_testimonies_not_yet_expired(self):
        testimony = self._make(timezone.now() + datetime.timedelta(days=1))

        expired_count = expire_stale_testimonies()

        self.assertEqual(expired_count, 0)
        testimony.refresh_from_db()
        self.assertTrue(testimony.is_available)

    def test_does_not_touch_a_testimony_already_unavailable(self):
        testimony = self._make(timezone.now() - datetime.timedelta(days=1), is_available=False)

        expired_count = expire_stale_testimonies()

        self.assertEqual(expired_count, 0)
        testimony.refresh_from_db()
        self.assertIsNone(testimony.expired_at)

    def test_expired_testimony_is_excluded_from_the_public_listing(self):
        self._make(timezone.now() - datetime.timedelta(days=1))
        expire_stale_testimonies()
        client = APIClient()
        client.force_authenticate(self.user)

        res = client.get(LIST_URL)

        self.assertEqual(res.status_code, 200)
        results = res.data["results"] if isinstance(res.data, dict) and "results" in res.data else res.data
        self.assertEqual(len(results), 0)


class TestimonyReactivationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(phone="+2348900000002", password="pw123456", country="NG")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_reactivating_an_expired_testimony_gives_it_a_fresh_expiry(self):
        testimony = UserTestimony.objects.create(
            user=self.user, category="faith", title="My story", story="...",
            is_available=False, expires_at=timezone.now() - datetime.timedelta(days=1),
            expired_at=timezone.now() - datetime.timedelta(days=1),
        )

        res = self.client.patch(f"{LIST_URL}{testimony.id}/", {"is_available": True}, format="json")

        self.assertEqual(res.status_code, 200)
        testimony.refresh_from_db()
        self.assertTrue(testimony.is_available)
        self.assertIsNone(testimony.expired_at)
        self.assertGreater(testimony.expires_at, timezone.now())

    def test_updating_an_already_available_testimony_does_not_reset_its_expiry(self):
        original_expiry = timezone.now() + datetime.timedelta(days=3)
        testimony = UserTestimony.objects.create(
            user=self.user, category="faith", title="My story", story="...",
            is_available=True, expires_at=original_expiry,
        )

        res = self.client.patch(f"{LIST_URL}{testimony.id}/", {"story": "updated"}, format="json")

        self.assertEqual(res.status_code, 200)
        testimony.refresh_from_db()
        self.assertEqual(testimony.expires_at, original_expiry)


class NewTestimonyDefaultExpiryTests(TestCase):
    def test_new_testimony_gets_a_fourteen_day_default_expiry(self):
        user = User.objects.create_user(phone="+2348900000003", password="pw123456", country="NG")

        testimony = UserTestimony.objects.create(
            user=user, category="faith", title="My story", story="...",
        )

        expected = timezone.now() + datetime.timedelta(days=14)
        self.assertLess(abs((testimony.expires_at - expected).total_seconds()), 5)
