from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('commerce', '0048_rename_productimage_order_to_sort_order'),
    ]

    operations = [
        migrations.AddField(
            model_name='productimage',
            name='is_active',
            field=models.BooleanField(default=True),
        ),
    ]
