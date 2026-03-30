from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('commerce', '0024_alter_shopservice_addons_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='servicebooking',
            name='metadata',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
