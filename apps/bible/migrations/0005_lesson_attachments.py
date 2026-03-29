from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("bible", "0004_seed_courses"),
    ]

    operations = [
        migrations.AddField(
            model_name="biblelesson",
            name="attachments",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
