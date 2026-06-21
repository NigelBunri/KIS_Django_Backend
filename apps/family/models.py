# apps/family/models.py
import uuid
from django.db import models
from django.utils import timezone

from apps.accounts.models import User


class TimeStampedUUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# ---------------------------------------------------------------------------
# FamilyAccount
# ---------------------------------------------------------------------------

class FamilyAccount(TimeStampedUUIDModel):
    admin_user = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="administered_families",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    invite_code = models.CharField(max_length=12, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "family_account"

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# FamilyMember
# ---------------------------------------------------------------------------

class MemberRole(models.TextChoices):
    PARENT = "parent", "Parent"
    CHILD = "child", "Child"
    GUARDIAN = "guardian", "Guardian"
    GRANDPARENT = "grandparent", "Grandparent"
    EXTENDED = "extended", "Extended"


class FamilyMember(TimeStampedUUIDModel):
    family = models.ForeignKey(
        FamilyAccount,
        on_delete=models.CASCADE,
        related_name="members",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="family_memberships",
    )
    role = models.CharField(max_length=20, choices=MemberRole.choices)
    nickname = models.CharField(max_length=100, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    is_admin = models.BooleanField(default=False)
    is_minor = models.BooleanField(default=False)
    added_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="added_family_members",
    )

    class Meta:
        db_table = "family_member"
        unique_together = [["family", "user"]]

    def __str__(self):
        return f"{self.nickname or self.user} in {self.family}"


# ---------------------------------------------------------------------------
# ParentalControl
# ---------------------------------------------------------------------------

class ContentFilterLevel(models.TextChoices):
    CHILD = "child", "Child"
    YOUTH = "youth", "Youth"
    ADULT = "adult", "Adult"


class ActivityReportFrequency(models.TextChoices):
    DAILY = "daily", "Daily"
    WEEKLY = "weekly", "Weekly"
    MONTHLY = "monthly", "Monthly"


class ParentalControl(TimeStampedUUIDModel):
    family_member = models.OneToOneField(
        FamilyMember,
        on_delete=models.CASCADE,
        related_name="parental_control",
    )
    content_filter_level = models.CharField(
        max_length=10,
        choices=ContentFilterLevel.choices,
        default=ContentFilterLevel.CHILD,
    )
    allowed_contact_ids = models.JSONField(default=list)
    screen_time_minutes_per_day = models.IntegerField(null=True, blank=True)
    time_restrictions = models.JSONField(default=dict)
    restricted_sections = models.JSONField(default=list)
    location_sharing = models.BooleanField(default=False)
    sos_enabled = models.BooleanField(default=True)
    activity_report_frequency = models.CharField(
        max_length=10,
        choices=ActivityReportFrequency.choices,
        default=ActivityReportFrequency.WEEKLY,
    )

    class Meta:
        db_table = "family_parental_control"

    def __str__(self):
        return f"ParentalControl for {self.family_member}"


# ---------------------------------------------------------------------------
# FamilyEvent
# ---------------------------------------------------------------------------

class EventType(models.TextChoices):
    BIRTHDAY = "birthday", "Birthday"
    ANNIVERSARY = "anniversary", "Anniversary"
    SCHOOL = "school", "School"
    CHURCH = "church", "Church"
    APPOINTMENT = "appointment", "Appointment"
    OTHER = "other", "Other"


class FamilyEvent(TimeStampedUUIDModel):
    family = models.ForeignKey(
        FamilyAccount,
        on_delete=models.CASCADE,
        related_name="events",
    )
    title = models.CharField(max_length=255)
    type = models.CharField(max_length=20, choices=EventType.choices)
    event_date = models.DateTimeField()
    reminder_minutes_before = models.IntegerField(default=60)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_family_events",
    )

    class Meta:
        db_table = "family_event"
        ordering = ["event_date"]

    def __str__(self):
        return f"{self.title} ({self.type})"


# ---------------------------------------------------------------------------
# FamilyPhotoAlbum / FamilyPhoto
# ---------------------------------------------------------------------------

class FamilyPhotoAlbum(TimeStampedUUIDModel):
    family = models.ForeignKey(
        FamilyAccount,
        on_delete=models.CASCADE,
        related_name="photo_albums",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    cover_url = models.URLField(blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_family_albums",
    )

    class Meta:
        db_table = "family_photo_album"

    def __str__(self):
        return self.name


class FamilyPhoto(TimeStampedUUIDModel):
    album = models.ForeignKey(
        FamilyPhotoAlbum,
        on_delete=models.CASCADE,
        related_name="photos",
    )
    uploader = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_family_photos",
    )
    file_url = models.URLField()
    caption = models.TextField(blank=True)
    taken_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "family_photo"

    def __str__(self):
        return f"Photo in {self.album}"


