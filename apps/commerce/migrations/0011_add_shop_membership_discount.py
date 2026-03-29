from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('commerce', '0010_alter_order_currency_alter_orderitem_currency_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='shop',
            name='membership_discount_pct',
            field=models.PositiveIntegerField(default=5),
        ),
        migrations.AddField(
            model_name='shop',
            name='membership_public',
            field=models.BooleanField(default=False),
        ),
    ]
