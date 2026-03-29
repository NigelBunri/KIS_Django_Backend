from django.db import migrations, models
from django.db.models.deletion import CASCADE, SET_NULL
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('commerce', '0006_shop_employee_slots'),
    ]

    operations = [
        migrations.CreateModel(
            name='ShopCategory',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('name', models.CharField(max_length=128)),
                ('slug', models.SlugField(max_length=128)),
                ('description', models.TextField(blank=True)),
                ('category_type', models.CharField(choices=[('product', 'Product'), ('service', 'Service'), ('both', 'Both')], default='product', max_length=16)),
                (
                    'shop',
                    models.ForeignKey(on_delete=CASCADE, related_name='categories', to='commerce.shop'),
                ),
            ],
            options={
                'abstract': False,
                'unique_together': {('shop', 'slug')},
            },
        ),
        migrations.AddField(
            model_name='product',
            name='category',
            field=models.ForeignKey(blank=True, null=True, on_delete=SET_NULL, related_name='products', to='commerce.shopcategory'),
        ),
        migrations.CreateModel(
            name='ProductImage',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('image_file', models.ImageField(upload_to='commerce/product-images/')),
                ('order', models.PositiveIntegerField(default=0)),
                (
                    'product',
                    models.ForeignKey(on_delete=CASCADE, related_name='images', to='commerce.product'),
                ),
            ],
            options={
                'abstract': False,
                'ordering': ['order', 'created_at'],
            },
        ),
    ]
