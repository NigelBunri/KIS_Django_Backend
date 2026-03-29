import uuid
from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.core.validators
from django.utils import timezone


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_clinical_ops_features"),
    ]

    operations = [
        migrations.CreateModel(
            name="InventoryItem",
            fields=[
                ("id", models.UUIDField(editable=False, primary_key=True, default=uuid.uuid4)),
                ("created_at", models.DateTimeField(editable=False, default=timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=200)),
                ("category", models.CharField(blank=True, max_length=120)),
                ("sku", models.CharField(blank=True, max_length=64)),
                ("unit", models.CharField(default="unit", max_length=32)),
                ("quantity_on_hand", models.DecimalField(default=Decimal("0.00"), decimal_places=2, max_digits=12)),
                ("reorder_level", models.DecimalField(default=Decimal("0.00"), decimal_places=2, max_digits=12)),
                ("status", models.CharField(choices=[("active", "Active"), ("inactive", "Inactive")], default="active", max_length=32)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="inventory_items",
                        to="core.healthcareorganization",
                    ),
                ),
                (
                    "profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="inventory_items",
                        to="core.medicalprofile",
                    ),
                ),
            ],
            options={
                "ordering": ["name"],
                "db_table": "core_inventory_item",
                "unique_together": {("profile", "name")},
            },
        ),
        migrations.CreateModel(
            name="DiagnosticOrder",
            fields=[
                ("id", models.UUIDField(editable=False, primary_key=True, default=uuid.uuid4)),
                ("created_at", models.DateTimeField(editable=False, default=timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("test_name", models.CharField(max_length=200)),
                ("status", models.CharField(choices=[("ordered", "Ordered"), ("processing", "Processing"), ("completed", "Completed"), ("cancelled", "Cancelled")], default="ordered", max_length=32)),
                ("specimen_collected_at", models.DateTimeField(blank=True, null=True)),
                ("results", models.JSONField(blank=True, default=dict)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "patient",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="diagnostic_orders",
                        to="core.patientmasterrecord",
                    ),
                ),
                (
                    "profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="core.medicalprofile",
                        null=True,
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        blank=True,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "db_table": "core_diagnostic_order",
            },
        ),
        migrations.CreateModel(
            name="ImagingStudy",
            fields=[
                ("id", models.UUIDField(editable=False, primary_key=True, default=uuid.uuid4)),
                ("created_at", models.DateTimeField(editable=False, default=timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("modality", models.CharField(max_length=64)),
                ("body_region", models.CharField(blank=True, max_length=120)),
                ("scheduled_at", models.DateTimeField(blank=True, null=True)),
                ("status", models.CharField(choices=[("scheduled", "Scheduled"), ("in_progress", "In Progress"), ("completed", "Completed"), ("cancelled", "Cancelled")], default="scheduled", max_length=32)),
                ("results_summary", models.TextField(blank=True)),
                ("result_files", models.JSONField(blank=True, default=list)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "patient",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="imaging_studies",
                        to="core.patientmasterrecord",
                    ),
                ),
                (
                    "profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="core.medicalprofile",
                        null=True,
                    ),
                ),
            ],
            options={
                "ordering": ["-scheduled_at"],
                "db_table": "core_imaging_study",
            },
        ),
        migrations.CreateModel(
            name="MedicationAdherenceReminder",
            fields=[
                ("id", models.UUIDField(editable=False, primary_key=True, default=uuid.uuid4)),
                ("created_at", models.DateTimeField(editable=False, default=timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("scheduled_at", models.DateTimeField()),
                ("status", models.CharField(choices=[("pending", "Pending"), ("sent", "Sent"), ("acknowledged", "Acknowledged")], default="pending", max_length=32)),
                ("channel", models.CharField(default="sms", max_length=64)),
                ("notes", models.TextField(blank=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "patient",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="adherence_reminders",
                        to="core.patientmasterrecord",
                    ),
                ),
                (
                    "medication_order",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="core.medicationorder",
                        null=True,
                        blank=True,
                    ),
                ),
            ],
            options={
                "ordering": ["scheduled_at"],
                "db_table": "core_adherence_reminder",
            },
        ),
        migrations.CreateModel(
            name="SupplyForecast",
            fields=[
                ("id", models.UUIDField(editable=False, primary_key=True, default=uuid.uuid4)),
                ("created_at", models.DateTimeField(editable=False, default=timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("category", models.CharField(max_length=128)),
                ("period_start", models.DateField()),
                ("period_end", models.DateField()),
                (
                    "predicted_usage",
                    models.DecimalField(
                        max_digits=14,
                        decimal_places=2,
                        validators=[django.core.validators.MinValueValidator(Decimal("0.00"))],
                    ),
                ),
                ("notes", models.TextField(blank=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="supply_forecasts",
                        to="core.medicalprofile",
                    ),
                ),
            ],
            options={
                "ordering": ["-period_start"],
                "db_table": "core_supply_forecast",
            },
        ),
    ]
