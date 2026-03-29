from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("commerce", "0026_remove_servicebooking_unique_service_schedule_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="shopservice",
            name="allow_multiple_attendees_per_slot",
            field=models.BooleanField(default=False),
        ),
    ]
