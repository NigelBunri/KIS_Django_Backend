from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('commerce', '0005_remove_productshare_platform_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='shop',
            name='employee_slots',
            field=models.PositiveIntegerField(default=1),
        ),
    ]
