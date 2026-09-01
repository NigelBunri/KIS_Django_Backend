"""
BroadcastItem's specialized-category (market/education) 1-year lifecycle -
previously every category got the same 10-day expires_at regardless of
what it was, even though nearly every creation call site independently
re-implements "now + 10 days" in Python. See
LONG_LIVED_BROADCAST_SOURCE_TYPES/BroadcastItem.save() in models.py.

Run:
  python3 manage.py test apps.broadcasts.test_long_lived_categories --keepdb -v 2
"""
import datetime

from django.test import TestCase
from django.utils import timezone

from .models import BroadcastItem, BroadcastSourceType


class LongLivedCategoryExpiryTests(TestCase):
    def test_market_product_gets_a_one_year_lifecycle_even_when_caller_passes_ten_days(self):
        item = BroadcastItem.objects.create(
            source_type=BroadcastSourceType.MARKET_PRODUCT,
            source_id="product-1",
            expires_at=timezone.now() + datetime.timedelta(days=10),
        )

        expected = timezone.now() + datetime.timedelta(days=365)
        self.assertLess(abs((item.expires_at - expected).total_seconds()), 5)

    def test_education_course_gets_a_one_year_lifecycle(self):
        item = BroadcastItem.objects.create(
            source_type=BroadcastSourceType.EDUCATION_COURSE,
            source_id="course-1",
            expires_at=timezone.now() + datetime.timedelta(days=10),
        )

        expected = timezone.now() + datetime.timedelta(days=365)
        self.assertLess(abs((item.expires_at - expected).total_seconds()), 5)

    def test_community_post_keeps_the_ordinary_ten_day_lifecycle(self):
        explicit_expiry = timezone.now() + datetime.timedelta(days=10)
        item = BroadcastItem.objects.create(
            source_type=BroadcastSourceType.COMMUNITY_POST,
            source_id="post-1",
            expires_at=explicit_expiry,
        )

        self.assertEqual(item.expires_at, explicit_expiry)

    def test_updating_an_existing_market_item_does_not_recompute_its_expiry(self):
        item = BroadcastItem.objects.create(
            source_type=BroadcastSourceType.MARKET_PRODUCT,
            source_id="product-2",
            expires_at=timezone.now() + datetime.timedelta(days=10),
        )
        original_expiry = item.expires_at

        item.metadata = {"updated": True}
        item.save()

        item.refresh_from_db()
        self.assertEqual(item.expires_at, original_expiry)

    def test_rebroadcasting_via_update_or_create_refreshes_the_lifecycle(self):
        BroadcastItem.objects.update_or_create(
            source_type=BroadcastSourceType.MARKET_PRODUCT,
            source_id="product-3",
            defaults={"expires_at": timezone.now() + datetime.timedelta(days=10)},
        )
        first = BroadcastItem.objects.get(source_type=BroadcastSourceType.MARKET_PRODUCT, source_id="product-3")
        first.expires_at = timezone.now() - datetime.timedelta(days=1)
        first.save()

        new_expiry = timezone.now() + datetime.timedelta(days=10)
        BroadcastItem.objects.update_or_create(
            source_type=BroadcastSourceType.MARKET_PRODUCT,
            source_id="product-3",
            defaults={"expires_at": new_expiry},
        )

        second = BroadcastItem.objects.get(source_type=BroadcastSourceType.MARKET_PRODUCT, source_id="product-3")
        self.assertEqual(second.id, first.id)
        self.assertEqual(second.expires_at, new_expiry)
