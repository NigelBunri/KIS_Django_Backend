from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("partners", "0039_partnerorganizationappcontentblock_and_more"),
        ("accounts", "0028_user_tier_default_free"),
    ]

    operations = [
        migrations.CreateModel(
            name="PartnerLocationEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("active", "Active"), ("closed", "Closed"), ("cancelled", "Cancelled")], default="draft", max_length=16)),
                ("start_dt", models.DateTimeField()),
                ("end_dt", models.DateTimeField()),
                ("checkin_opens_before_minutes", models.PositiveSmallIntegerField(default=15)),
                ("late_after_minutes", models.PositiveSmallIntegerField(default=0)),
                ("recurrence", models.CharField(choices=[("once", "Once"), ("daily", "Daily"), ("weekly", "Weekly"), ("monthly", "Monthly"), ("custom", "Custom days")], default="once", max_length=16)),
                ("recurrence_days", models.JSONField(blank=True, default=list)),
                ("recurrence_until", models.DateField(blank=True, null=True)),
                ("target_type", models.CharField(choices=[("all", "All members"), ("roles", "Selected roles"), ("users", "Selected users"), ("group", "Messaging group"), ("community", "Community"), ("channel", "Channel / sub-room")], default="all", max_length=16)),
                ("target_roles", models.JSONField(blank=True, default=list)),
                ("target_user_ids", models.JSONField(blank=True, default=list)),
                ("target_ref_id", models.UUIDField(blank=True, null=True)),
                ("center_lat", models.DecimalField(decimal_places=6, max_digits=9)),
                ("center_lng", models.DecimalField(decimal_places=6, max_digits=9)),
                ("radius_meters", models.PositiveIntegerField(default=100)),
                ("show_arrival_order_to_members", models.BooleanField(default=False)),
                ("show_checkin_count_to_members", models.BooleanField(default=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("partner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="location_events", to="partners.partner")),
                ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_location_events", to="accounts.user")),
            ],
            options={"ordering": ["-start_dt"], "db_table": "location_event"},
        ),
        migrations.CreateModel(
            name="PartnerLocationZone",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=128)),
                ("center_lat", models.DecimalField(decimal_places=6, max_digits=9)),
                ("center_lng", models.DecimalField(decimal_places=6, max_digits=9)),
                ("radius_meters", models.PositiveIntegerField(default=50)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("event", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="extra_zones", to="location.partnerlocationevent")),
            ],
            options={"db_table": "location_zone"},
        ),
        migrations.CreateModel(
            name="PartnerLocationAttendance",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("checked_in_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("is_late", models.BooleanField(default=False)),
                ("arrival_number", models.PositiveIntegerField()),
                ("distance_from_center_m", models.PositiveIntegerField(default=0)),
                ("location_verified", models.BooleanField(default=True)),
                ("source", models.CharField(default="app", max_length=32)),
                ("device_os", models.CharField(blank=True, max_length=16)),
                ("is_manual", models.BooleanField(default=False)),
                ("manually_adjusted_at", models.DateTimeField(blank=True, null=True)),
                ("event", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="attendances", to="location.partnerlocationevent")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="location_attendances", to="accounts.user")),
                ("partner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="location_attendances", to="partners.partner")),
                ("manually_adjusted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="manual_checkins_performed", to="accounts.user")),
            ],
            options={"ordering": ["arrival_number"], "db_table": "location_attendance"},
        ),
        migrations.AddConstraint(
            model_name="partnerlocationattendance",
            constraint=models.UniqueConstraint(fields=["event", "user"], name="unique_attendance_per_event"),
        ),
        migrations.CreateModel(
            name="PartnerLocationConsent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("granted", models.BooleanField(default=False)),
                ("granted_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("is_minor", models.BooleanField(default=False)),
                ("notes", models.TextField(blank=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="location_consents", to="accounts.user")),
                ("partner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="location_consents", to="partners.partner")),
            ],
            options={"db_table": "location_consent"},
        ),
        migrations.AddConstraint(
            model_name="partnerlocationconsent",
            constraint=models.UniqueConstraint(fields=["user", "partner"], name="unique_consent_per_partner"),
        ),
        migrations.CreateModel(
            name="PartnerLocationAuditLog",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("action", models.CharField(choices=[("event_created", "Event created"), ("event_updated", "Event updated"), ("event_deleted", "Event deleted"), ("zone_changed", "Zone changed"), ("checkin_recorded", "Check-in recorded"), ("checkin_manual", "Manual check-in"), ("attendance_adjusted", "Attendance adjusted"), ("report_exported", "Report exported"), ("consent_granted", "Consent granted"), ("consent_revoked", "Consent revoked")], max_length=32)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("partner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="location_audit_logs", to="partners.partner")),
                ("event", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="audit_logs", to="location.partnerlocationevent")),
                ("actor", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="location_audit_actions", to="accounts.user")),
                ("target_user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="location_audit_targets", to="accounts.user")),
            ],
            options={"ordering": ["-created_at"], "db_table": "location_audit_log"},
        ),
    ]
