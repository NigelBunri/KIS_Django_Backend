import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Petition
        migrations.CreateModel(
            name="Petition",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(max_length=255)),
                ("description", models.TextField()),
                ("target", models.CharField(max_length=255)),
                ("target_count", models.IntegerField(default=1000)),
                ("signatures_count", models.IntegerField(default=0)),
                ("deadline", models.DateField(blank=True, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("active", "Active"),
                            ("closed", "Closed"),
                            ("won", "Won"),
                        ],
                        default="active",
                        max_length=20,
                    ),
                ),
                (
                    "category",
                    models.CharField(
                        choices=[
                            ("civic", "Civic"),
                            ("faith", "Faith"),
                            ("business", "Business"),
                            ("education", "Education"),
                            ("health", "Health"),
                            ("general", "General"),
                        ],
                        default="general",
                        max_length=20,
                    ),
                ),
                ("country", models.CharField(blank=True, max_length=100)),
                (
                    "creator",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="petitions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        # PetitionSignature
        migrations.CreateModel(
            name="PetitionSignature",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_anonymous", models.BooleanField(default=False)),
                ("comment", models.TextField(blank=True)),
                (
                    "petition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="signatures",
                        to="government.petition",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="petition_signatures",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-created_at"], "unique_together": {("petition", "user")}},
        ),
        # CivicPoll
        migrations.CreateModel(
            name="CivicPoll",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("question", models.TextField()),
                ("options", models.JSONField(default=list)),
                (
                    "scope",
                    models.CharField(
                        choices=[
                            ("neighborhood", "Neighborhood"),
                            ("church", "Church"),
                            ("organization", "Organization"),
                            ("regional", "Regional"),
                            ("national", "National"),
                        ],
                        default="organization",
                        max_length=20,
                    ),
                ),
                ("start_date", models.DateTimeField()),
                ("end_date", models.DateTimeField()),
                ("is_public", models.BooleanField(default=True)),
                ("church_id", models.UUIDField(blank=True, null=True)),
                ("org_id", models.UUIDField(blank=True, null=True)),
                (
                    "creator",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="civic_polls",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        # PollVote
        migrations.CreateModel(
            name="PollVote",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("option_key", models.CharField(max_length=50)),
                (
                    "poll",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="votes",
                        to="government.civicpoll",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="poll_votes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"unique_together": {("poll", "user")}},
        ),
        # LegalAidEntry
        migrations.CreateModel(
            name="LegalAidEntry",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=255)),
                ("specialty", models.CharField(max_length=255)),
                ("country", models.CharField(max_length=100)),
                ("state", models.CharField(blank=True, max_length=100)),
                ("city", models.CharField(blank=True, max_length=100)),
                ("contact_phone", models.CharField(blank=True, max_length=50)),
                ("contact_email", models.EmailField(blank=True)),
                ("website", models.URLField(blank=True)),
                ("description", models.TextField(blank=True)),
                ("is_pro_bono", models.BooleanField(default=False)),
                ("languages", models.JSONField(default=list)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="legal_aid_entries",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["name"], "verbose_name_plural": "Legal Aid Entries"},
        ),
        # LegalDocumentTemplate
        migrations.CreateModel(
            name="LegalDocumentTemplate",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(max_length=255)),
                ("type", models.CharField(max_length=100)),
                ("country", models.CharField(blank=True, max_length=100)),
                ("content_markdown", models.TextField()),
                ("description", models.TextField(blank=True)),
                ("is_public", models.BooleanField(default=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="legal_doc_templates",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["title"]},
        ),
        # DiasporaCommunity
        migrations.CreateModel(
            name="DiasporaCommunity",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=255)),
                ("country_of_origin", models.CharField(max_length=100)),
                ("host_country", models.CharField(max_length=100)),
                ("description", models.TextField(blank=True)),
                ("member_count", models.IntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                ("banner_url", models.URLField(blank=True)),
                (
                    "founder",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="founded_diaspora_communities",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["name"], "verbose_name_plural": "Diaspora Communities"},
        ),
        # DiasporaCommunityMembership
        migrations.CreateModel(
            name="DiasporaCommunityMembership",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("join_date", models.DateTimeField(auto_now_add=True)),
                (
                    "community",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="memberships",
                        to="government.diasporacommunity",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="diaspora_memberships",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"unique_together": {("community", "user")}},
        ),
        # NGOProfile
        migrations.CreateModel(
            name="NGOProfile",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=255)),
                ("reg_number", models.CharField(blank=True, max_length=100)),
                ("country", models.CharField(max_length=100)),
                ("focus_areas", models.JSONField(default=list)),
                ("description", models.TextField(blank=True)),
                ("contact_email", models.EmailField(blank=True)),
                ("website", models.URLField(blank=True)),
                ("is_verified", models.BooleanField(default=False)),
                ("annual_budget", models.DecimalField(blank=True, decimal_places=2, max_digits=15, null=True)),
                (
                    "owner",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="ngo_profiles",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["name"]},
        ),
        # GrantApplication
        migrations.CreateModel(
            name="GrantApplication",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(max_length=255)),
                ("description", models.TextField()),
                ("amount_requested", models.DecimalField(decimal_places=2, max_digits=15)),
                ("currency", models.CharField(default="USD", max_length=3)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("submitted", "Submitted"),
                            ("under_review", "Under Review"),
                            ("approved", "Approved"),
                            ("rejected", "Rejected"),
                        ],
                        default="draft",
                        max_length=20,
                    ),
                ),
                ("grant_source", models.CharField(blank=True, max_length=255)),
                ("submission_date", models.DateField(blank=True, null=True)),
                ("supporting_docs", models.JSONField(default=list)),
                (
                    "ngo",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="grant_applications",
                        to="government.ngoprofile",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="grant_applications",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        # ComplianceDeadline
        migrations.CreateModel(
            name="ComplianceDeadline",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("org_id", models.UUIDField()),
                ("title", models.CharField(max_length=255)),
                (
                    "type",
                    models.CharField(
                        choices=[
                            ("filing", "Filing"),
                            ("renewal", "Renewal"),
                            ("report", "Report"),
                            ("license", "License"),
                            ("tax", "Tax"),
                        ],
                        default="filing",
                        max_length=20,
                    ),
                ),
                ("due_date", models.DateField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("complete", "Complete"),
                            ("overdue", "Overdue"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("notes", models.TextField(blank=True)),
                ("reminder_sent", models.BooleanField(default=False)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="compliance_deadlines",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["due_date"]},
        ),
        # WhistleblowerReport
        migrations.CreateModel(
            name="WhistleblowerReport",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("encrypted_content", models.TextField()),
                ("submitted_at", models.DateTimeField(auto_now_add=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("received", "Received"),
                            ("reviewing", "Reviewing"),
                            ("resolved", "Resolved"),
                        ],
                        default="received",
                        max_length=20,
                    ),
                ),
                (
                    "category",
                    models.CharField(
                        choices=[
                            ("corruption", "Corruption"),
                            ("fraud", "Fraud"),
                            ("abuse", "Abuse"),
                            ("safety", "Safety"),
                            ("other", "Other"),
                        ],
                        max_length=20,
                    ),
                ),
                ("case_ref", models.CharField(max_length=16, unique=True)),
                ("country", models.CharField(blank=True, max_length=100)),
            ],
            options={"ordering": ["-submitted_at"]},
        ),
        # BoardElection
        migrations.CreateModel(
            name="BoardElection",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("org_id", models.UUIDField()),
                ("title", models.CharField(max_length=255)),
                ("candidates", models.JSONField(default=list)),
                ("start_date", models.DateTimeField()),
                ("end_date", models.DateTimeField()),
                ("results", models.JSONField(default=dict)),
                ("is_closed", models.BooleanField(default=False)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="board_elections",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        # ElectionVote
        migrations.CreateModel(
            name="ElectionVote",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("candidate_index", models.IntegerField()),
                (
                    "election",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="votes",
                        to="government.boardelection",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="election_votes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"unique_together": {("election", "user")}},
        ),
        # BoardResolution
        migrations.CreateModel(
            name="BoardResolution",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("org_id", models.UUIDField()),
                ("title", models.CharField(max_length=255)),
                ("content", models.TextField()),
                ("meeting_date", models.DateField()),
                ("passed", models.BooleanField(null=True)),
                ("vote_results", models.JSONField(default=dict)),
                ("tags", models.JSONField(default=list)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="board_resolutions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-meeting_date"]},
        ),
    ]
