from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0004_rename_billing_br_status_idx_billing_bil_status_fa3c2f_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="walletaccount",
            name="locked_cents",
            field=models.BigIntegerField(default=0),
        ),
    ]
