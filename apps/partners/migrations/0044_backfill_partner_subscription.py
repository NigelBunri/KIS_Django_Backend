# apps/partners/migrations/0044_backfill_partner_subscription.py
"""
Backfill: give every existing Partner a PartnerSubscription matching its
owner's CURRENT personal tier at migration time. This is a one-time
snapshot, not an ongoing link — going forward each Partner's tier is
managed independently via PartnerSubscription (see that model's docstring).
Without this backfill, every existing organization would drop to "no tier"
(zero features) the instant partner-scoped feature gates switch from
checking the owner's personal tier to checking PartnerSubscription.
"""

from django.db import migrations


def backfill_partner_subscriptions(apps, schema_editor):
    Partner = apps.get_model("partners", "Partner")
    PartnerSubscription = apps.get_model("partners", "PartnerSubscription")
    Subscription = apps.get_model("accounts", "Subscription")
    AccountTier = apps.get_model("accounts", "AccountTier")

    for partner in Partner.objects.all().iterator():
        if PartnerSubscription.objects.filter(partner_id=partner.id).exists():
            continue

        tier = None
        active_sub = (
            Subscription.objects.filter(user_id=partner.owner_id, status="active")
            .order_by("-started_at")
            .first()
        )
        if active_sub and active_sub.tier_id:
            tier = active_sub.tier
        else:
            owner_tier_name = getattr(partner.owner, "tier", None)
            if owner_tier_name:
                tier = AccountTier.objects.filter(name__iexact=owner_tier_name).first()

        PartnerSubscription.objects.create(
            partner_id=partner.id,
            tier=tier,
            status="active",
        )


def noop_reverse(apps, schema_editor):
    # Deliberately not reversible by deleting rows — a rollback of the
    # schema migration already drops the table; there is nothing else to
    # undo here, and re-deriving "was this row backfilled or set manually
    # afterward" isn't possible.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("partners", "0043_add_partner_subscription"),
    ]

    operations = [
        migrations.RunPython(backfill_partner_subscriptions, noop_reverse),
    ]
