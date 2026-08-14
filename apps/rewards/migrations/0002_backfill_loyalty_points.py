from django.db import migrations


def backfill_loyalty_points(apps, schema_editor):
    """
    One-time backfill of legacy apps.commerce.LoyaltyPoint rows into the new
    RewardLedgerEntry ledger, so existing users don't see their KIS Coins
    balance reset to zero when the new system goes live.

    Read-only with respect to LoyaltyPoint: original rows are never modified
    or deleted, they remain as historical data. Every created
    RewardLedgerEntry carries an idempotency_key derived from the source
    LoyaltyPoint's id, so re-running this migration (e.g. after a partial
    failure) is safe — already-backfilled rows are skipped via the ledger's
    unique idempotency_key constraint rather than reprocessed.

    Type inference is best-effort against LoyaltyPoint.reason, which was
    free text with no structured type:
      - reason starting with "referral:" -> TYPE_REFERRAL
      - reason == "User redemption" (the exact string
        apps.commerce.views.LoyaltyPointViewSet.redeem uses) -> TYPE_REDEMPTION
      - everything else -> TYPE_ADMIN_ADJUSTMENT, source="legacy_loyalty_point_backfill"
        (the original free-text reason is preserved verbatim in metadata and
        description, nothing is lost — it's just not confidently classifiable
        into a structured type).
    """
    LoyaltyPoint = apps.get_model('commerce', 'LoyaltyPoint')
    RewardLedgerEntry = apps.get_model('rewards', 'RewardLedgerEntry')

    TYPE_REFERRAL = "referral"
    TYPE_REDEMPTION = "redemption"
    TYPE_ADMIN_ADJUSTMENT = "admin_adjustment"
    STATUS_CONFIRMED = "confirmed"

    created = 0
    skipped_existing = 0

    for lp in LoyaltyPoint.objects.all().iterator():
        idempotency_key = f"legacy_loyalty_point:{lp.id}"
        if RewardLedgerEntry.objects.filter(idempotency_key=idempotency_key).exists():
            skipped_existing += 1
            continue

        reason = (lp.reason or "").strip()
        if reason.startswith("referral:"):
            entry_type = TYPE_REFERRAL
            reference_type = "legacy_referral"
        elif reason == "User redemption":
            entry_type = TYPE_REDEMPTION
            reference_type = "legacy_redemption"
        else:
            entry_type = TYPE_ADMIN_ADJUSTMENT
            reference_type = ""

        RewardLedgerEntry.objects.create(
            user_id=lp.user_id,
            type=entry_type,
            source="legacy_loyalty_point_backfill",
            amount=lp.points,
            status=STATUS_CONFIRMED,
            reference_type=reference_type,
            idempotency_key=idempotency_key,
            description=reason[:255],
            metadata={
                "legacy_loyalty_point_id": str(lp.id),
                "legacy_reason": reason,
                "legacy_shop_id": str(lp.shop_id) if lp.shop_id else None,
            },
            effective_at=lp.earned_at,
            expires_at=lp.expires_at,
        )
        created += 1

    print(
        f"[0002_backfill_loyalty_points] created {created} RewardLedgerEntry row(s), "
        f"skipped {skipped_existing} already-backfilled row(s)"
    )


def noop_reverse(apps, schema_editor):
    # Intentionally not reversible: deleting the backfilled entries would
    # silently zero out balances again. If a rollback is genuinely needed,
    # delete RewardLedgerEntry rows with metadata containing
    # "legacy_loyalty_point_id" explicitly and deliberately, not via a bare
    # migration reversal.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('rewards', '0001_initial'),
        ('commerce', '0063_shop_status'),
    ]

    operations = [
        migrations.RunPython(backfill_loyalty_points, noop_reverse),
    ]
