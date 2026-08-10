# apps/billing/management/commands/reconcile_subscriptions.py
"""Manual/cron fallback and administrative repair tool for subscription
expiry processing.

Wraps the exact same function the Celery task
(apps.billing.tasks.expire_subscriptions) calls — this command exists for
deployments that don't run Celery Beat, and as an on-demand repair tool an
operator can run (with --dry-run first) to see and fix any subscription
that lapsed without being processed. Safe to run repeatedly; only ever
acts on a Subscription still STATUS_ACTIVE past its own ends_at.

Usage:
    python manage.py reconcile_subscriptions
    python manage.py reconcile_subscriptions --dry-run
    python manage.py reconcile_subscriptions --limit 100
"""
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Finalize (expire) subscriptions past their ends_at that are still marked active."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=500,
            help="Maximum number of subscriptions to process in this run (default: 500).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="Print what would be finalized without writing to the database.",
        )

    def handle(self, *args, **options):
        from apps.accounts.models import Subscription

        limit: int = options["limit"]
        dry_run: bool = options["dry_run"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be written.\n"))
            candidates = (
                Subscription.objects.filter(
                    status=Subscription.STATUS_ACTIVE,
                    ends_at__isnull=False,
                    ends_at__lte=timezone.now(),
                )
                .select_related("user", "tier", "pending_tier")
                .order_by("ends_at")[:limit]
            )
            count = 0
            for sub in candidates:
                count += 1
                target = sub.pending_tier.name if sub.pending_tier else "Free"
                reason = "scheduled downgrade/cancel" if sub.cancel_at_period_end else "natural lapse (no action taken)"
                self.stdout.write(
                    f"  [WOULD EXPIRE] subscription={sub.id} user={sub.user_id} "
                    f"tier={sub.tier.name if sub.tier else '?'} -> {target}  "
                    f"ends_at={sub.ends_at.isoformat()}  reason={reason}"
                )
            if count == 0:
                self.stdout.write("  (nothing to do)")
            self.stdout.write(self.style.SUCCESS(f"\nWould finalize {count} subscription(s)."))
            return

        from apps.billing.services import sweep_expired_subscriptions

        result = sweep_expired_subscriptions(limit=limit)
        self.stdout.write(
            self.style.SUCCESS(
                f"Reconciled {result['candidates']} candidate(s): "
                f"{result['finalized']} finalized, {result['errors']} error(s)."
            )
        )
