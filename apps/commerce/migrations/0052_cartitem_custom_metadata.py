from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('commerce', '0051_alter_productimage_options_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='cartitem',
            name='selected_attributes',
            field=models.JSONField(default=dict, blank=True),
        ),
        migrations.AddField(
            model_name='cartitem',
            name='custom_description',
            field=models.TextField(blank=True),
        ),
    ]
