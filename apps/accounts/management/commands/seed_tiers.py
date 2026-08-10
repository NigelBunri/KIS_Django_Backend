"""
Idempotent, self-healing management command to seed AccountTier
(apps.accounts) records.

Previously this command maintained its OWN hardcoded tier table (different
names — "Basic" instead of "Free" — different currency (XAF vs USD),
different feature keys) completely independent from apps.accounts.
tier_presets.TIER_PRESETS, which is what actually auto-seeds AccountTier on
every /api/v1/tiers/ request. Running this command was a live
data-corruption hazard: for tier names that happened to match
(Pro/Business/Business Pro/Partner/Partner Pro) it would silently overwrite
the correct USD-priced, tier_presets-sourced row with the wrong
XAF-denominated one; for "Free" vs "Basic" it would create a second,
duplicate free-tier row instead of updating the existing one.

It also seeded apps.tiers.BillingPlan/Entitlement — a disconnected,
unexposed parallel billing system (see apps/tiers/models.py's deprecation
notice) that this command has no reason to keep populating.

This command is now a thin, explicit wrapper around the single source of
truth: apps.accounts.tiers.ensure_default_account_tiers(), which reads
TIER_PRESETS and self-heals any drift on every call.

Usage:
    python manage.py seed_tiers
    python manage.py seed_tiers --dry-run
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Seed/self-heal AccountTier records from apps.accounts.tier_presets. "
        "Idempotent — safe to run multiple times."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            dest='dry_run',
            default=False,
            help='Print what would change without writing to the database.',
        )

    def handle(self, *args, **options):
        from apps.accounts.models import AccountTier
        from apps.accounts.tier_presets import TIER_PRESETS
        from apps.accounts.tiers import ensure_default_account_tiers

        dry_run: bool = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes will be written.\n'))
            existing = {
                (tier.name or "").strip().lower(): tier for tier in AccountTier.objects.all()
            }
            for preset in TIER_PRESETS:
                key = (preset["name"] or "").strip().lower()
                current = existing.get(key)
                desired = {
                    "price_cents": preset["price_cents"],
                    "features_json": preset["features_json"],
                    "rank": preset.get("rank", 0),
                    "billing_period_days": preset.get("billing_period_days", 30),
                }
                if current is None:
                    action = "CREATE"
                elif any(getattr(current, field) != value for field, value in desired.items()):
                    action = "UPDATE"
                else:
                    action = "NO CHANGE"
                self.stdout.write(
                    f"  [{action}] {preset['name']}  price_cents={desired['price_cents']}  rank={desired['rank']}"
                )
            return

        before = {
            (tier.name or "").strip().lower(): (tier.price_cents, tier.rank, tier.features_json)
            for tier in AccountTier.objects.all()
        }
        ensure_default_account_tiers()
        after_names = set(AccountTier.objects.values_list("name", flat=True))

        created, updated, unchanged = 0, 0, 0
        for preset in TIER_PRESETS:
            key = (preset["name"] or "").strip().lower()
            if key not in before:
                created += 1
                self.stdout.write(f"  [CREATED] {preset['name']}")
            elif before[key] != (
                preset["price_cents"], preset.get("rank", 0), preset["features_json"],
            ):
                updated += 1
                self.stdout.write(f"  [UPDATED] {preset['name']}")
            else:
                unchanged += 1
                self.stdout.write(f"  [NO CHANGE] {preset['name']}")

        self.stdout.write(
            self.style.SUCCESS(
                f"AccountTier: {created} created, {updated} updated, {unchanged} unchanged. "
                f"Total canonical tiers present: {len(after_names & {p['name'] for p in TIER_PRESETS})}/{len(TIER_PRESETS)}."
            )
        )
