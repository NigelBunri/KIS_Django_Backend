from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("communities", "0008_communitypost_comment_conversation"),
    ]

    operations = [
        migrations.AddField(
            model_name="communitymembership",
            name="lesson_access_only",
            field=models.BooleanField(
                default=False,
                help_text="Only enrolled for lesson-focused access.",
            ),
        ),
    ]
