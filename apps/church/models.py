import uuid
from django.db import models
from django.db.models import JSONField
from django.utils import timezone
from apps.accounts.models import User


class TimeStampedUUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# ─── GIVING ───────────────────────────────────────────────────────────────────

class OfferingCampaign(TimeStampedUUIDModel):
    CAMPAIGN_TYPE_CHOICES = [
        ("building", "Building"),
        ("missions", "Missions"),
        ("benevolence", "Benevolence"),
        ("special", "Special"),
        ("general", "General"),
    ]

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    target_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="USD")
    raised_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    type = models.CharField(max_length=20, choices=CAMPAIGN_TYPE_CHOICES)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="offering_campaigns"
    )
    banner_url = models.URLField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class ChurchGiving(TimeStampedUUIDModel):
    GIVING_TYPE_CHOICES = [
        ("tithe", "Tithe"),
        ("offering", "Offering"),
        ("pledge", "Pledge"),
        ("special", "Special"),
        ("firstfruits", "First Fruits"),
        ("matching", "Matching"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="church_givings")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    type = models.CharField(max_length=20, choices=GIVING_TYPE_CHOICES)
    campaign = models.ForeignKey(
        OfferingCampaign, null=True, blank=True, on_delete=models.SET_NULL, related_name="givings"
    )
    is_anonymous = models.BooleanField(default=False)
    payment_ref = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    metadata = JSONField(default=dict)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} — {self.type} {self.amount} {self.currency}"


class TithePledge(TimeStampedUUIDModel):
    PERIOD_CHOICES = [
        ("monthly", "Monthly"),
        ("annual", "Annual"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="tithe_pledges")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    period = models.CharField(max_length=10, choices=PERIOD_CHOICES)
    fulfilled_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} pledge {self.amount} {self.currency} ({self.period})"


# ─── MEMBERSHIP ───────────────────────────────────────────────────────────────

class ChurchMembership(TimeStampedUUIDModel):
    TIER_CHOICES = [
        ("visitor", "Visitor"),
        ("member", "Member"),
        ("deacon", "Deacon"),
        ("elder", "Elder"),
        ("staff", "Staff"),
    ]
    STATUS_CHOICES = [
        ("active", "Active"),
        ("inactive", "Inactive"),
        ("transferred", "Transferred"),
        ("deceased", "Deceased"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="church_memberships")
    church_id = models.UUIDField()
    tier = models.CharField(max_length=20, choices=TIER_CHOICES)
    join_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    baptism_date = models.DateField(null=True, blank=True)
    confirmation_date = models.DateField(null=True, blank=True)
    transfer_from = models.CharField(max_length=255, blank=True)
    transfer_to = models.CharField(max_length=255, blank=True)
    pastoral_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} — {self.tier} at {self.church_id}"


