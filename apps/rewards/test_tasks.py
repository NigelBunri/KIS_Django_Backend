from __future__ import annotations

from django.test import SimpleTestCase, TestCase

from apps.rewards.tasks import (
    expire_reward_ledger_entries,
    reconcile_rewards_and_referrals_task,
)


class RewardTaskRegistrationTests(SimpleTestCase):
    """Same check verify_celery_launch's _beat_schedule_tasks_are_registered
    performs for every CELERY_BEAT_SCHEDULE entry: importable, and its
    .name matches the dotted path used to register it."""

    def test_expire_task_is_registered_with_expected_name(self):
        self.assertEqual(
            expire_reward_ledger_entries.name,
            "apps.rewards.tasks.expire_reward_ledger_entries",
        )

    def test_reconcile_task_is_registered_with_expected_name(self):
        self.assertEqual(
            reconcile_rewards_and_referrals_task.name,
            "apps.rewards.tasks.reconcile_rewards_and_referrals_task",
        )


class RewardTaskBehaviorTests(TestCase):
    """Calling a Celery task directly (not via .delay()) runs its body
    synchronously — this exercises the real services.py functions through
    the task wrapper, on an empty ledger."""

    def test_expire_task_runs_clean_on_empty_ledger(self):
        result = expire_reward_ledger_entries()
        self.assertEqual(result, {"candidates": 0, "expired": 0, "errors": 0})

    def test_reconcile_task_runs_clean_on_empty_ledger(self):
        result = reconcile_rewards_and_referrals_task()
        self.assertEqual(result["anomalies"], [])
