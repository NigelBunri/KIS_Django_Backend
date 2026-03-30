from django.db import migrations, models
import django.contrib.postgres.fields


class Migration(migrations.Migration):

    dependencies = [
        ('commerce', '0035_servicebookingreceipt'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='brand',
            field=models.CharField(blank=True, default='', max_length=128),
        ),
        migrations.AddField(
            model_name='product',
            name='condition',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='product',
            name='sale_price',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name='product',
            name='compare_at_price',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name='product',
            name='material',
            field=models.CharField(blank=True, default='', max_length=128),
        ),
        migrations.AddField(
            model_name='product',
            name='fit',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='product',
            name='size_guide',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='product',
            name='available_sizes',
            field=django.contrib.postgres.fields.ArrayField(base_field=models.CharField(max_length=64), blank=True, default=list, size=None),
        ),
        migrations.AddField(
            model_name='product',
            name='available_colors',
            field=django.contrib.postgres.fields.ArrayField(base_field=models.CharField(max_length=64), blank=True, default=list, size=None),
        ),
        migrations.AddField(
            model_name='product',
            name='weight',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='product',
            name='length',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='product',
            name='width',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='product',
            name='height',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='product',
            name='low_stock_threshold',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='product',
            name='requires_shipping',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='product',
            name='pickup_available',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='product',
            name='allow_backorder',
            field=models.BooleanField(default=False),
        ),
    ]
