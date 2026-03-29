from django.conf import settings
from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_telemed_reminder_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="ClinicalTask",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False, editable=False)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("title", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("in_progress", "In Progress"), ("completed", "Completed")], default="pending", max_length=32)),
                ("priority", models.CharField(choices=[("low", "Low"), ("medium", "Medium"), ("high", "High")], default="medium", max_length=16)),
                ("due_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("patient", models.ForeignKey(on_delete=models.CASCADE, related_name="clinical_tasks", to="core.patientmasterrecord")),
                ("assigned_to", models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name="clinical_tasks", to=settings.AUTH_USER_MODEL)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name="created_clinical_tasks", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "core_clinical_task", "ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="EmergencyEscalation",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False, editable=False)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("severity", models.CharField(choices=[("low", "Low"), ("medium", "Medium"), ("high", "High"), ("critical", "Critical")], default="medium", max_length=16)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("escalated", "Escalated"), ("resolved", "Resolved")], default="pending", max_length=16)),
                ("summary", models.TextField()),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("patient", models.ForeignKey(on_delete=models.CASCADE, related_name="escalations", to="core.patientmasterrecord")),
                ("reported_by", models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name="reported_escalations", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "core_emergency_escalation", "ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="TriageRecord",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False, editable=False)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("symptoms", models.JSONField(blank=True, default=list)),
                ("acuity_level", models.CharField(choices=[("routine", "Routine"), ("elevated", "Elevated"), ("urgent", "Urgent")], default="routine", max_length=32)),
                ("recommended_unit", models.CharField(blank=True, max_length=128)),
                ("ai_response", models.JSONField(blank=True, default=dict)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("patient", models.ForeignKey(on_delete=models.CASCADE, related_name="triage_records", to="core.patientmasterrecord")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name="triage_requests", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "core_triage_record", "ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="ReferralRoute",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False, editable=False)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("reason", models.CharField(max_length=400)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("accepted", "Accepted"), ("declined", "Declined")], default="pending", max_length=32)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("patient", models.ForeignKey(on_delete=models.CASCADE, related_name="referrals", to="core.patientmasterrecord")),
                ("from_organization", models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name="referrals_from", to="core.healthcareorganization")),
                ("to_organization", models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name="referrals_to", to="core.healthcareorganization")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name="created_referrals", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "core_referral_route", "ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="ClinicalEventLog",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False, editable=False)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("event_type", models.CharField(choices=[("task_created", "Task Created"), ("task_completed", "Task Completed"), ("escalation", "Escalation"), ("triage", "Triage"), ("referral", "Referral")], max_length=64)),
                ("description", models.TextField()),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("patient", models.ForeignKey(on_delete=models.CASCADE, related_name="clinical_event_logs", to="core.patientmasterrecord")),
                ("triggered_by", models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name="triggered_clinical_events", to=settings.AUTH_USER_MODEL)),
                ("task", models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name="events", to="core.clinicaltask")),
                ("escalation", models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name="events", to="core.emergencyescalation")),
                ("triage", models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name="events", to="core.triagerecord")),
                ("referral", models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name="events", to="core.referralroute")),
            ],
            options={"db_table": "core_clinical_event", "ordering": ["-created_at"]},
        ),
    ]
