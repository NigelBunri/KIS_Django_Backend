from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0009_medical_resources_phase2"),
    ]

    operations = [
        migrations.CreateModel(
            name="ComplianceAuditLog",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("action", models.CharField(max_length=200)),
                ("target_type", models.CharField(blank=True, max_length=64)),
                ("target_id", models.CharField(blank=True, max_length=64)),
                ("severity", models.CharField(choices=[("low", "Low"), ("medium", "Medium"), ("high", "High"), ("critical", "Critical")], default="medium", max_length=16)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="compliance_audit_logs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "core_compliance_audit_log",
                "indexes": [
                    models.Index(fields=["actor", "action"], name="core_compliance_a_actor_action_idx"),
                    models.Index(fields=["severity"], name="core_compliance_a_severity_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="CredentialVerification",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("credential_type", models.CharField(max_length=120)),
                ("license_number", models.CharField(max_length=120)),
                ("issuing_body", models.CharField(blank=True, max_length=128)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("verified", "Verified"), ("expired", "Expired"), ("revoked", "Revoked")], default="pending", max_length=32)),
                ("issued_at", models.DateField(blank=True, null=True)),
                ("expires_at", models.DateField(blank=True, null=True)),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
                ("notes", models.TextField(blank=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "staff_profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="credentials",
                        to="core.staffprofile",
                    ),
                ),
                (
                    "verified_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="verified_credentials",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "core_credential_verification",
                "ordering": ["-expires_at"],
                "unique_together": {("staff_profile", "credential_type", "license_number")},
            },
        ),
        migrations.CreateModel(
            name="RegulatoryReport",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("report_type", models.CharField(max_length=120)),
                ("period_start", models.DateField()),
                ("period_end", models.DateField()),
                ("status", models.CharField(choices=[("draft", "Draft"), ("submitted", "Submitted"), ("reviewed", "Reviewed")], default="draft", max_length=32)),
                ("data_payload", models.JSONField(blank=True, default=dict)),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="regulatory_reports",
                        to="core.healthcareorganization",
                    ),
                ),
                (
                    "profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="regulatory_reports",
                        to="core.medicalprofile",
                    ),
                ),
            ],
            options={
                "db_table": "core_regulatory_report",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="ComplianceDocument",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("document_name", models.CharField(max_length=160)),
                ("file_path", models.CharField(blank=True, max_length=400)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("signed", "Signed"), ("archived", "Archived")], default="draft", max_length=32)),
                ("is_signed", models.BooleanField(default=False)),
                ("signed_at", models.DateTimeField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="compliance_documents",
                        to="core.healthcareorganization",
                    ),
                ),
                (
                    "profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="compliance_documents",
                        to="core.medicalprofile",
                    ),
                ),
                (
                    "signed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="signed_documents",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "core_compliance_document",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="DataAccessConsent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("granted_to", models.CharField(max_length=128)),
                ("scope", models.CharField(max_length=200)),
                ("status", models.CharField(choices=[("active", "Active"), ("revoked", "Revoked"), ("expired", "Expired")], default="active", max_length=32)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "patient",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="data_access_consents",
                        to="core.patientmasterrecord",
                    ),
                ),
            ],
            options={
                "db_table": "core_data_access_consent",
                "ordering": ["-created_at"],
            },
        ),
    ]
