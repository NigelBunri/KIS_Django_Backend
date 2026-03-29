from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("bible", "0005_lesson_attachments"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="biblecourse",
            name="is_public",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="BibleCourseReaction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("emoji", models.CharField(default="❤️", max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("course", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="reactions", to="bible.biblecourse")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="course_reactions", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "unique_together": {("course", "user")},
            },
        ),
        migrations.CreateModel(
            name="BibleCourseComment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("content", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("course", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="comments", to="bible.biblecourse")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="course_comments", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="BibleCourseShare",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("course", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="shares", to="bible.biblecourse")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="course_shares", to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
