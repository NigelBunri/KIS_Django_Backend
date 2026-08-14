# apps/referrals/management/commands/confirm_settled_referrals.py
"""Manual/cron fallback for settling qualified referrals.

Wraps the exact same function the Celery task
(apps.referrals.tasks.confirm_settled_referrals) calls — this command
exists for deployments that don't run Celery Beat, and as an on-demand
tool an operator can run (with --dry-run first). Safe to run repeatedly;
only ever acts on a Referral still QUALIFIED past
REFERRAL_SETTLEMENT_WINDOW_DAYS.

Usage:
    python manage.py confirm_settled_referrals
    python manage.py confirm_settled_referrals --dry-run
    python manage.py confirm_settled_referrals --limit 100
"""
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Settle (REWARDED) QUALIFIED referrals past the settlement window."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=500,
            help="Maximum number of referrals to process in this run (default: 500).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="Print what would be settled without writing to the database.",
        )

    def handle(self, *args, **options):
        from apps.referrals.models import Referral
        from apps.referrals.services import REFERRAL_SETTLEMENT_WINDOW_DAYS

        limit: int = options["limit"]
        dry_run: bool = options["dry_run"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be written.\n"))
            cutoff = timezone.now() - timezone.timedelta(days=REFERRAL_SETTLEMENT_WINDOW_DAYS)
            candidates = (
                Referral.objects.filter(
                    status=Referral.STATUS_QUALIFIED,
                    qualified_at__isnull=False,
                    qualified_at__lte=cutoff,
                )
                .select_related("referrer")
                .order_by("qualified_at")[:limit]
            )
            count = 0
            for referral in candidates:
                count += 1
                self.stdout.write(
                    f"  [WOULD SETTLE] referral={referral.id} referrer={referral.referrer_id} "
                    f"qualified_at={referral.qualified_at.isoformat()}"
                )
            if count == 0:
                self.stdout.write("  (nothing to do)")
            self.stdout.write(self.style.SUCCESS(f"\nWould settle {count} referral(s)."))
            return

        from apps.referrals.services import sweep_settleable_referrals

        result = sweep_settleable_referrals(limit=limit)
        self.stdout.write(
            self.style.SUCCESS(
                f"Reconciled {result['candidates']} candidate(s): "
                f"{result['settled']} settled, {result['errors']} error(s)."
            )
        )
