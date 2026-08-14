"""
Idempotent, self-healing management command to seed/correct
ReferralRateConfig rows from apps.referrals.services.RATE_PERCENT_BY_TIER_RANK.

AccountTier rows are themselves lazily self-healed on first /api/v1/tiers/
access (or via `manage.py seed_tiers`) rather than at migrate time, so the
one-time data migration that ships with this feature
(0003_seed_referral_rate_config) may run before any AccountTier rows exist
and correctly no-op. Run this command (any time, any number of times) to
backfill/correct rates once tiers are present — mirrors the existing
`seed_tiers` command's own "thin wrapper around a self-healing service
function" shape exactly.

Usage:
    python manage.py seed_referral_rates
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Seed/self-heal ReferralRateConfig records from "
        "apps.referrals.services.RATE_PERCENT_BY_TIER_RANK. Idempotent — "
        "safe to run multiple times."
    )

    def handle(self, *args, **options):
        from apps.referrals.services import ensure_referral_rate_configs

        result = ensure_referral_rate_configs()
        self.stdout.write(
            self.style.SUCCESS(
                f"ReferralRateConfig: {result['created']} created, "
                f"{result['updated']} updated, {result['unchanged']} unchanged."
            )
        )
