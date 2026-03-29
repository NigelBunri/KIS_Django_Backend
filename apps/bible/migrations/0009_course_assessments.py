from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("bible", "0008_lesson_social"),
        ("partners", "0022_merge_0021_org_profile_and_setting_rename"),
    ]

    operations = [
        migrations.CreateModel(
            name="BibleCourseTrack",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("description", models.TextField(blank=True)),
                ("is_published", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("partner", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="course_tracks", to="partners.partner")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="BibleCourseTrackItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("order", models.PositiveIntegerField(default=1)),
                ("course", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="track_items", to="bible.biblecourse")),
                ("track", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="bible.biblecoursetrack")),
            ],
            options={"ordering": ["order"], "unique_together": {("track", "order")}},
        ),
        migrations.CreateModel(
            name="BibleCoursePrerequisite",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("required_percent", models.PositiveIntegerField(default=100)),
                ("course", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="prerequisites", to="bible.biblecourse")),
                ("required_course", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="required_for", to="bible.biblecourse")),
            ],
            options={"unique_together": {("course", "required_course")}},
        ),
        migrations.CreateModel(
            name="BibleQuiz",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("description", models.TextField(blank=True)),
                ("pass_score", models.PositiveIntegerField(default=70)),
                ("time_limit_minutes", models.PositiveIntegerField(default=0)),
                ("attempts_allowed", models.PositiveIntegerField(default=3)),
                ("is_exam", models.BooleanField(default=False)),
                ("is_active", models.BooleanField(default=True)),
                ("order", models.PositiveIntegerField(default=1)),
                ("course", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="quizzes", to="bible.biblecourse")),
                ("lesson", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="quizzes", to="bible.biblelesson")),
            ],
            options={"ordering": ["order"]},
        ),
        migrations.CreateModel(
            name="BibleQuizQuestion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("prompt", models.TextField()),
                ("kind", models.CharField(choices=[("single_choice", "Single choice"), ("multiple_choice", "Multiple choice"), ("true_false", "True/False"), ("short_answer", "Short answer")], default="single_choice", max_length=20)),
                ("points", models.PositiveIntegerField(default=1)),
                ("order", models.PositiveIntegerField(default=1)),
                ("quiz", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="questions", to="bible.biblequiz")),
            ],
            options={"ordering": ["order"]},
        ),
        migrations.CreateModel(
            name="BibleQuizChoice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("text", models.CharField(max_length=300)),
                ("is_correct", models.BooleanField(default=False)),
                ("question", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="choices", to="bible.biblequizquestion")),
            ],
        ),
        migrations.CreateModel(
            name="BibleQuizAttempt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("score", models.PositiveIntegerField(default=0)),
                ("max_score", models.PositiveIntegerField(default=0)),
                ("passed", models.BooleanField(default=False)),
                ("answers", models.JSONField(blank=True, default=list)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("quiz", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="attempts", to="bible.biblequiz")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="quiz_attempts", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-started_at"]},
        ),
        migrations.CreateModel(
            name="BibleAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("description", models.TextField(blank=True)),
                ("due_at", models.DateTimeField(blank=True, null=True)),
                ("max_points", models.PositiveIntegerField(default=100)),
                ("rubric", models.JSONField(blank=True, default=list)),
                ("order", models.PositiveIntegerField(default=1)),
                ("is_required", models.BooleanField(default=True)),
                ("course", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="assignments", to="bible.biblecourse")),
                ("lesson", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="assignments", to="bible.biblelesson")),
            ],
            options={"ordering": ["order"]},
        ),
        migrations.CreateModel(
            name="BibleAssignmentSubmission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("content_text", models.TextField(blank=True)),
                ("attachments", models.JSONField(blank=True, default=list)),
                ("score", models.PositiveIntegerField(default=0)),
                ("feedback", models.TextField(blank=True)),
                ("status", models.CharField(choices=[("submitted", "Submitted"), ("graded", "Graded")], default="submitted", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("graded_at", models.DateTimeField(blank=True, null=True)),
                ("assignment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="submissions", to="bible.bibleassignment")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="assignment_submissions", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"], "unique_together": {("assignment", "user")}},
        ),
        migrations.CreateModel(
            name="BiblePeerReview",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("score", models.PositiveIntegerField(default=0)),
                ("feedback", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("reviewer", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="peer_reviews", to=settings.AUTH_USER_MODEL)),
                ("submission", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="peer_reviews", to="bible.bibleassignmentsubmission")),
            ],
            options={"unique_together": {("submission", "reviewer")}},
        ),
    ]
