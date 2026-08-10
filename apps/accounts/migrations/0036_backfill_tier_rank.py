# Data migration: backfill AccountTier.rank / billing_period_days for rows
# that already existed in the database before the previous migration added
# these columns (which default to rank=0). Without this, any production
# tier row named "Pro"/"Business"/"Business Pro"/"Partner"/"Partner Pro"
# would silently read as rank 0 — identical to "Free" — until something
# happened to invoke ensure_default_account_tiers()'s self-healing path.
# This makes the correct values land immediately and deterministically at
# migration time instead. Never deletes or renames any row.
from django.db import migrations

# Mirrors apps/accounts/tier_presets.py's rank assignment. Duplicated as a
# plain dict (not imported) — migrations must not depend on application
# code that can change shape after this migration is written.
CANONICAL_RANKS = {
    "free": 0,
    "pro": 1,
    "business": 2,
    "business pro": 3,
    "partner": 4,
    "partner pro": 5,
}
DEFAULT_BILLING_PERIOD_DAYS = 30


def backfill_rank(apps, schema_editor):
    AccountTier = apps.get_model("accounts", "AccountTier")
    for tier in AccountTier.objects.all():
        key = (tier.name or "").strip().lower()
        rank = CANONICAL_RANKS.get(key)
        changed_fields = []
        if rank is not None and tier.rank != rank:
            tier.rank = rank
            changed_fields.append("rank")
        if not tier.billing_period_days:
            tier.billing_period_days = DEFAULT_BILLING_PERIOD_DAYS
            changed_fields.append("billing_period_days")
        if changed_fields:
            tier.save(update_fields=changed_fields)


def noop_reverse(apps, schema_editor):
    # Reversing would mean resetting rank back to 0 for these rows, which is
    # itself a real data change we don't want to make implicitly on a
    # migration rollback — intentionally a no-op.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0035_add_tier_rank_and_billing_period'),
    ]

    operations = [
        migrations.RunPython(backfill_rank, noop_reverse),
    ]
