# apps/billing/management/commands/reconcile_wallet_transactions.py
"""Manual/cron fallback and administrative repair tool for stale pending
WalletTransaction processing.

Wraps the exact same function the Celery task
(apps.billing.tasks.expire_stale_pending_wallet_transactions) calls — this
command exists for deployments that don't run Celery Beat, and as an
on-demand repair tool an operator can run (with --dry-run first) to see
and fix any tier-upgrade checkout that was abandoned/cancelled without
ever being processed. Safe to run repeatedly; only ever acts on a
WalletTransaction still "pending" past its stale-timeout.

Usage:
    python manage.py reconcile_wallet_transactions
    python manage.py reconcile_wallet_transactions --dry-run
    python manage.py reconcile_wallet_transactions --limit 100
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.billing.services import WALLET_TRANSACTION_STALE_PENDING_TIMEOUT


class Command(BaseCommand):
    help = "Cancel WalletTransaction rows stuck 'pending' past the stale-checkout timeout."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=500,
            help="Maximum number of transactions to process in this run (default: 500).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="Print what would be cancelled without writing to the database.",
        )

    def handle(self, *args, **options):
        from apps.billing.models import WalletTransaction

        limit: int = options["limit"]
        dry_run: bool = options["dry_run"]
        cutoff = timezone.now() - WALLET_TRANSACTION_STALE_PENDING_TIMEOUT

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be written.\n"))
            candidates = (
                WalletTransaction.objects.filter(status="pending", created_at__lte=cutoff)
                .order_by("created_at")[:limit]
            )
            count = 0
            for tx in candidates:
                count += 1
                self.stdout.write(
                    f"  [WOULD CANCEL] transaction={tx.id} user={tx.user_id} "
                    f"amount_cents={tx.amount_cents} tx_ref={tx.tx_ref}  "
                    f"created_at={tx.created_at.isoformat()}"
                )
            if count == 0:
                self.stdout.write("  (nothing to do)")
            self.stdout.write(self.style.SUCCESS(f"\nWould cancel {count} transaction(s)."))
            return

        from apps.billing.services import sweep_stale_pending_wallet_transactions

        result = sweep_stale_pending_wallet_transactions(limit=limit)
        self.stdout.write(
            self.style.SUCCESS(
                f"Reconciled {result['candidates']} candidate(s): "
                f"{result['finalized']} finalized, {result['errors']} error(s)."
            )
        )
