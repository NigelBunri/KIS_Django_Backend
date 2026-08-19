# Generated manually — data migration seeding WebsiteTemplate rows from
# apps.websites.template_seeds.TEMPLATE_SEEDS (plain data, no model
# dependency, safe to import from a migration).
import uuid

from django.db import migrations


def seed_templates(apps, schema_editor):
    from apps.websites.template_seeds import TEMPLATE_SEEDS

    WebsiteTemplate = apps.get_model("websites", "WebsiteTemplate")
    for entry in TEMPLATE_SEEDS:
        WebsiteTemplate.objects.get_or_create(
            owner_type=entry["owner_type"],
            name=entry["name"],
            defaults={
                "id": uuid.uuid4(),
                "description": entry["description"],
                "seed_pages": entry["seed_pages"],
                "is_active": True,
            },
        )


def unseed_templates(apps, schema_editor):
    from apps.websites.template_seeds import TEMPLATE_SEEDS

    WebsiteTemplate = apps.get_model("websites", "WebsiteTemplate")
    for entry in TEMPLATE_SEEDS:
        WebsiteTemplate.objects.filter(owner_type=entry["owner_type"], name=entry["name"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("websites", "0007_websitetemplate"),
    ]

    operations = [
        migrations.RunPython(seed_templates, unseed_templates),
    ]
