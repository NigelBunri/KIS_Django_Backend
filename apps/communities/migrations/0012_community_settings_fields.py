from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("communities", "0011_alter_communitypost_text_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="community",
            name="allow_post_link_copy",
            field=models.BooleanField(
                default=True,
                help_text="If false, only privileged roles can copy post permalinks.",
            ),
        ),
        migrations.AddField(
            model_name="community",
            name="allow_join_link",
            field=models.BooleanField(
                default=True,
                help_text="When false, join links are disabled and requests must go through approval.",
            ),
        ),
        migrations.AddField(
            model_name="community",
            name="require_join_survey",
            field=models.BooleanField(
                default=False,
                help_text="Require a short survey before a member is approved.",
            ),
        ),
    ]
