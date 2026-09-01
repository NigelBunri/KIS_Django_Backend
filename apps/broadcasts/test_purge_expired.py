"""
purge_expired_broadcasts_task - cleanup_expired_broadcast_items was a
real, working function with its own management command
(purge_expired_broadcasts) but was never actually wired into
CELERY_BEAT_SCHEDULE, so expired BroadcastItem rows only got cleaned up
if someone remembered to run the command by hand.

Run:
  python3 manage.py test apps.broadcasts.test_purge_expired --keepdb -v 2
"""
import datetime

from django.test import TestCase
from django.utils import timezone

from .models import BroadcastItem, BroadcastSourceType
from .tasks import purge_expired_broadcasts_task


class PurgeExpiredBroadcastsTaskTests(TestCase):
    def test_deletes_items_past_their_expiry(self):
        BroadcastItem.objects.create(
            source_type=BroadcastSourceType.COMMUNITY_POST,
            source_id="post-1",
            expires_at=timezone.now() - datetime.timedelta(days=1),
        )

        result = purge_expired_broadcasts_task()

        self.assertEqual(result["deleted"], 1)
        self.assertEqual(BroadcastItem.objects.count(), 0)

    def test_leaves_items_not_yet_expired(self):
        BroadcastItem.objects.create(
            source_type=BroadcastSourceType.COMMUNITY_POST,
            source_id="post-2",
            expires_at=timezone.now() + datetime.timedelta(days=1),
        )

        result = purge_expired_broadcasts_task()

        self.assertEqual(result["deleted"], 0)
        self.assertEqual(BroadcastItem.objects.count(), 1)
