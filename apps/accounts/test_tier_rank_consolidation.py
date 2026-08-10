"""
Regression tests for the tier-rank consolidation: previously three
independent implementations existed (apps.accounts.tiers._tier_weight —
list-index based; AccountTierSerializer._tier_rank and
apps.billing.views._tier_rank — both hand-rolled, order-dependent substring
matching on the tier name). All three now defer to the single
database-backed AccountTier.rank column via apps.accounts.tiers.tier_rank().

Run:
  python3 manage.py test apps.accounts.test_tier_rank_consolidation --keepdb -v 2
"""
from django.test import TestCase

from .models import AccountTier
from .serializers import AccountTierSerializer
from .tiers import (
    ensure_default_account_tiers,
    get_aggregated_tier_features,
    is_paid_tier_name,
    tier_rank,
)


class TierRankTests(TestCase):
    def setUp(self):
        AccountTier.objects.all().delete()
        self.free = AccountTier.objects.create(name="Rank Free", price_cents=0, rank=0, features_json={"a": 1})
        self.pro = AccountTier.objects.create(name="Rank Pro", price_cents=1000, rank=1, features_json={"b": 2})
        self.business = AccountTier.objects.create(name="Rank Business", price_cents=2500, rank=2, features_json={"c": 3})

    def test_tier_rank_reads_the_database_backed_column(self):
        self.assertEqual(tier_rank("Rank Free"), 0)
        self.assertEqual(tier_rank("Rank Pro"), 1)
        self.assertEqual(tier_rank("Rank Business"), 2)

    def test_tier_rank_is_case_insensitive(self):
        self.assertEqual(tier_rank("rank pro"), 1)
        self.assertEqual(tier_rank("RANK PRO"), 1)

    def test_tier_rank_applies_the_basic_to_free_alias(self):
        # apps.accounts.tiers.TIER_NAME_ALIASES maps "basic" -> "free"; a
        # literal "Free" row must resolve identically via either spelling.
        AccountTier.objects.filter(name="Rank Free").update(name="Free")
        self.assertEqual(tier_rank("basic"), tier_rank("free"))

    def test_tier_rank_falls_back_to_hierarchy_list_for_an_unmatched_name(self):
        # No DB row named this — must not raise, must return a stable,
        # "beyond all known tiers" sentinel rather than crashing.
        from .tiers import TIER_HIERARCHY
        self.assertEqual(tier_rank("Some Made Up Tier Name"), len(TIER_HIERARCHY))

    def test_is_paid_tier_name(self):
        self.assertFalse(is_paid_tier_name("Rank Free"))
        self.assertTrue(is_paid_tier_name("Rank Pro"))
        self.assertTrue(is_paid_tier_name("Rank Business"))
        self.assertFalse(is_paid_tier_name(""))
        self.assertFalse(is_paid_tier_name(None))

    def test_serializer_tier_rank_field_matches_the_db_column_directly(self):
        data = AccountTierSerializer(self.business).data
        self.assertEqual(data["tier_rank"], 2)
        self.assertEqual(data["tier_rank"], self.business.rank)

    def test_aggregated_features_are_cumulative_by_rank(self):
        features = get_aggregated_tier_features(self.business)
        # Business (rank 2) must inherit Free's (rank 0) and Pro's (rank 1)
        # features in addition to its own.
        self.assertEqual(features, {"a": 1, "b": 2, "c": 3})

    def test_aggregated_features_do_not_leak_higher_ranked_tiers(self):
        features = get_aggregated_tier_features(self.free)
        self.assertEqual(features, {"a": 1})
        self.assertNotIn("b", features)
        self.assertNotIn("c", features)


class EnsureDefaultAccountTiersSelfHealingTests(TestCase):
    """Regression test for a real bug found during this consolidation:
    ensure_default_account_tiers() used to short-circuit entirely once all
    6 tier NAMES existed, so a later change to TIER_PRESETS' price/features/
    rank never propagated to already-seeded rows — drift persisted forever."""

    def test_seeds_all_canonical_tiers_from_scratch(self):
        AccountTier.objects.all().delete()
        ensure_default_account_tiers()
        names = set(AccountTier.objects.values_list("name", flat=True))
        self.assertEqual(
            names, {"Free", "Pro", "Business", "Business Pro", "Partner", "Partner Pro"},
        )
        free = AccountTier.objects.get(name="Free")
        partner_pro = AccountTier.objects.get(name="Partner Pro")
        self.assertEqual(free.rank, 0)
        self.assertEqual(partner_pro.rank, 5)

    def test_self_heals_drifted_price_on_an_already_seeded_tier(self):
        ensure_default_account_tiers()
        pro = AccountTier.objects.get(name="Pro")
        pro.price_cents = 999999
        pro.save(update_fields=["price_cents"])

        ensure_default_account_tiers()

        pro.refresh_from_db()
        self.assertEqual(pro.price_cents, 1000)

    def test_self_heals_drifted_rank_on_an_already_seeded_tier(self):
        ensure_default_account_tiers()
        business = AccountTier.objects.get(name="Business")
        business.rank = 0
        business.save(update_fields=["rank"])

        ensure_default_account_tiers()

        business.refresh_from_db()
        self.assertEqual(business.rank, 2)

    def test_does_not_rewrite_a_tier_that_already_matches(self):
        ensure_default_account_tiers()
        pro = AccountTier.objects.get(name="Pro")
        original_updated_at = pro.updated_at

        ensure_default_account_tiers()

        pro.refresh_from_db()
        self.assertEqual(pro.updated_at, original_updated_at)
