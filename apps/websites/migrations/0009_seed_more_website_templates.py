# Generated manually — re-runs the same idempotent seed as 0008 now that
# apps.websites.template_seeds.TEMPLATE_SEEDS has 5 more entries (a
# second template per owner type). get_or_create on (owner_type, name)
# means the original 5 are untouched; only the 5 new ones get created.
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
        ("websites", "0008_seed_website_templates"),
    ]

    operations = [
        migrations.RunPython(seed_templates, unseed_templates),
    ]
