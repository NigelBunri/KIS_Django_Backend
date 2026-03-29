from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("analytics", "0001_initial"),
        ("core", "0003_patientmasterrecord_patientfamilyprofile_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ClinicalAnalyticsReport",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("report_type", models.CharField(choices=[("clinical_summary", "Clinical summary"), ("population_health", "Population health")], max_length=32)),
                ("summary", models.TextField(blank=True)),
                ("metrics", models.JSONField(blank=True, default=dict)),
                ("period_start", models.DateField()),
                ("period_end", models.DateField()),
                ("status", models.CharField(default="draft", max_length=32)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="analytics_reports",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="analytics_reports",
                        to="core.healthcareorganization",
                    ),
                ),
                (
                    "profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="analytics_reports",
                        to="core.medicalprofile",
                    ),
                ),
            ],
            options={
                "db_table": "analytics_clinical_report",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="RiskStratification",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("score", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=5)),
                ("level", models.CharField(choices=[("low", "Low"), ("moderate", "Moderate"), ("high", "High"), ("critical", "Critical")], default="low", max_length=16)),
                ("drivers", models.JSONField(blank=True, default=list)),
                ("assessed_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "patient",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="risk_assessments",
                        to="core.patientmasterrecord",
                    ),
                ),
                (
                    "profile",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="risk_assessments",
                        to="core.medicalprofile",
                    ),
                ),
            ],
            options={
                "db_table": "analytics_risk_stratification",
                "ordering": ["-assessed_at"],
            },
        ),
        migrations.CreateModel(
            name="OutcomeBenchmark",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("metric_name", models.CharField(max_length=120)),
                ("actual_value", models.DecimalField(decimal_places=2, max_digits=10)),
                ("target_value", models.DecimalField(decimal_places=2, max_digits=10)),
                ("period_start", models.DateField()),
                ("period_end", models.DateField()),
                ("notes", models.TextField(blank=True)),
                (
                    "profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="outcome_benchmarks",
                        to="core.medicalprofile",
                    ),
                ),
            ],
            options={
                "db_table": "analytics_outcome_benchmark",
                "ordering": ["-period_start"],
            },
        ),
        migrations.CreateModel(
            name="PatientSatisfactionScore",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("score", models.IntegerField(default=0)),
                ("channel", models.CharField(choices=[("app", "App"), ("sms", "SMS"), ("email", "Email"), ("call", "Call")], default="app", max_length=32)),
                ("comments", models.TextField(blank=True)),
                ("recorded_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("status", models.CharField(default="completed", max_length=32)),
                (
                    "patient",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="satisfaction_scores",
                        to="core.patientmasterrecord",
                    ),
                ),
                (
                    "profile",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="satisfaction_scores",
                        to="core.medicalprofile",
                    ),
                ),
            ],
            options={
                "db_table": "analytics_patient_satisfaction",
                "ordering": ["-recorded_at"],
            },
        ),
        migrations.CreateModel(
            name="OutreachCampaign",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("name", models.CharField(max_length=160)),
                ("channel", models.CharField(max_length=64)),
                ("target_population", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(choices=[("planned", "Planned"), ("active", "Active"), ("completed", "Completed")], default="planned", max_length=32)),
                ("launched_at", models.DateTimeField(blank=True, null=True)),
                ("metrics", models.JSONField(blank=True, default=dict)),
                (
                    "profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="outreach_campaigns",
                        to="core.medicalprofile",
                    ),
                ),
            ],
            options={
                "db_table": "analytics_outreach_campaign",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="WellnessChallenge",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("title", models.CharField(max_length=160)),
                ("description", models.TextField(blank=True)),
                ("start_date", models.DateField()),
                ("end_date", models.DateField()),
                ("goal", models.CharField(max_length=200)),
                ("participation_target", models.PositiveIntegerField(default=0)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="wellness_challenges",
                        to="core.medicalprofile",
                    ),
                ),
            ],
            options={
                "db_table": "analytics_wellness_challenge",
                "ordering": ["-start_date"],
            },
        ),
        migrations.CreateModel(
            name="HabitTrackingEntry",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("habit_name", models.CharField(max_length=120)),
                ("progress_value", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=8)),
                ("notes", models.TextField(blank=True)),
                ("logged_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "challenge",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="habit_entries",
                        to="analytics.wellnesschallenge",
                    ),
                ),
                (
                    "patient",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="habit_entries",
                        to="core.patientmasterrecord",
                    ),
                ),
            ],
            options={
                "db_table": "analytics_habit_tracking",
                "ordering": ["-logged_at"],
            },
        ),
    ]
