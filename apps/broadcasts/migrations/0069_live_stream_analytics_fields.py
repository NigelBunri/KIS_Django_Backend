from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("broadcasts", "0068_add_channel_chat_ban_and_live_chat_moderation"),
    ]

    operations = [
        migrations.AddField(
            model_name="channellivestream",
            name="total_view_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="channellivestream",
            name="total_watch_seconds",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="channellivestream",
            name="playback_error_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
