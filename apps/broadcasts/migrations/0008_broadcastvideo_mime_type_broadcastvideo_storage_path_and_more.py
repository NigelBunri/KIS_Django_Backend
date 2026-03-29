from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("broadcasts", "0007_broadcast_extra_features"),
    ]

    operations = [
        migrations.AddField(
            model_name="broadcastvideo",
            name="mime_type",
            field=models.CharField(blank=True, default="", max_length=256),
        ),
        migrations.AddField(
            model_name="broadcastvideo",
            name="storage_path",
            field=models.CharField(blank=True, default="", max_length=1024),
        ),
        migrations.AddField(
            model_name="broadcastvideo",
            name="transcript_segments",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
