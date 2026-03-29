from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("health_dashboard", "0002_relational_landing_pages"),
    ]

    operations = [
        migrations.AlterField(
            model_name="healthdashboardinstitutionlandingpage",
            name="is_published",
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]
