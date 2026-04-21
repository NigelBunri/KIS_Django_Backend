from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("commerce", "0041_migrate_catalog_categories"),
    ]

    operations = [
        migrations.RemoveField(model_name="product", name="category"),
        migrations.RemoveField(model_name="product", name="category_ids"),
        migrations.RemoveField(model_name="product", name="categories"),
        migrations.RemoveField(model_name="shopservice", name="category"),
        migrations.RemoveField(model_name="shopservice", name="category_ids"),
    ]
