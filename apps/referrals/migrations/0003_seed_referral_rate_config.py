from decimal import Decimal

from django.db import migrations

# rank -> starting referral rate percent, per the confirmed business rule:
# Free=2%, Pro=5%, Business=8%, Business Pro=10%, Partner=15%, Partner Pro=20%.
# Matched by AccountTier.rank (0 through 5), NOT by name string — this is
# the canonical, DB-enforced tier hierarchy field, deliberately avoiding the
# name__iexact="Free" antipattern found elsewhere in this codebase.
_RATE_BY_RANK = {
    0: Decimal("2.00"),
    1: Decimal("5.00"),
    2: Decimal("8.00"),
    3: Decimal("10.00"),
    4: Decimal("15.00"),
    5: Decimal("20.00"),
}


def seed_referral_rates(apps, schema_editor):
    AccountTier = apps.get_model('accounts', 'AccountTier')
    ReferralRateConfig = apps.get_model('referrals', 'ReferralRateConfig')

    created = 0
    updated = 0
    skipped_ranks = []

    for rank, rate_percent in _RATE_BY_RANK.items():
        tier = AccountTier.objects.filter(rank=rank).order_by("created_at").first()
        if not tier:
            skipped_ranks.append(rank)
            continue
        _, was_created = ReferralRateConfig.objects.update_or_create(
            tier=tier, defaults={"rate_percent": rate_percent, "is_active": True},
        )
        if was_created:
            created += 1
        else:
            updated += 1

    print(
        f"[0003_seed_referral_rate_config] created {created}, updated {updated} "
        f"ReferralRateConfig row(s); no AccountTier found for rank(s): {skipped_ranks or 'none'}"
    )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('referrals', '0002_referral_qualification_fields'),
        ('accounts', '0040_device_one_active_parent_constraint'),
    ]

    operations = [
        migrations.RunPython(seed_referral_rates, noop_reverse),
    ]
