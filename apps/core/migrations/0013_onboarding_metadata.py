from __future__ import annotations

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0012_rename_core_compliance_a_actor_action_idx_core_compli_actor_i_bc364e_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="healthcareorganization",
            name="is_deleted",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="healthcareorganization",
            name="onboarding_status",
            field=models.CharField(default="draft", max_length=32),
        ),
        migrations.AddField(
            model_name="healthcareorganization",
            name="onboarding_metadata",
            field=models.JSONField(default=dict, blank=True),
        ),
        migrations.AddField(
            model_name="healthcareorganization",
            name="verified_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="verified_medical_organizations",
                to="accounts.user",
            ),
        ),
        migrations.AddField(
            model_name="healthcareorganization",
            name="last_status_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="healthcareorganization",
            name="document_expiry",
            field=models.JSONField(default=dict, blank=True),
        ),
        migrations.AddField(
            model_name="healthcareorganization",
            name="compliance_officer",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="healthcareorganization",
            name="risk_summary",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="healthcareorganization",
            name="security_notes",
            field=models.JSONField(default=dict, blank=True),
        ),
        migrations.AddField(
            model_name="medicalprofile",
            name="onboarding_status",
            field=models.CharField(default="draft", max_length=32),
        ),
        migrations.AddField(
            model_name="medicalprofile",
            name="compliance_documents",
            field=models.JSONField(default=dict, blank=True),
        ),
        migrations.AddField(
            model_name="medicalprofile",
            name="audit_entries",
            field=models.JSONField(default=list, blank=True),
        ),
        migrations.AddField(
            model_name="medicalprofile",
            name="review_notes",
            field=models.TextField(blank=True, default=""),
        ),
    ]
