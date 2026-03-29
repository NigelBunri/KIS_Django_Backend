from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('commerce', '0007_shop_categories_and_product_images'),
    ]

    operations = [
        migrations.AlterField(
            model_name='product',
            name='currency',
            field=models.CharField(default='KISC', max_length=8),
        ),
        migrations.AlterField(
            model_name='order',
            name='currency',
            field=models.CharField(default='KISC', max_length=8),
        ),
        migrations.AlterField(
            model_name='orderitem',
            name='currency',
            field=models.CharField(default='KISC', max_length=8),
        ),
        migrations.AlterField(
            model_name='payment',
            name='currency',
            field=models.CharField(default='KISC', max_length=8),
        ),
        migrations.AlterField(
            model_name='subscription',
            name='currency',
            field=models.CharField(default='KISC', max_length=8),
        ),
    ]
