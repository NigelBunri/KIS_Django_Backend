# apps/referrals/management/commands/settle_referral_commissions.py
"""Manual/cron fallback for the lifetime cash-commission sweeps
(apps.referrals.commission_services) — the commission-track equivalent of
confirm_settled_referrals.py, which only ever touches the separate
one-time KIS-Coins track. Runs both steps in order: approve PENDING
commissions past the settlement window, then attempt payout for every
APPROVED commission with an ACTIVE payout account.

Usage:
    python manage.py settle_referral_commissions
    python manage.py settle_referral_commissions --skip-payout
    python manage.py settle_referral_commissions --limit 100
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Approve settlement-window-eligible referral commissions, then attempt payout for approved ones."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=500, help="Max rows per step (default: 500).")
        parser.add_argument(
            "--skip-payout", action="store_true", default=False,
            help="Only run the approval step; do not attempt any real provider payout calls.",
        )

    def handle(self, *args, **options):
        from apps.referrals.commission_services import (
            sweep_approvable_referral_commissions,
            sweep_payable_referral_commissions,
        )

        limit: int = options["limit"]
        approve_result = sweep_approvable_referral_commissions(limit=limit)
        self.stdout.write(
            self.style.SUCCESS(
                f"Approval sweep: {approve_result['candidates']} candidate(s), "
                f"{approve_result['approved']} approved, {approve_result['errors']} error(s)."
            )
        )

        if options["skip_payout"]:
            self.stdout.write("Skipping payout step (--skip-payout).")
            return

        payout_result = sweep_payable_referral_commissions(limit=limit)
        self.stdout.write(
            self.style.SUCCESS(
                f"Payout sweep: {payout_result['candidates']} candidate(s), "
                f"{payout_result['paid']} paid, {payout_result['initiated']} initiated "
                f"(awaiting provider confirmation), {payout_result['skipped']} skipped "
                f"(no active payout account), {payout_result['errors']} error(s)."
            )
        )
