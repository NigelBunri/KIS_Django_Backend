from django.conf import settings
from django.db import migrations, models

import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('commerce', '0008_currency_defaults'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProductRating',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('score', models.PositiveSmallIntegerField()),
                ('product', models.ForeignKey(on_delete=models.CASCADE, related_name='ratings', to='commerce.product')),
                ('user', models.ForeignKey(on_delete=models.CASCADE, related_name='product_ratings', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'abstract': False,
                'unique_together': {('product', 'user')},
            },
        ),
    ]
