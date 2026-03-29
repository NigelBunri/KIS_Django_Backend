from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("bible", "0007_course_visibility"),
    ]

    operations = [
        migrations.CreateModel(
            name="BibleLessonReaction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("emoji", models.CharField(default="❤️", max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("lesson", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="reactions", to="bible.biblelesson")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lesson_reactions", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "unique_together": {("lesson", "user")},
            },
        ),
        migrations.CreateModel(
            name="BibleLessonComment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("content", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("lesson", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="comments", to="bible.biblelesson")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lesson_comments", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
