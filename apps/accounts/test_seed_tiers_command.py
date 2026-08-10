"""
Regression tests for the rewritten seed_tiers management command.

Previously this command maintained a second, independently-hardcoded tier
table (different names/currency/features than apps.accounts.tier_presets)
and would silently corrupt already-correctly-seeded AccountTier rows on
rerun. It's now a thin wrapper around ensure_default_account_tiers() — the
single source of truth — and no longer touches apps.tiers at all.

Run:
  python3 manage.py test apps.accounts.test_seed_tiers_command --keepdb -v 2
"""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from .models import AccountTier
from .tier_presets import TIER_PRESETS


class SeedTiersCommandTests(TestCase):
    def setUp(self):
        AccountTier.objects.all().delete()

    def test_creates_all_six_canonical_tiers_from_scratch(self):
        call_command("seed_tiers", stdout=StringIO())
        names = set(AccountTier.objects.values_list("name", flat=True))
        self.assertEqual(names, {p["name"] for p in TIER_PRESETS})

    def test_seeded_tiers_match_tier_presets_exactly(self):
        call_command("seed_tiers", stdout=StringIO())
        for preset in TIER_PRESETS:
            tier = AccountTier.objects.get(name=preset["name"])
            self.assertEqual(tier.price_cents, preset["price_cents"])
            self.assertEqual(tier.rank, preset["rank"])
            self.assertEqual(tier.features_json, preset["features_json"])

    def test_rerunning_does_not_create_duplicates(self):
        call_command("seed_tiers", stdout=StringIO())
        call_command("seed_tiers", stdout=StringIO())
        self.assertEqual(AccountTier.objects.count(), len(TIER_PRESETS))

    def test_rerunning_heals_manually_corrupted_data(self):
        call_command("seed_tiers", stdout=StringIO())
        pro = AccountTier.objects.get(name="Pro")
        pro.price_cents = 1
        pro.rank = 0
        pro.save(update_fields=["price_cents", "rank"])

        call_command("seed_tiers", stdout=StringIO())

        pro.refresh_from_db()
        self.assertEqual(pro.price_cents, 1000)
        self.assertEqual(pro.rank, 1)

    def test_dry_run_makes_no_database_changes(self):
        out = StringIO()
        call_command("seed_tiers", "--dry-run", stdout=out)
        self.assertEqual(AccountTier.objects.count(), 0)
        self.assertIn("DRY RUN", out.getvalue())
        self.assertIn("CREATE", out.getvalue())

    def test_does_not_touch_apps_tiers_billing_plan(self):
        from apps.tiers.models import BillingPlan
        BillingPlan.objects.all().delete()
        call_command("seed_tiers", stdout=StringIO())
        self.assertEqual(BillingPlan.objects.count(), 0)

    def test_no_longer_creates_a_basic_named_duplicate_of_free(self):
        call_command("seed_tiers", stdout=StringIO())
        call_command("seed_tiers", stdout=StringIO())
        free_like = AccountTier.objects.filter(name__iexact="Basic")
        self.assertFalse(free_like.exists())
        self.assertEqual(AccountTier.objects.filter(name__iexact="Free").count(), 1)
