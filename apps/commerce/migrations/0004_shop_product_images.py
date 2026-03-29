import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("commerce", "0003_alter_product_image_url"),
    ]

    operations = [
        migrations.AddField(
            model_name="shop",
            name="image_file",
            field=models.ImageField(blank=True, null=True, upload_to="commerce/shops/"),
        ),
        migrations.AddField(
            model_name="product",
            name="image_file",
            field=models.ImageField(blank=True, null=True, upload_to="commerce/products/"),
        ),
        migrations.CreateModel(
            name="ProductSubscription",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("subscribed_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="subscriptions",
                        to="commerce.product",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="product_subscriptions",
                        to="accounts.user",
                    ),
                ),
            ],
            options={
                "unique_together": {("user", "product")},
            },
        ),
    ]
