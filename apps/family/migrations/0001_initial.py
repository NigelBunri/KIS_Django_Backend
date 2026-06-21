# Generated migration for apps.family

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
        # -----------------------------------------------------------------
        # FamilyAccount
        # -----------------------------------------------------------------
        migrations.CreateModel(
            name="FamilyAccount",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True)),
                (
                    "invite_code",
                    models.CharField(max_length=12, unique=True),
                ),
                ("is_active", models.BooleanField(default=True)),
                (
                    "admin_user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="administered_families",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "family_account",
                "abstract": False,
            },
        ),
        # -----------------------------------------------------------------
        # FamilyMember
        # -----------------------------------------------------------------
        migrations.CreateModel(
            name="FamilyMember",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("parent", "Parent"),
                            ("child", "Child"),
                            ("guardian", "Guardian"),
                            ("grandparent", "Grandparent"),
                            ("extended", "Extended"),
                        ],
                        max_length=20,
                    ),
                ),
                ("nickname", models.CharField(blank=True, max_length=100)),
                ("birth_date", models.DateField(blank=True, null=True)),
                ("is_admin", models.BooleanField(default=False)),
                ("is_minor", models.BooleanField(default=False)),
                (
                    "family",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="members",
                        to="family.familyaccount",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="family_memberships",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "added_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="added_family_members",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "family_member",
                "abstract": False,
                "unique_together": {("family", "user")},
            },
        ),
        # -----------------------------------------------------------------
        # ParentalControl
        # -----------------------------------------------------------------
        migrations.CreateModel(
            name="ParentalControl",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "content_filter_level",
                    models.CharField(
                        choices=[
                            ("child", "Child"),
                            ("youth", "Youth"),
                            ("adult", "Adult"),
                        ],
                        default="child",
                        max_length=10,
                    ),
                ),
                ("allowed_contact_ids", models.JSONField(default=list)),
                (
                    "screen_time_minutes_per_day",
                    models.IntegerField(blank=True, null=True),
                ),
                ("time_restrictions", models.JSONField(default=dict)),
                ("restricted_sections", models.JSONField(default=list)),
                ("location_sharing", models.BooleanField(default=False)),
                ("sos_enabled", models.BooleanField(default=True)),
                (
                    "activity_report_frequency",
                    models.CharField(
                        choices=[
                            ("daily", "Daily"),
                            ("weekly", "Weekly"),
                            ("monthly", "Monthly"),
                        ],
                        default="weekly",
                        max_length=10,
                    ),
                ),
                (
                    "family_member",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="parental_control",
                        to="family.familymember",
                    ),
                ),
            ],
            options={
                "db_table": "family_parental_control",
                "abstract": False,
            },
        ),
        # -----------------------------------------------------------------
        # FamilyEvent
        # -----------------------------------------------------------------
        migrations.CreateModel(
            name="FamilyEvent",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(max_length=255)),
                (
                    "type",
                    models.CharField(
                        choices=[
                            ("birthday", "Birthday"),
                            ("anniversary", "Anniversary"),
                            ("school", "School"),
                            ("church", "Church"),
                            ("appointment", "Appointment"),
                            ("other", "Other"),
                        ],
                        max_length=20,
                    ),
                ),
                ("event_date", models.DateTimeField()),
                ("reminder_minutes_before", models.IntegerField(default=60)),
                ("notes", models.TextField(blank=True)),
                (
                    "family",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="events",
                        to="family.familyaccount",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_family_events",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "family_event",
                "ordering": ["event_date"],
                "abstract": False,
            },
        ),
        # -----------------------------------------------------------------
        # FamilyPhotoAlbum
        # -----------------------------------------------------------------
        migrations.CreateModel(
            name="FamilyPhotoAlbum",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True)),
                ("cover_url", models.URLField(blank=True)),
                (
                    "family",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="photo_albums",
                        to="family.familyaccount",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_family_albums",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "family_photo_album",
                "abstract": False,
            },
        ),
        # -----------------------------------------------------------------
        # FamilyPhoto
        # -----------------------------------------------------------------
        migrations.CreateModel(
            name="FamilyPhoto",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("file_url", models.URLField()),
                ("caption", models.TextField(blank=True)),
                ("taken_at", models.DateTimeField(blank=True, null=True)),
                (
                    "album",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="photos",
                        to="family.familyphotoalbum",
                    ),
                ),
                (
                    "uploader",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="uploaded_family_photos",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "family_photo",
                "abstract": False,
            },
        ),
        # -----------------------------------------------------------------
        # MemorialPage
        # -----------------------------------------------------------------
        migrations.CreateModel(
            name="MemorialPage",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=255)),
                ("birth_date", models.DateField(blank=True, null=True)),
                ("death_date", models.DateField(blank=True, null=True)),
                ("tribute", models.TextField(blank=True)),
                ("photo_url", models.URLField(blank=True)),
                ("is_public", models.BooleanField(default=False)),
                (
                    "family",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="memorial_pages",
                        to="family.familyaccount",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_memorial_pages",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "family_memorial_page",
                "abstract": False,
            },
        ),
        # -----------------------------------------------------------------
        # MilestoneRecord
        # -----------------------------------------------------------------
        migrations.CreateModel(
            name="MilestoneRecord",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(max_length=255)),
                (
                    "type",
                    models.CharField(
                        choices=[
                            ("first_steps", "First Steps"),
                            ("graduation", "Graduation"),
                            ("baptism", "Baptism"),
                            ("marriage", "Marriage"),
                            ("birth", "Birth"),
                            ("achievement", "Achievement"),
                            ("other", "Other"),
                        ],
                        max_length=20,
                    ),
                ),
                ("milestone_date", models.DateField()),
                ("description", models.TextField(blank=True)),
                ("photo_url", models.URLField(blank=True)),
                (
                    "family_member",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="milestones",
                        to="family.familymember",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_milestones",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "family_milestone_record",
                "ordering": ["-milestone_date"],
                "abstract": False,
            },
        ),
        # -----------------------------------------------------------------
        # TimeCapsule
        # -----------------------------------------------------------------
        migrations.CreateModel(
            name="TimeCapsule",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(max_length=255)),
                ("message", models.TextField()),
                ("unlock_date", models.DateField()),
                ("is_locked", models.BooleanField(default=True)),
                ("media_urls", models.JSONField(default=list)),
                (
                    "family",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="time_capsules",
                        to="family.familyaccount",
                    ),
                ),
                (
                    "creator",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_time_capsules",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "family_time_capsule",
                "abstract": False,
            },
        ),
        # -----------------------------------------------------------------
        # FamilyPrayerItem
        # -----------------------------------------------------------------
        migrations.CreateModel(
            name="FamilyPrayerItem",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("text", models.TextField()),
                ("is_answered", models.BooleanField(default=False)),
                ("answered_note", models.TextField(blank=True)),
                (
                    "family",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="prayer_items",
                        to="family.familyaccount",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="family_prayer_items",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "family_prayer_item",
                "abstract": False,
            },
        ),
        # -----------------------------------------------------------------
        # FamilyNoticeBoard
        # -----------------------------------------------------------------
        migrations.CreateModel(
            name="FamilyNoticeBoard",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(max_length=255)),
                ("content", models.TextField()),
                ("is_pinned", models.BooleanField(default=False)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                (
                    "family",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notices",
                        to="family.familyaccount",
                    ),
                ),
                (
                    "posted_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="posted_family_notices",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "family_notice_board",
                "ordering": ["-is_pinned", "-created_at"],
                "abstract": False,
            },
        ),
        # -----------------------------------------------------------------
        # GriefSupportGroup
        # -----------------------------------------------------------------
        migrations.CreateModel(
            name="GriefSupportGroup",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=255)),
                (
                    "type",
                    models.CharField(
                        choices=[
                            ("spouse", "Spouse"),
                            ("child", "Child"),
                            ("parent", "Parent"),
                            ("sibling", "Sibling"),
                            ("general", "General"),
                        ],
                        max_length=20,
                    ),
                ),
                ("description", models.TextField(blank=True)),
                ("is_anonymous_allowed", models.BooleanField(default=True)),
                ("member_count", models.IntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "facilitator",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="facilitated_grief_groups",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "family_grief_support_group",
                "abstract": False,
            },
        ),
        # -----------------------------------------------------------------
        # GriefGroupMembership
        # -----------------------------------------------------------------
        migrations.CreateModel(
            name="GriefGroupMembership",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_anonymous", models.BooleanField(default=False)),
                ("join_date", models.DateTimeField(auto_now_add=True)),
                (
                    "group",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="memberships",
                        to="family.griefsupportgroup",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="grief_group_memberships",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "family_grief_group_membership",
                "abstract": False,
                "unique_together": {("group", "user")},
            },
        ),
    ]
