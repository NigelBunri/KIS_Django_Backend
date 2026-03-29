from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AdminUserActivity",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=models.PROTECT, related_name="admin_control_actions", to=settings.AUTH_USER_MODEL)),
                ("action", models.CharField(max_length=128)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("path", models.CharField(max_length=512)),
                ("method", models.CharField(max_length=10)),
                ("status_code", models.PositiveSmallIntegerField(db_index=True, default=200)),
                ("ip_address", models.GenericIPAddressField(default="0.0.0.0")),
                ("device", models.CharField(blank=True, max_length=512)),
                ("duration_ms", models.FloatField(default=0.0)),
                ("response_size", models.PositiveIntegerField(default=0)),
                ("is_deleted", models.BooleanField(db_index=True, default=False)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
