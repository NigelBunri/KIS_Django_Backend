from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("communities", "0012_community_settings_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="community",
            name="allow_broadcasts",
            field=models.BooleanField(
                default=True,
                help_text="When false, community posts cannot be promoted to broadcasts.",
            ),
        ),
    ]
