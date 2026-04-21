from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('commerce', '0046_remove_productimage_alt_text'),
    ]

    operations = [
        migrations.AddField(
            model_name='productimage',
            name='alt_text',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
    ]
