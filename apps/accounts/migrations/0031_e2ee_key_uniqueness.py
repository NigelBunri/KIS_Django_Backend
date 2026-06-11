from django.db import migrations, models
from django.db.models import Count


def remove_duplicate_e2ee_keys(apps, schema_editor):
    E2EDeviceKey = apps.get_model("accounts", "E2EDeviceKey")
    E2EPreKey = apps.get_model("accounts", "E2EPreKey")

    duplicate_devices = (
        E2EDeviceKey.objects.values("user_id", "device_id")
        .annotate(row_count=Count("id"))
        .filter(row_count__gt=1)
    )
    for duplicate in duplicate_devices.iterator():
        rows = E2EDeviceKey.objects.filter(
            user_id=duplicate["user_id"],
            device_id=duplicate["device_id"],
        ).order_by("-updated_at", "-created_at", "-id")
        keeper = rows.first()
        if keeper:
            rows.exclude(pk=keeper.pk).delete()

    duplicate_prekeys = (
        E2EPreKey.objects.values("user_id", "device_id", "prekey_id")
        .annotate(row_count=Count("id"))
        .filter(row_count__gt=1)
    )
    for duplicate in duplicate_prekeys.iterator():
        rows = E2EPreKey.objects.filter(
            user_id=duplicate["user_id"],
            device_id=duplicate["device_id"],
            prekey_id=duplicate["prekey_id"],
        ).order_by("-updated_at", "-created_at", "-id")
        keeper = rows.first()
        if keeper:
            rows.exclude(pk=keeper.pk).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0030_device_parent_qr"),
    ]

    operations = [
        migrations.RunPython(remove_duplicate_e2ee_keys, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="e2edevicekey",
            constraint=models.UniqueConstraint(
                fields=("user", "device"),
                name="accounts_e2e_device_key_user_device_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="e2eprekey",
            constraint=models.UniqueConstraint(
                fields=("user", "device", "prekey_id"),
                name="accounts_e2e_prekey_user_device_id_uniq",
            ),
        ),
    ]
