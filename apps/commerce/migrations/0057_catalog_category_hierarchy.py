from django.db import migrations, models
import django.db.models.deletion


def sync_catalog_hierarchy(apps, schema_editor):
    from apps.commerce.category_catalog import ensure_catalog_categories

    ensure_catalog_categories()


class Migration(migrations.Migration):

    dependencies = [
        ("commerce", "0056_complaint_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="catalogcategory",
            name="parent",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="children",
                to="commerce.catalogcategory",
            ),
        ),
        migrations.AddField(
            model_name="catalogcategory",
            name="sort_order",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterModelOptions(
            name="catalogcategory",
            options={"ordering": ["category_type", "sort_order", "name"]},
        ),
        migrations.RunPython(sync_catalog_hierarchy, reverse_code=migrations.RunPython.noop),
    ]
