# apps/rewards/management/commands/expire_reward_entries.py
"""Manual/cron fallback for expiring lapsed KIS Coins.

Wraps the exact same function the Celery task
(apps.rewards.tasks.expire_reward_ledger_entries) calls — this command
exists for deployments that don't run Celery Beat, and as an on-demand
tool an operator can run (with --dry-run first). Safe to run repeatedly;
only ever acts on a RewardLedgerEntry still CONFIRMED past its own
expires_at.

Usage:
    python manage.py expire_reward_entries
    python manage.py expire_reward_entries --dry-run
    python manage.py expire_reward_entries --limit 100
"""
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Expire (compensate) CONFIRMED KIS Coins entries past their expires_at."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=500,
            help="Maximum number of entries to process in this run (default: 500).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="Print what would be expired without writing to the database.",
        )

    def handle(self, *args, **options):
        from apps.rewards.models import RewardLedgerEntry

        limit: int = options["limit"]
        dry_run: bool = options["dry_run"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be written.\n"))
            candidates = (
                RewardLedgerEntry.objects.filter(
                    status=RewardLedgerEntry.STATUS_CONFIRMED,
                    expires_at__isnull=False,
                    expires_at__lte=timezone.now(),
                )
                .select_related("user")
                .order_by("expires_at")[:limit]
            )
            count = 0
            for entry in candidates:
                count += 1
                self.stdout.write(
                    f"  [WOULD EXPIRE] entry={entry.id} user={entry.user_id} "
                    f"amount={entry.amount:+d}  expires_at={entry.expires_at.isoformat()}"
                )
            if count == 0:
                self.stdout.write("  (nothing to do)")
            self.stdout.write(self.style.SUCCESS(f"\nWould expire {count} entrie(s)."))
            return

        from apps.rewards.services import expire_reward_entries

        result = expire_reward_entries(limit=limit)
        self.stdout.write(
            self.style.SUCCESS(
                f"Reconciled {result['candidates']} candidate(s): "
                f"{result['expired']} expired, {result['errors']} error(s)."
            )
        )
