from django.db import migrations, models
import uuid
import django.utils.timezone


class Migration(migrations.Migration):
    initial = False

    dependencies = [
        ("core", "0003_patientmasterrecord_patientfamilyprofile_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="Location",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("label", models.CharField(max_length=160)),
                ("address", models.JSONField(blank=True, default=dict)),
                ("is_primary", models.BooleanField(default=False)),
                ("timezone", models.CharField(blank=True, max_length=64)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="locations",
                        to="core.healthcareorganization",
                    ),
                ),
                (
                    "profile",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.CASCADE,
                        related_name="locations",
                        to="core.medicalprofile",
                    ),
                ),
            ],
            options={
                "db_table": "core_location",
                "ordering": ["label"],
                "indexes": [
                    models.Index(
                        fields=["organization", "profile"],
                        name="core_location_org_profile_idx",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="Ward",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=120)),
                ("capacity", models.PositiveIntegerField(default=0)),
                ("is_isolation", models.BooleanField(default=False)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "location",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="wards",
                        to="core.location",
                    ),
                ),
            ],
            options={
                "db_table": "core_ward",
                "ordering": ["name"],
                "unique_together": {("location", "name")},
            },
        ),
        migrations.CreateModel(
            name="Service",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=140)),
                ("category", models.CharField(blank=True, max_length=80)),
                ("description", models.TextField(blank=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "department",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.SET_NULL,
                        related_name="department_services",
                        related_query_name="department_service",
                        to="core.department",
                    ),
                ),
                (
                    "profile",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="services",
                        to="core.medicalprofile",
                    ),
                ),
            ],
            options={
                "db_table": "core_service",
                "ordering": ["name"],
                "unique_together": {("profile", "name")},
            },
        ),
        migrations.CreateModel(
            name="Equipment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=140)),
                ("equipment_type", models.CharField(blank=True, max_length=80)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("active", "Active"),
                            ("maintenance", "Maintenance"),
                            ("offline", "Offline"),
                        ],
                        default="active",
                        max_length=32,
                    ),
                ),
                ("last_service_at", models.DateTimeField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "profile",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="equipment",
                        to="core.medicalprofile",
                    ),
                ),
                (
                    "ward",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.SET_NULL,
                        related_name="equipment",
                        to="core.ward",
                    ),
                ),
            ],
            options={
                "db_table": "core_equipment",
                "ordering": ["name"],
                "unique_together": {("profile", "name")},
            },
        ),
    ]
