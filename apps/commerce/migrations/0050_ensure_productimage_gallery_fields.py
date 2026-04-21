from django.db import migrations, models


def _get_column_names(connection, table_name):
    with connection.cursor() as cursor:
        description = connection.introspection.get_table_description(cursor, table_name)
    return {
        getattr(column, "name", None) or column[0]
        for column in description
    }


def _ensure_gallery_fields(apps, schema_editor):
    ProductImage = apps.get_model("commerce", "ProductImage")
    table_name = ProductImage._meta.db_table
    connection = schema_editor.connection

    existing_columns = _get_column_names(connection, table_name)

    if "order" in existing_columns and "sort_order" not in existing_columns:
        table_quoted = schema_editor.quote_name(table_name)
        try:
            schema_editor.execute(
                f"ALTER TABLE {table_quoted} "
                f"RENAME COLUMN {schema_editor.quote_name('order')} "
                f"TO {schema_editor.quote_name('sort_order')};"
            )
        except Exception:
            pass
        existing_columns = _get_column_names(connection, table_name)

    if "sort_order" not in existing_columns:
        sort_order_field = models.PositiveIntegerField(default=0)
        sort_order_field.set_attributes_from_name("sort_order")
        schema_editor.add_field(ProductImage, sort_order_field)
        existing_columns.add("sort_order")

    if "alt_text" not in existing_columns:
        alt_text_field = models.CharField(max_length=255, blank=True, default="")
        alt_text_field.set_attributes_from_name("alt_text")
        schema_editor.add_field(ProductImage, alt_text_field)
        existing_columns.add("alt_text")

    if "is_active" not in existing_columns:
        active_field = models.BooleanField(default=True)
        active_field.set_attributes_from_name("is_active")
        schema_editor.add_field(ProductImage, active_field)
        existing_columns.add("is_active")


class Migration(migrations.Migration):
    dependencies = [
        ("commerce", "0049_add_productimage_is_active"),
    ]

    operations = [
        migrations.RunPython(_ensure_gallery_fields, reverse_code=migrations.RunPython.noop),
    ]
