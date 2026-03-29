from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("admin_control", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AdminAuditEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name="admin_control_actions", to=settings.AUTH_USER_MODEL)),
                ("action", models.CharField(max_length=128)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("action_type", models.CharField(max_length=64)),
                ("target_app", models.CharField(blank=True, max_length=128)),
                ("target_model", models.CharField(blank=True, max_length=128)),
                ("target_pk", models.CharField(blank=True, max_length=64)),
                ("severity", models.CharField(choices=[("info", "Info"), ("warning", "Warning"), ("critical", "Critical")], default="info", max_length=16)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="SuspiciousActivityFlag",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("reason", models.CharField(max_length=256)),
                ("path", models.CharField(blank=True, max_length=512)),
                ("severity", models.CharField(choices=[("info", "Info"), ("warning", "Warning"), ("critical", "Critical")], default="warning", max_length=16)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("resolved", models.BooleanField(default=False, db_index=True)),
                ("acknowledged_at", models.DateTimeField(blank=True, null=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name="suspicious_flags", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
