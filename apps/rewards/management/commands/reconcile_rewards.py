# apps/rewards/management/commands/reconcile_rewards.py
"""Manual/cron fallback for the read-only reward/referral consistency
check.

Wraps the exact same function the Celery task
(apps.rewards.tasks.reconcile_rewards_and_referrals_task) calls. Never
writes anything to the database — every anomaly found is printed (and
logged via logger.warning by the underlying function) for an operator to
review and, if needed, act on manually.

Usage:
    python manage.py reconcile_rewards
    python manage.py reconcile_rewards --limit 100
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Read-only consistency check across Referral/RewardLedgerEntry/WalletTransaction."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=1000,
            help="Maximum number of rows to check per category in this run (default: 1000).",
        )

    def handle(self, *args, **options):
        from apps.rewards.services import reconcile_rewards_and_referrals

        result = reconcile_rewards_and_referrals(limit=options["limit"])
        for anomaly in result["anomalies"]:
            self.stdout.write(self.style.WARNING(f"  [ANOMALY] {anomaly}"))

        checked = result["checked"]
        self.stdout.write(
            self.style.SUCCESS(
                f"Checked {sum(checked.values())} row(s) across "
                f"{len(checked)} categor(y/ies): {len(result['anomalies'])} anomaly/anomalies found."
            )
        )
