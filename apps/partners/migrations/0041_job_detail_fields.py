from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('partners', '0040_user_app_shortcut'),
    ]

    operations = [
        migrations.AddField(
            model_name='partnerjobpost',
            name='location',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
        migrations.AddField(
            model_name='partnerjobpost',
            name='is_remote',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='partnerjobpost',
            name='job_type',
            field=models.CharField(
                choices=[
                    ('full_time', 'Full Time'),
                    ('part_time', 'Part Time'),
                    ('contract', 'Contract'),
                    ('freelance', 'Freelance'),
                    ('internship', 'Internship'),
                ],
                default='full_time',
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name='partnerjobpost',
            name='salary_min_cents',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='partnerjobpost',
            name='salary_max_cents',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='partnerjobpost',
            name='salary_currency',
            field=models.CharField(blank=True, default='USD', max_length=10),
        ),
        migrations.AddField(
            model_name='partnerjobpost',
            name='tags',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
