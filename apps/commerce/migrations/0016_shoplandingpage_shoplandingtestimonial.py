from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [
        ('commerce', '0015_auto_20260319_1100'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ShopLandingPage',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('headline', models.CharField(blank=True, max_length=255)),
                ('subheadline', models.TextField(blank=True)),
                ('hero_image_url', models.URLField(blank=True, max_length=512)),
                ('hero_cta_text', models.CharField(blank=True, max_length=128)),
                ('hero_cta_url', models.URLField(blank=True, max_length=512)),
                ('is_public', models.BooleanField(default=False)),
                ('is_published', models.BooleanField(default=False)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='shop_landing_pages_created', to=settings.AUTH_USER_MODEL)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='shop_landing_pages_updated', to=settings.AUTH_USER_MODEL)),
                ('shop', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='landing_page', to='commerce.shop')),
            ],
            options={
                'verbose_name': 'Shop landing page',
                'verbose_name_plural': 'Shop landing pages',
            },
        ),
        migrations.CreateModel(
            name='ShopLandingTestimonial',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('quote', models.TextField()),
                ('author', models.CharField(blank=True, max_length=128)),
                ('role', models.CharField(blank=True, max_length=128)),
                ('rating', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('sort_order', models.PositiveIntegerField(default=0)),
                ('landing_page', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='testimonials', to='commerce.shoplandingpage')),
            ],
            options={
                'ordering': ['sort_order', 'created_at'],
            },
        ),
    ]
