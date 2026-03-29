from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("broadcasts", "0009_alter_broadcastvideo_mime_type_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="broadcastitem",
            name="metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
