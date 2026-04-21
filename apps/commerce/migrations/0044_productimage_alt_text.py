from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('commerce', '0043_remove_shopservice_commerce_sh_categor_8518c5_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='productimage',
            name='alt_text',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
    ]