class AttendanceRecord(TimeStampedUUIDModel):
    ATTENDANCE_TYPE_CHOICES = [
        ("physical", "Physical"),
        ("digital", "Digital"),
    ]
    CHECK_IN_METHOD_CHOICES = [
        ("qr", "QR Code"),
        ("manual", "Manual"),
        ("app", "App"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="attendance_records")
    church_id = models.UUIDField()
    date = models.DateField()
    type = models.CharField(max_length=10, choices=ATTENDANCE_TYPE_CHOICES)
    check_in_method = models.CharField(max_length=10, choices=CHECK_IN_METHOD_CHOICES)
    service_type = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return f"{self.user} — {self.date} ({self.type})"


class ChurchLifeEvent(TimeStampedUUIDModel):
    LIFE_EVENT_TYPE_CHOICES = [
        ("baptism", "Baptism"),
        ("confirmation", "Confirmation"),
        ("wedding", "Wedding"),
        ("funeral", "Funeral"),
        ("dedication", "Dedication"),
        ("salvation", "Salvation"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="church_life_events")
    church_id = models.UUIDField()
    type = models.CharField(max_length=20, choices=LIFE_EVENT_TYPE_CHOICES)
    event_date = models.DateField()
    officiant = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    witnesses = JSONField(default=list)
    certificate_url = models.URLField(blank=True)

    class Meta:
        ordering = ["-event_date"]

    def __str__(self):
        return f"{self.user} — {self.type} on {self.event_date}"


class MemberBirthdayAnniversary(TimeStampedUUIDModel):
    TYPE_CHOICES = [
        ("birthday", "Birthday"),
        ("anniversary", "Anniversary"),
        ("wedding_anniversary", "Wedding Anniversary"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="birthday_anniversaries")
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    date = models.DateField()
    notify_church = models.BooleanField(default=True)

    class Meta:
        ordering = ["date"]

    def __str__(self):
        return f"{self.user} — {self.type} on {self.date}"


# ─── SMALL GROUPS ─────────────────────────────────────────────────────────────

class SmallGroup(TimeStampedUUIDModel):
    GROUP_TYPE_CHOICES = [
        ("cell", "Cell"),
        ("youth", "Youth"),
        ("women", "Women"),
        ("men", "Men"),
        ("couples", "Couples"),
        ("seniors", "Seniors"),
        ("mixed", "Mixed"),
    ]

    name = models.CharField(max_length=255)
    church_id = models.UUIDField()
    leader = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="led_groups"
    )
    type = models.CharField(max_length=10, choices=GROUP_TYPE_CHOICES)
    district = models.CharField(max_length=100, blank=True)
    zone = models.CharField(max_length=100, blank=True)
    region = models.CharField(max_length=100, blank=True)
    meeting_schedule = JSONField(default=dict)
    description = models.TextField(blank=True)
    max_members = models.IntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class SmallGroupMembership(TimeStampedUUIDModel):
    ROLE_CHOICES = [
        ("leader", "Leader"),
        ("co_leader", "Co-Leader"),
        ("member", "Member"),
    ]

    group = models.ForeignKey(SmallGroup, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="small_group_memberships")
    role = models.CharField(max_length=15, choices=ROLE_CHOICES)
    join_date = models.DateField(auto_now_add=True)

    class Meta:
        unique_together = [["group", "user"]]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} in {self.group} ({self.role})"


class SmallGroupAttendance(TimeStampedUUIDModel):
    group = models.ForeignKey(SmallGroup, on_delete=models.CASCADE, related_name="attendances")
    date = models.DateField()
    present_members = JSONField(default=list)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return f"{self.group} attendance on {self.date}"


# ─── DISCIPLESHIP ─────────────────────────────────────────────────────────────

class DiscipleshipJourney(TimeStampedUUIDModel):
    STAGE_CHOICES = [
        ("seeker", "Seeker"),
        ("salvation", "Salvation"),
        ("baptism", "Baptism"),
        ("basic", "Basic"),
        ("intermediate", "Intermediate"),
        ("leadership", "Leadership"),
        ("mentor", "Mentor"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="discipleship_journeys")
    stage = models.CharField(max_length=20, choices=STAGE_CHOICES)
    salvation_date = models.DateField(null=True, blank=True)
    baptism_date = models.DateField(null=True, blank=True)
    milestones = JSONField(default=list)
    mentor = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mentor_disciples",
    )
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} — {self.stage}"


class SpiritualGiftsAssessment(TimeStampedUUIDModel):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="spiritual_gifts_assessments"
    )
    completed_at = models.DateTimeField(auto_now=True)
    results = JSONField(default=dict)
    dominant_gifts = JSONField(default=list)

    class Meta:
        ordering = ["-completed_at"]

    def __str__(self):
        return f"{self.user} gifts assessment"


class AccountabilityPartner(TimeStampedUUIDModel):
    user1 = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="accountability_initiated",
    )
    user2 = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="accountability_received",
    )
    focus_area = models.CharField(max_length=255, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.user1} <-> {self.user2}"


# ─── PRAYER ───────────────────────────────────────────────────────────────────

