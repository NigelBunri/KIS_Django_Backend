from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('commerce', '0012_auto_20260319_0904'),
    ]

    def _create_owner_memberships(apps, schema_editor):
        Shop = apps.get_model('commerce', 'Shop')
        ShopTeamMember = apps.get_model('commerce', 'ShopTeamMember')
        for shop in Shop.objects.filter(is_deleted=False).all():
            if not shop.owner_id:
                continue
            ShopTeamMember.objects.get_or_create(
                shop=shop,
                user_id=shop.owner_id,
                defaults={'role': 'owner', 'is_active': True},
            )

    operations = [
        migrations.CreateModel(
            name='ShopTeamMember',
            fields=[
                ('id', models.UUIDField(editable=False, primary_key=True, default=uuid.uuid4)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('role', models.CharField(choices=[('owner', 'Owner'), ('manager', 'Manager'), ('admin', 'Admin'), ('member', 'Member')], default='member', max_length=16)),
                ('is_active', models.BooleanField(default=True)),
                ('shop', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='team_members', to='commerce.shop')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='shop_memberships', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'unique_together': {('shop', 'user')},
                'indexes': [
                    models.Index(fields=['shop', 'role'], name='commerce_sh_shop_id_929a90_idx'),
                    models.Index(fields=['user'], name='commerce_sh_user_id_d5fe0b_idx'),
                ],
            },
        ),
        migrations.RunPython(_create_owner_memberships, migrations.RunPython.noop),
    ]
