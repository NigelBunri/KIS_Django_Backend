from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0028_user_tier_default_free'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='open_to_work',
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name='UserConnection',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'Pending'),
                        ('accepted', 'Accepted'),
                        ('rejected', 'Rejected'),
                        ('blocked', 'Blocked'),
                    ],
                    default='pending',
                    max_length=20,
                )),
                ('note', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('from_user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='sent_connections',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('to_user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='received_connections',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'unique_together': {('from_user', 'to_user')},
                'indexes': [
                    models.Index(fields=['to_user', 'status'], name='accounts_uc_to_user_status_idx'),
                    models.Index(fields=['from_user', 'status'], name='accounts_uc_from_user_status_idx'),
                ],
            },
        ),
    ]
