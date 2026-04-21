from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("commerce", "0038_productvariant"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="category_ids",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="shopservice",
            name="category_ids",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
