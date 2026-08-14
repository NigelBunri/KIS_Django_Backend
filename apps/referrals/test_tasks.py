from __future__ import annotations

from django.test import SimpleTestCase, TestCase

from apps.referrals.tasks import confirm_settled_referrals


class ReferralTaskRegistrationTests(SimpleTestCase):
    """Same check verify_celery_launch's _beat_schedule_tasks_are_registered
    performs for every CELERY_BEAT_SCHEDULE entry: importable, and its
    .name matches the dotted path used to register it."""

    def test_task_is_registered_with_expected_name(self):
        self.assertEqual(
            confirm_settled_referrals.name,
            "apps.referrals.tasks.confirm_settled_referrals",
        )


class ReferralTaskBehaviorTests(TestCase):
    """Calling a Celery task directly (not via .delay()) runs its body
    synchronously — this exercises sweep_settleable_referrals through the
    task wrapper, on an empty referrals table."""

    def test_task_runs_clean_with_no_referrals(self):
        result = confirm_settled_referrals()
        self.assertEqual(result, {"candidates": 0, "settled": 0, "errors": 0})
