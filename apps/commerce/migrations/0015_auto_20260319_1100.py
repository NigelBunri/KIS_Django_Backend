from django.db import migrations


def create_owner_memberships(apps, schema_editor):
    Shop = apps.get_model('commerce', 'Shop')
    ShopTeamMember = apps.get_model('commerce', 'ShopTeamMember')
    for shop in Shop.objects.exclude(owner_id__isnull=True):
        ShopTeamMember.objects.get_or_create(
            shop_id=shop.id,
            user_id=shop.owner_id,
            defaults={'role': 'owner', 'is_active': True},
        )


class Migration(migrations.Migration):
    dependencies = [
        ('commerce', '0014_alter_shopteammember_id'),
    ]

    operations = [
        migrations.RunPython(create_owner_memberships, reverse_code=migrations.RunPython.noop),
    ]
