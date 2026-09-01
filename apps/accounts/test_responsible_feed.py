"""
Responsible-engagement daily feed limit (2h/day default) - see
apps.accounts.responsible_feed and apps.broadcasts.views.BroadcastFeedView
.get, the only endpoint that actually checks it. Server-authoritative:
usage is reconstructed from the gap between successive heartbeat request
timestamps as measured by the server's own clock, never anything the
client reports, so it survives a manipulated device clock, an app
reinstall, or a logout/login.

Run:
  python3 manage.py test apps.accounts.test_responsible_feed --keepdb -v 2
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import DailyFeedUsage, FeedEngagementState
from apps.accounts.responsible_feed import (
    feed_usage_status,
    get_today_feed_status,
    record_feed_heartbeat,
)

User = get_user_model()

HEARTBEAT_URL = "/api/v1/engagement/feed-heartbeat/"
STATUS_URL = "/api/v1/engagement/feed-status/"


class FeedUsageStatusTests(TestCase):
    @override_settings(RESPONSIBLE_FEED_DAILY_LIMIT_SECONDS=7200)
    def test_status_shape(self):
        result = feed_usage_status(1800)

        self.assertEqual(result["seconds_consumed"], 1800)
        self.assertEqual(result["limit_seconds"], 7200)
        self.assertEqual(result["seconds_remaining"], 5400)
        self.assertFalse(result["limit_reached"])

    @override_settings(RESPONSIBLE_FEED_DAILY_LIMIT_SECONDS=7200)
    def test_limit_reached_at_exactly_the_limit(self):
        result = feed_usage_status(7200)

        self.assertTrue(result["limit_reached"])
        self.assertEqual(result["seconds_remaining"], 0)

    @override_settings(RESPONSIBLE_FEED_DAILY_LIMIT_SECONDS=7200)
    def test_over_limit_does_not_go_negative(self):
        result = feed_usage_status(9999)

        self.assertEqual(result["seconds_remaining"], 0)
        self.assertTrue(result["limit_reached"])


class RecordFeedHeartbeatTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(phone="+2348800000001", password="pw123456", country="NG")

    def test_first_ever_heartbeat_credits_nothing(self):
        result = record_feed_heartbeat(self.user)

        self.assertEqual(result["seconds_consumed"], 0)
        state = FeedEngagementState.objects.get(user=self.user)
        self.assertIsNotNone(state.last_heartbeat_at)

    @override_settings(RESPONSIBLE_FEED_MAX_HEARTBEAT_GAP_SECONDS=90)
    def test_credits_the_real_gap_between_heartbeats(self):
        record_feed_heartbeat(self.user)
        state = FeedEngagementState.objects.get(user=self.user)
        state.last_heartbeat_at = timezone.now() - datetime.timedelta(seconds=20)
        state.save(update_fields=["last_heartbeat_at"])

        result = record_feed_heartbeat(self.user)

        self.assertGreaterEqual(result["seconds_consumed"], 19)
        self.assertLessEqual(result["seconds_consumed"], 21)

    @override_settings(RESPONSIBLE_FEED_MAX_HEARTBEAT_GAP_SECONDS=90)
    def test_clamps_a_long_gap_to_the_configured_maximum(self):
        record_feed_heartbeat(self.user)
        state = FeedEngagementState.objects.get(user=self.user)
        state.last_heartbeat_at = timezone.now() - datetime.timedelta(hours=5)
        state.save(update_fields=["last_heartbeat_at"])

        result = record_feed_heartbeat(self.user)

        self.assertEqual(result["seconds_consumed"], 90)

    def test_heartbeats_accumulate_across_calls(self):
        for _ in range(3):
            record_feed_heartbeat(self.user)
            state = FeedEngagementState.objects.get(user=self.user)
            state.last_heartbeat_at = timezone.now() - datetime.timedelta(seconds=10)
            state.save(update_fields=["last_heartbeat_at"])

        usage = DailyFeedUsage.objects.get(user=self.user, date=timezone.now().date())
        self.assertGreaterEqual(usage.seconds_consumed, 20)

    def test_ignores_any_client_supplied_elapsed_time(self):
        # record_feed_heartbeat doesn't accept a client value at all -
        # this documents that guarantee at the call-signature level rather
        # than just in prose.
        import inspect

        sig = inspect.signature(record_feed_heartbeat)
        self.assertEqual(list(sig.parameters.keys()), ["user"])

    def test_survives_a_backwards_device_clock(self):
        # A manipulated/rolled-back client clock has no bearing here at
        # all - record_feed_heartbeat never reads anything from the
        # request other than "a request happened". Simulate a client with
        # a clock skewed into the past by asserting the server timestamp
        # written is close to the real now(), regardless.
        before = timezone.now()
        record_feed_heartbeat(self.user)
        after = timezone.now()

        state = FeedEngagementState.objects.get(user=self.user)
        self.assertGreaterEqual(state.last_heartbeat_at, before)
        self.assertLessEqual(state.last_heartbeat_at, after)


class FeedHeartbeatEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(phone="+2348800000002", password="pw123456", country="NG")
        self.client.force_authenticate(self.user)

    def test_heartbeat_requires_auth(self):
        anon_client = APIClient()

        res = anon_client.post(HEARTBEAT_URL, {}, format="json")

        self.assertEqual(res.status_code, 401)

    def test_heartbeat_ignores_a_spoofed_elapsed_seconds_in_the_body(self):
        res = self.client.post(HEARTBEAT_URL, {"seconds": 999999}, format="json")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["seconds_consumed"], 0)

    def test_status_endpoint_reflects_recorded_usage(self):
        record_feed_heartbeat(self.user)
        state = FeedEngagementState.objects.get(user=self.user)
        state.last_heartbeat_at = timezone.now() - datetime.timedelta(seconds=30)
        state.save(update_fields=["last_heartbeat_at"])
        record_feed_heartbeat(self.user)

        res = self.client.get(STATUS_URL)

        self.assertEqual(res.status_code, 200)
        self.assertGreater(res.data["seconds_consumed"], 0)


@override_settings(RESPONSIBLE_FEED_DAILY_LIMIT_SECONDS=60)
class BroadcastFeedLimitEnforcementTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(phone="+2348800000003", password="pw123456", country="NG")
        self.client.force_authenticate(self.user)

    def test_feed_returns_content_when_under_the_limit(self):
        res = self.client.get("/api/v1/broadcasts/")

        self.assertEqual(res.status_code, 200)
        self.assertNotIn("feed_limit", res.data)

    def test_feed_returns_empty_with_a_flag_once_the_limit_is_reached(self):
        DailyFeedUsage.objects.create(user=self.user, date=timezone.now().date(), seconds_consumed=60)

        res = self.client.get("/api/v1/broadcasts/")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["results"], [])
        self.assertTrue(res.data["feed_limit"]["limit_reached"])

    def test_messaging_is_unaffected_by_the_feed_limit(self):
        DailyFeedUsage.objects.create(user=self.user, date=timezone.now().date(), seconds_consumed=60)

        res = self.client.get("/api/v1/conversations/")

        self.assertEqual(res.status_code, 200)

    def test_own_profile_is_unaffected_by_the_feed_limit(self):
        DailyFeedUsage.objects.create(user=self.user, date=timezone.now().date(), seconds_consumed=60)

        res = self.client.get(f"/api/v1/users/{self.user.id}/")

        self.assertEqual(res.status_code, 200)

    def test_a_new_day_resets_the_limit(self):
        yesterday = timezone.now().date() - datetime.timedelta(days=1)
        DailyFeedUsage.objects.create(user=self.user, date=yesterday, seconds_consumed=999)

        res = self.client.get("/api/v1/broadcasts/")

        self.assertEqual(res.status_code, 200)
        self.assertNotIn("feed_limit", res.data)

    def test_limit_status_is_keyed_by_server_date_not_a_client_supplied_one(self):
        # get_today_feed_status takes no date argument at all - there is
        # no way for a request to claim a different "today".
        import inspect

        sig = inspect.signature(get_today_feed_status)
        self.assertEqual(list(sig.parameters.keys()), ["user"])
