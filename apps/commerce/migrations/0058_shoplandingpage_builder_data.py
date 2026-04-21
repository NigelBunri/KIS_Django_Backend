from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('commerce', '0057_catalog_category_hierarchy'),
    ]

    operations = [
        migrations.AddField(
            model_name='shoplandingpage',
            name='builder_data',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
