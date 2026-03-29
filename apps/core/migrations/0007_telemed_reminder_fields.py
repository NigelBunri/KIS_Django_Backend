from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0006_telemedicinesession_alter_equipment_id_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="telemedicinesession",
            name="reminder_sent",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="telemedicinesession",
            name="reminder_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
