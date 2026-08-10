"""
Tests for Phase 5's Celery/scheduled-jobs infrastructure: the
verify_celery_launch guardrail command, and the CELERY_TASK_ALWAYS_EAGER
test-mode wiring it depends on to be testable without a real worker.

Run:
  python3 manage.py test apps.core.test_verify_celery_launch --keepdb -v 2
"""
import json
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings


class VerifyCeleryLaunchCommandTests(TestCase):
    def test_passes_default_local_guardrails(self):
        out = StringIO()
        call_command("verify_celery_launch", stdout=out)
        output = out.getvalue()
        self.assertIn("Celery infrastructure guardrails ready: True", output)
        self.assertIn("CELERY_BEAT_SCHEDULER", output)
        self.assertIn("django_celery_beat.schedulers:DatabaseScheduler", output)

    def test_every_configured_beat_schedule_task_path_actually_resolves(self):
        """Guards against a schedule entry pointing at a typo'd or renamed
        task path — exactly the class of bug this check exists to catch."""
        out = StringIO()
        call_command("verify_celery_launch", stdout=out)
        output = out.getvalue()
        for entry in settings.CELERY_BEAT_SCHEDULE:
            self.assertIn(f"PASS: beat_schedule:{entry}", output)

    def test_json_output_is_well_formed_and_reports_zero_failures(self):
        out = StringIO()
        call_command("verify_celery_launch", "--json", "--skip-ping", stdout=out)
        payload = json.loads(out.getvalue())
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["summary"]["failures"], 0)
        self.assertGreater(payload["summary"]["checks"], 0)

    def test_skip_ping_omits_the_worker_ping_check(self):
        out = StringIO()
        call_command("verify_celery_launch", "--json", "--skip-ping", stdout=out)
        payload = json.loads(out.getvalue())
        names = [c["name"] for c in payload["checks"]]
        self.assertNotIn("worker_ping", names)

    def test_a_broken_beat_schedule_task_path_fails_the_check(self):
        broken_schedule = {
            "bogus-entry": {"task": "apps.billing.tasks.this_task_does_not_exist", "schedule": 60},
        }
        with override_settings(CELERY_BEAT_SCHEDULE=broken_schedule):
            out = StringIO()
            call_command("verify_celery_launch", "--json", "--skip-ping", stdout=out)
            payload = json.loads(out.getvalue())
        self.assertFalse(payload["ready"])
        bogus_check = next(c for c in payload["checks"] if c["name"] == "beat_schedule:bogus-entry")
        self.assertEqual(bogus_check["state"], "fail")

    def test_missing_beat_scheduler_setting_fails_the_check(self):
        with override_settings(CELERY_BEAT_SCHEDULER=""):
            out = StringIO()
            call_command("verify_celery_launch", "--json", "--skip-ping", stdout=out)
            payload = json.loads(out.getvalue())
        self.assertFalse(payload["ready"])
        scheduler_check = next(c for c in payload["checks"] if c["name"] == "CELERY_BEAT_SCHEDULER")
        self.assertEqual(scheduler_check["state"], "fail")

    def test_unreachable_broker_fails_connectivity_but_does_not_crash(self):
        with override_settings(CELERY_BROKER_URL="redis://127.0.0.1:1/0"):
            out = StringIO()
            call_command("verify_celery_launch", "--json", "--skip-ping", stdout=out)
            payload = json.loads(out.getvalue())
        self.assertFalse(payload["ready"])
        broker_check = next(c for c in payload["checks"] if c["name"] == "broker_connectivity")
        self.assertEqual(broker_check["state"], "fail")

    def test_strict_flag_raises_when_a_blocker_is_present(self):
        with override_settings(CELERY_BEAT_SCHEDULER=""):
            with self.assertRaises(CommandError):
                call_command("verify_celery_launch", "--strict", "--skip-ping", stdout=StringIO())

    def test_strict_flag_does_not_raise_when_everything_passes(self):
        call_command("verify_celery_launch", "--strict", stdout=StringIO())

    def test_never_prints_the_broker_or_backend_url_value(self):
        # Deliberately 127.0.0.1, not a real external host: an unreachable
        # real host can silently drop packets instead of refusing the
        # connection, which depends on network egress behavior and can be
        # slow/flaky in a sandboxed CI environment. Loopback on a closed
        # port fails fast and deterministically everywhere.
        secret_looking_broker = "redis://user:supersecretpassword@127.0.0.1:1/0"
        with override_settings(CELERY_BROKER_URL=secret_looking_broker):
            out = StringIO()
            call_command("verify_celery_launch", "--skip-ping", stdout=out)
            output = out.getvalue()
        self.assertNotIn("supersecretpassword", output)


class CeleryTestModeEagerExecutionTests(TestCase):
    """Confirms the config/settings/local.py IS_TEST_RUN wiring actually
    makes .delay() run synchronously during tests — without this, any test
    that calls .delay() on a task would silently no-op (enqueue to Redis
    with nothing consuming it) rather than exercising the task's logic."""

    def test_always_eager_is_enabled_during_test_runs(self):
        self.assertTrue(settings.CELERY_TASK_ALWAYS_EAGER)
        self.assertTrue(settings.CELERY_TASK_EAGER_PROPAGATES)

    def test_delay_on_a_real_task_executes_synchronously_in_process(self):
        from apps.billing.tasks import expire_subscriptions

        async_result = expire_subscriptions.delay()
        self.assertTrue(async_result.successful())
        self.assertIn("candidates", async_result.result)
