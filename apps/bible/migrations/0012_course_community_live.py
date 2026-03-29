from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("bible", "0011_lesson_accessibility"),
    ]

    operations = [
        migrations.CreateModel(
            name="BibleCourseForum",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_locked", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("course", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="forum", to="bible.biblecourse")),
            ],
        ),
        migrations.CreateModel(
            name="BibleForumThread",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("is_pinned", models.BooleanField(default=False)),
                ("is_locked", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="forum_threads", to=settings.AUTH_USER_MODEL)),
                ("forum", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="threads", to="bible.biblecourseforum")),
            ],
            options={"ordering": ["-is_pinned", "-created_at"]},
        ),
        migrations.CreateModel(
            name="BibleForumPost",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("content", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("thread", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="posts", to="bible.bibleforumthread")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="forum_posts", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["created_at"]},
        ),
        migrations.CreateModel(
            name="BibleMentorAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("role", models.CharField(default="mentor", max_length=50)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("course", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="mentors", to="bible.biblecourse")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="mentor_courses", to=settings.AUTH_USER_MODEL)),
            ],
            options={"unique_together": {("course", "user")}},
        ),
        migrations.CreateModel(
            name="BibleLiveSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("description", models.TextField(blank=True)),
                ("start_at", models.DateTimeField()),
                ("end_at", models.DateTimeField(blank=True, null=True)),
                ("meeting_url", models.URLField(blank=True)),
                ("is_recorded", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("course", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="live_sessions", to="bible.biblecourse")),
                ("host", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="hosted_live_sessions", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-start_at"]},
        ),
        migrations.CreateModel(
            name="BibleLiveAttendance",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(default="registered", max_length=20)),
                ("joined_at", models.DateTimeField(blank=True, null=True)),
                ("left_at", models.DateTimeField(blank=True, null=True)),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="attendances", to="bible.biblelivesession")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="live_attendances", to=settings.AUTH_USER_MODEL)),
            ],
            options={"unique_together": {("session", "user")}},
        ),
        migrations.CreateModel(
            name="BibleLiveRecording",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("media_url", models.URLField()),
                ("duration_minutes", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="recordings", to="bible.biblelivesession")),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
