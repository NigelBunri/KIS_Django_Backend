import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("commerce", "0039_multi_category_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="CatalogCategory",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("name", models.CharField(max_length=128)),
                ("slug", models.SlugField(max_length=128, unique=True)),
                ("description", models.TextField(blank=True)),
                ("category_type", models.CharField(max_length=16)),
            ],
            options={"ordering": ["category_type", "name"]},
        ),
        migrations.AddField(
            model_name="product",
            name="catalog_categories",
            field=models.ManyToManyField(
                blank=True,
                related_name="products",
                to="commerce.CatalogCategory",
            ),
        ),
        migrations.AddField(
            model_name="shopservice",
            name="catalog_categories",
            field=models.ManyToManyField(
                blank=True,
                related_name="services",
                to="commerce.CatalogCategory",
            ),
        ),
    ]
