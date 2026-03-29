from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("bible", "0009_course_assessments"),
    ]

    operations = [
        migrations.AddField(
            model_name="bibleassignmentsubmission",
            name="plagiarism_score",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="bibleassignmentsubmission",
            name="plagiarism_status",
            field=models.CharField(
                choices=[("pending", "Pending"), ("clean", "Clean"), ("flagged", "Flagged")],
                default="pending",
                max_length=20,
            ),
        ),
    ]