# ---------------------------------------------------------------------------
# MemorialPage
# ---------------------------------------------------------------------------

class MemorialPage(TimeStampedUUIDModel):
    family = models.ForeignKey(
        FamilyAccount,
        on_delete=models.CASCADE,
        related_name="memorial_pages",
    )
    name = models.CharField(max_length=255)
    birth_date = models.DateField(null=True, blank=True)
    death_date = models.DateField(null=True, blank=True)
    tribute = models.TextField(blank=True)
    photo_url = models.URLField(blank=True)
    is_public = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_memorial_pages",
    )

    class Meta:
        db_table = "family_memorial_page"

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# MilestoneRecord
# ---------------------------------------------------------------------------

class MilestoneType(models.TextChoices):
    FIRST_STEPS = "first_steps", "First Steps"
    GRADUATION = "graduation", "Graduation"
    BAPTISM = "baptism", "Baptism"
    MARRIAGE = "marriage", "Marriage"
    BIRTH = "birth", "Birth"
    ACHIEVEMENT = "achievement", "Achievement"
    OTHER = "other", "Other"


class MilestoneRecord(TimeStampedUUIDModel):
    family_member = models.ForeignKey(
        FamilyMember,
        on_delete=models.CASCADE,
        related_name="milestones",
    )
    title = models.CharField(max_length=255)
    type = models.CharField(max_length=20, choices=MilestoneType.choices)
    milestone_date = models.DateField()
    description = models.TextField(blank=True)
    photo_url = models.URLField(blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_milestones",
    )

    class Meta:
        db_table = "family_milestone_record"
        ordering = ["-milestone_date"]

    def __str__(self):
        return f"{self.title} — {self.family_member}"


# ---------------------------------------------------------------------------
# TimeCapsule
# ---------------------------------------------------------------------------

class TimeCapsule(TimeStampedUUIDModel):
    family = models.ForeignKey(
        FamilyAccount,
        on_delete=models.CASCADE,
        related_name="time_capsules",
    )
    title = models.CharField(max_length=255)
    message = models.TextField()
    unlock_date = models.DateField()
    is_locked = models.BooleanField(default=True)
    creator = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_time_capsules",
    )
    media_urls = models.JSONField(default=list)

    class Meta:
        db_table = "family_time_capsule"

    def __str__(self):
        return self.title


# ---------------------------------------------------------------------------
# FamilyPrayerItem
# ---------------------------------------------------------------------------

class FamilyPrayerItem(TimeStampedUUIDModel):
    family = models.ForeignKey(
        FamilyAccount,
        on_delete=models.CASCADE,
        related_name="prayer_items",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="family_prayer_items",
    )
    text = models.TextField()
    is_answered = models.BooleanField(default=False)
    answered_note = models.TextField(blank=True)

    class Meta:
        db_table = "family_prayer_item"

    def __str__(self):
        return self.text[:60]


# ---------------------------------------------------------------------------
# FamilyNoticeBoard
# ---------------------------------------------------------------------------

class FamilyNoticeBoard(TimeStampedUUIDModel):
    family = models.ForeignKey(
        FamilyAccount,
        on_delete=models.CASCADE,
        related_name="notices",
    )
    posted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="posted_family_notices",
    )
    title = models.CharField(max_length=255)
    content = models.TextField()
    is_pinned = models.BooleanField(default=False)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "family_notice_board"
        ordering = ["-is_pinned", "-created_at"]

    def __str__(self):
        return self.title


# ---------------------------------------------------------------------------
# GriefSupportGroup / GriefGroupMembership
# ---------------------------------------------------------------------------

class GriefGroupType(models.TextChoices):
    SPOUSE = "spouse", "Spouse"
    CHILD = "child", "Child"
    PARENT = "parent", "Parent"
    SIBLING = "sibling", "Sibling"
    GENERAL = "general", "General"


class GriefSupportGroup(TimeStampedUUIDModel):
    name = models.CharField(max_length=255)
    type = models.CharField(max_length=20, choices=GriefGroupType.choices)
    facilitator = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="facilitated_grief_groups",
    )
    description = models.TextField(blank=True)
    is_anonymous_allowed = models.BooleanField(default=True)
    member_count = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "family_grief_support_group"

    def __str__(self):
        return self.name


class GriefGroupMembership(TimeStampedUUIDModel):
    group = models.ForeignKey(
        GriefSupportGroup,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="grief_group_memberships",
    )
    is_anonymous = models.BooleanField(default=False)
    join_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "family_grief_group_membership"
        unique_together = [["group", "user"]]

    def __str__(self):
        return f"{self.user} in {self.group}"
