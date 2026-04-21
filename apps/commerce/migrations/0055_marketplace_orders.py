from __future__ import annotations

from django.conf import settings
from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('commerce', '0054_cartitem_attribute_labels'),
        ('billing', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='MarketplaceOrder',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('total_amount', models.DecimalField(decimal_places=2, max_digits=14)),
                ('currency', models.CharField(default='KIS', max_length=16)),
                ('status', models.CharField(choices=[('temporal', 'Temporal'), ('cancelled', 'Cancelled'), ('satisfied', 'Satisfied'), ('completed', 'Completed'), ('complaint', 'Complaint')], default='temporal', max_length=32)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('receipt_generated', models.BooleanField(default=False)),
                ('buyer', models.ForeignKey(on_delete=models.CASCADE, related_name='marketplace_orders', to=settings.AUTH_USER_MODEL)),
                ('shop', models.ForeignKey(on_delete=models.CASCADE, related_name='marketplace_orders', to='commerce.shop')),
                ('buyer_debit_transaction', models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name='marketplace_buyer_orders', to='billing.wallettransaction')),
                ('provider_credit_transaction', models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name='marketplace_provider_orders', to='billing.wallettransaction')),
            ],
            options={
                'indexes': [
                    models.Index(fields=['buyer', 'status'], name='commerce_marketplaceorder_buyer_status_idx'),
                    models.Index(fields=['shop', 'status'], name='commerce_marketplaceorder_shop_status_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='MarketplaceOrderItem',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('variant_id', models.CharField(blank=True, default='', max_length=128)),
                ('quantity', models.PositiveIntegerField(default=1)),
                ('unit_price_cents', models.PositiveIntegerField()),
                ('selected_attributes', models.JSONField(blank=True, default=dict)),
                ('custom_description', models.TextField(blank=True)),
                ('order', models.ForeignKey(on_delete=models.CASCADE, related_name='items', to='commerce.marketplaceorder')),
                ('product', models.ForeignKey(on_delete=models.CASCADE, related_name='marketplace_order_items', to='commerce.product')),
            ],
            options={
                'indexes': [models.Index(fields=['order'], name='commerce_marketorderitem_order_idx')],
                'unique_together': {('order', 'product', 'variant_id')},
            },
        ),
        migrations.CreateModel(
            name='MarketplaceComplaint',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('text', models.TextField()),
                ('attachment', models.FileField(blank=True, null=True, upload_to='commerce/marketplace/complaints/')),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('reviewed', 'Reviewed'), ('resolved', 'Resolved')], default='pending', max_length=32)),
                ('order', models.ForeignKey(on_delete=models.CASCADE, related_name='complaints', to='commerce.marketplaceorder')),
                ('user', models.ForeignKey(on_delete=models.CASCADE, related_name='marketplace_complaints', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'indexes': [models.Index(fields=['order'], name='commerce_marketplacecomplaint_order_idx')],
            },
        ),
    ]
