from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("communities", "0006_merge_20260105_0733"),
    ]

    operations = [
        migrations.AddField(
            model_name="communitypost",
            name="is_broadcast",
            field=models.BooleanField(default=False),
        ),
    ]
