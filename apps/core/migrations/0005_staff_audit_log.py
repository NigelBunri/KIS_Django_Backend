from django.db import migrations, models
from django.conf import settings
import uuid
import django.utils.timezone


class Migration(migrations.Migration):
    initial = False

    dependencies = [
        ("core", "0004_healthcare_location_ward_service_equipment"),
    ]

    operations = [
        migrations.CreateModel(
            name="StaffAuditLog",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("action", models.CharField(max_length=80)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("note", models.TextField(blank=True)),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.SET_NULL,
                        related_name="staff_audits",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "staff",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="audits",
                        to="core.staffprofile",
                    ),
                ),
            ],
            options={
                "db_table": "core_staff_audit_log",
                "ordering": ["-created_at"],
            },
        ),
    ]