class PrayerRequest(TimeStampedUUIDModel):
    PRIVACY_CHOICES = [
        ("public", "Public"),
        ("anonymous", "Anonymous"),
        ("leaders_only", "Leaders Only"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="church_prayer_requests")
    text = models.TextField()
    privacy = models.CharField(max_length=15, choices=PRIVACY_CHOICES, default="public")
    is_answered = models.BooleanField(default=False)
    testimony = models.TextField(blank=True)
    category = models.CharField(max_length=100, blank=True)
    tags = JSONField(default=list)
    prayer_count = models.IntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Prayer request by {self.user}"


class PrayerWallEntry(TimeStampedUUIDModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="prayer_wall_entries")
    text = models.TextField()
    is_public = models.BooleanField(default=True)
    prayer_count = models.IntegerField(default=0)
    church_id = models.UUIDField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Prayer wall entry by {self.user}"


class FastingRecord(TimeStampedUUIDModel):
    FASTING_TYPE_CHOICES = [
        ("partial", "Partial"),
        ("full", "Full"),
        ("liquid", "Liquid"),
        ("sunrise_to_sunset", "Sunrise to Sunset"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="fasting_records")
    type = models.CharField(max_length=20, choices=FASTING_TYPE_CHOICES)
    start_date = models.DateField()
    end_date = models.DateField()
    goal = models.TextField(blank=True)
    is_community = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-start_date"]

    def __str__(self):
        return f"{self.user} fasting {self.start_date} to {self.end_date}"


class PrayerChainSlot(TimeStampedUUIDModel):
    church_id = models.UUIDField()
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="prayer_chain_slots")
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    focus = models.CharField(max_length=255, blank=True)
    is_filled = models.BooleanField(default=True)

    class Meta:
        ordering = ["start_time"]

    def __str__(self):
        return f"{self.user} prayer slot {self.start_time}"


# ─── WORSHIP ──────────────────────────────────────────────────────────────────

class Song(TimeStampedUUIDModel):
    title = models.CharField(max_length=255)
    artist = models.CharField(max_length=255, blank=True)
    key = models.CharField(max_length=10, blank=True)
    tempo_bpm = models.IntegerField(null=True, blank=True)
    tags = JSONField(default=list)
    ccli_number = models.CharField(max_length=50, blank=True)
    lyrics = models.TextField(blank=True)
    chord_chart_url = models.URLField(blank=True)
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_songs"
    )
    church_id = models.UUIDField(null=True, blank=True)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title


class SetList(TimeStampedUUIDModel):
    title = models.CharField(max_length=255)
    church_id = models.UUIDField()
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_setlists"
    )
    service_date = models.DateField()
    songs = JSONField(default=list)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-service_date"]

    def __str__(self):
        return f"{self.title} — {self.service_date}"


# ─── MINISTRY & OUTREACH ──────────────────────────────────────────────────────

class MinistryDepartment(TimeStampedUUIDModel):
    DEPT_TYPE_CHOICES = [
        ("children", "Children"),
        ("youth", "Youth"),
        ("women", "Women"),
        ("men", "Men"),
        ("media", "Media"),
        ("worship", "Worship"),
        ("outreach", "Outreach"),
        ("prayer", "Prayer"),
        ("admin", "Admin"),
    ]

    name = models.CharField(max_length=255)
    church_id = models.UUIDField()
    type = models.CharField(max_length=15, choices=DEPT_TYPE_CHOICES)
    leader = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="led_departments"
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class VolunteerSignup(TimeStampedUUIDModel):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("confirmed", "Confirmed"),
        ("completed", "Completed"),
        ("no_show", "No Show"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="volunteer_signups")
    department = models.ForeignKey(
        MinistryDepartment,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="volunteers",
    )
    church_id = models.UUIDField()
    role = models.CharField(max_length=100)
    event_date = models.DateField()
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="pending")

    class Meta:
        ordering = ["-event_date"]

    def __str__(self):
        return f"{self.user} — {self.role} on {self.event_date}"


class OutreachCampaign(TimeStampedUUIDModel):
    title = models.CharField(max_length=255)
    church_id = models.UUIDField()
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="outreach_campaigns"
    )
    description = models.TextField(blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    target_count = models.IntegerField(null=True, blank=True)
    current_count = models.IntegerField(default=0)
    type = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["-start_date"]

    def __str__(self):
        return self.title


class EvangelismRecord(TimeStampedUUIDModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="evangelism_records")
    date = models.DateField()
    shares = models.IntegerField(default=0)
    follow_ups = models.IntegerField(default=0)
    salvations = models.IntegerField(default=0)
    notes = models.TextField(blank=True)
    church_id = models.UUIDField(null=True, blank=True)

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return f"{self.user} evangelism on {self.date}"
