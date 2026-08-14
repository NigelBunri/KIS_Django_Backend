from collections import defaultdict

from django.db import migrations


def cleanup_devices(apps, schema_editor):
    """
    One-time data cleanup ahead of two new constraints on Device:
      - UniqueConstraint(user, device_id)
      - UniqueConstraint(user) WHERE is_parent AND NOT revoked  (one active
        primary per account)

    Both races that could have produced violating rows (upsert_device()
    promoting a device with no atomicity guard, and update_or_create() with
    no uniqueness backstop) predate this migration — see the forensic audit
    that motivated it. This only touches rows that are already anomalous;
    well-formed accounts are untouched.

    Nothing here revokes a device or removes account access: exact-duplicate
    rows collapse into whichever twin was seen most recently (the other is
    almost certainly an orphaned artifact of the race, not a device anyone
    is actively using), and extra "parent" rows are merely demoted to
    secondary — they stay active.
    """
    Device = apps.get_model('accounts', 'Device')

    # --- 1. Exact (user_id, device_id) duplicates -------------------------
    dupe_groups = defaultdict(list)
    for row in Device.objects.all().order_by('user_id', 'device_id'):
        dupe_groups[(row.user_id, row.device_id)].append(row)

    removed = 0
    for key, rows in dupe_groups.items():
        if len(rows) <= 1:
            continue
        rows.sort(key=lambda d: (d.last_seen_at, d.updated_at), reverse=True)
        keeper, losers = rows[0], rows[1:]
        # If any loser was the active parent and the keeper isn't, promote
        # the keeper so the account doesn't lose its primary device outright.
        if any(l.is_parent and l.revoked_at is None for l in losers) and not (
            keeper.is_parent and keeper.revoked_at is None
        ):
            keeper.is_parent = True
            keeper.revoked_at = None
            keeper.save(update_fields=['is_parent', 'revoked_at'])
        for loser in losers:
            loser.delete()
            removed += 1

    # --- 2. Multiple active parents for one user ---------------------------
    parent_rows = defaultdict(list)
    for row in Device.objects.filter(is_parent=True, revoked_at__isnull=True):
        parent_rows[row.user_id].append(row)

    demoted = 0
    for user_id, rows in parent_rows.items():
        if len(rows) <= 1:
            continue
        rows.sort(key=lambda d: (d.last_seen_at, d.updated_at), reverse=True)
        for extra in rows[1:]:
            extra.is_parent = False
            extra.save(update_fields=['is_parent'])
            demoted += 1

    if removed or demoted:
        print(
            f"[0039_cleanup_device_duplicates_and_multi_parent] "
            f"removed {removed} duplicate Device row(s), "
            f"demoted {demoted} extra active-parent row(s)"
        )


def noop_reverse(apps, schema_editor):
    # Cleanup is not reversible (duplicate rows are gone); nothing to undo.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0038_profilepreferences_lock_timeout_minutes'),
    ]

    operations = [
        migrations.RunPython(cleanup_devices, noop_reverse),
    ]
