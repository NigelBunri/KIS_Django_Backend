from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0029_profile_open_to_work_userconnection'),
    ]

    operations = [
        # Add new fields to Device model
        migrations.AddField(
            model_name='device',
            name='is_parent',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='device',
            name='linked_via_qr',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='device',
            name='parent_device',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='secondary_devices',
                to='accounts.device',
            ),
        ),
        migrations.AddField(
            model_name='device',
            name='nickname',
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name='device',
            name='trusted_until',
            field=models.DateTimeField(blank=True, null=True),
        ),
        # Create the DeviceQRToken model
        migrations.CreateModel(
            name='DeviceQRToken',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token_hash', models.CharField(max_length=64)),
                ('nonce', models.CharField(max_length=64)),
                ('issued_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField()),
                ('used_at', models.DateTimeField(blank=True, null=True)),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='qr_tokens',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('parent_device', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='generated_qr_tokens',
                    to='accounts.device',
                )),
                ('used_by_device', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='qr_login_used',
                    to='accounts.device',
                )),
            ],
            options={
                'indexes': [
                    models.Index(fields=['user', 'expires_at'], name='accounts_de_user_id_456775_idx'),
                    models.Index(fields=['token_hash'], name='accounts_de_token_h_09f6e8_idx'),
                ],
            },
        ),
    ]
