from django.db import migrations


def _collect_catalog_categories(shop_category_ids, ShopCategory, catalog_map):
    seen = set()
    catalog_categories = []

    for raw_id in shop_category_ids:
        if not raw_id:
            continue

        identifier = str(raw_id)

        if identifier in seen:
            continue

        seen.add(identifier)

        shop_category = ShopCategory.objects.filter(id=raw_id).first()
        if not shop_category:
            continue

        catalog_category = catalog_map.get(shop_category.slug)
        if catalog_category:
            catalog_categories.append(catalog_category)

    return catalog_categories


def migrate_categories(apps, schema_editor):
    CatalogCategory = apps.get_model("commerce", "CatalogCategory")
    ShopCategory = apps.get_model("commerce", "ShopCategory")
    Product = apps.get_model("commerce", "Product")
    ShopService = apps.get_model("commerce", "ShopService")

    # IMPORTANT:
    # Do not call ensure_catalog_categories() here.
    # This migration runs before parent/sort_order fields exist.
    # Calling current model code here breaks fresh production DB setup.
    catalog_map = {cat.slug: cat for cat in CatalogCategory.objects.all()}

    for model in (Product, ShopService):
        for instance in model.objects.all():
            shop_category_ids = []

            primary_id = getattr(instance, "category_id", None)
            if primary_id:
                shop_category_ids.append(primary_id)

            extra_ids = getattr(instance, "category_ids", None) or []
            shop_category_ids.extend(extra_ids)

            catalog_categories = _collect_catalog_categories(
                shop_category_ids,
                ShopCategory,
                catalog_map,
            )

            if catalog_categories:
                getattr(instance, "catalog_categories").set(catalog_categories)


class Migration(migrations.Migration):

    dependencies = [
        ("commerce", "0040_product_catalog_categories"),
    ]

    operations = [
        migrations.RunPython(
            migrate_categories,
            reverse_code=migrations.RunPython.noop,
        ),
    ]