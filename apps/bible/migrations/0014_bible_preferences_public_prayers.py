from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("bible", "0013_marketplace_credentials"),
    ]

    operations = [
        migrations.AddField(
            model_name="prayerrequest",
            name="is_public",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="biblepreference",
            name="enable_audio_sync",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="biblepreference",
            name="enable_parallel_view",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="biblepreference",
            name="enable_daily_reminders",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="biblepreference",
            name="enable_offline_cache",
            field=models.BooleanField(default=False),
        ),
    ]
