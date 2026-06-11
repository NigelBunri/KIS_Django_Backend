from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("groups", "0004_merge_20260103_2008"),
    ]

    operations = [
        migrations.AddField(
            model_name="group",
            name="invite_token",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Short random token used to build a shareable invite link.",
                max_length=64,
            ),
        ),
    ]
