from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("bible", "0010_submission_plagiarism"),
    ]

    operations = [
        migrations.AddField(
            model_name="biblelesson",
            name="transcript",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="biblelesson",
            name="captions_url",
            field=models.URLField(blank=True),
        ),
        migrations.AddField(
            model_name="biblelesson",
            name="language",
            field=models.CharField(default="en", max_length=20),
        ),
    ]
