# apps/prayer/models.py
"""
Prayer Calendar: a permanent, global, 24-hour prayer platform built as a
coordination layer over 24 existing apps.groups.Group rooms — one per
UTC hour. This app owns none of the chat/calling infrastructure itself
(message history lives in the NestJS backend's MongoDB; calls are the
existing group-call system reached via the mobile app's useSocket()) —
it only tracks the Prayer-Calendar-specific state that doesn't belong on
a generic Group: which daily session is live, who's leading it, prayer
points submitted for it, and the audit trail of leadership changes.
"""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.groups.models import Group


class PrayerWatchGroup(models.Model):
    """The permanent mapping from one of the 24 UTC hours to its backing
    apps.groups.Group. Created exactly once by the provisioning
    management command (see management/commands/provision_prayer_watches.py)
    — never recreated, never duplicated (hour and group are both unique)."""

    hour = models.PositiveSmallIntegerField(unique=True, db_index=True)  # 0-23 UTC
    group = models.OneToOneField(Group, on_delete=models.PROTECT, related_name="prayer_watch")
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "prayer_watch_group"
        ordering = ["hour"]

    def __str__(self) -> str:
        return self.name


class PrayerSessionStatus(models.TextChoices):
    SCHEDULED = "scheduled", "Scheduled"
    ACTIVE = "active", "Active"
    COMPLETED = "completed", "Completed"
    INTERRUPTED = "interrupted", "Interrupted"


class PrayerLeaderKind(models.TextChoices):
    MEMBER = "member", "Member"
    ADMIN = "admin", "Admin"


class PrayerParticipationMode(models.TextChoices):
    TEXT = "text", "Text"
    VOICE = "voice", "Voice"
    VIDEO = "video", "Video"


class PrayerDailySession(models.Model):
    """One real row per (watch, UTC calendar date) — created lazily and
    idempotently (get_or_create keyed on the unique_together below) the
    first time anything touches that day's session, rather than by a
    cron job that could be delayed or double-fire. Stage/status are
    always derived from scheduled_start + the fixed 30/15/5/5/5 schedule
    (see services.compute_stage) — never from a leader pressing a button
    or from a scheduled task having run on time."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    watch_group = models.ForeignKey(PrayerWatchGroup, on_delete=models.CASCADE, related_name="sessions")
    session_date = models.DateField(db_index=True)  # UTC calendar date
    scheduled_start = models.DateTimeField()
    scheduled_end = models.DateTimeField()
    actual_started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=PrayerSessionStatus.choices, default=PrayerSessionStatus.SCHEDULED)
    interrupted_reason = models.CharField(max_length=255, blank=True, default="")

    # Current leadership state (the authoritative "who is leading right
    # now" read) — PrayerLeadershipEvent below is the append-only audit
    # trail of how it got here, not a second source of truth.
    leader = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="prayer_sessions_led",
    )
    leader_kind = models.CharField(max_length=8, choices=PrayerLeaderKind.choices, blank=True, default="")
    sharing_admin = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="prayer_sessions_shared",
    )
    sharing_admin_accepted_at = models.DateTimeField(null=True, blank=True)

    participation_mode = models.CharField(
        max_length=8, choices=PrayerParticipationMode.choices, default=PrayerParticipationMode.TEXT,
    )

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "prayer_daily_session"
        unique_together = [("watch_group", "session_date")]
        indexes = [
            models.Index(fields=["watch_group", "session_date"]),
            models.Index(fields=["leader", "session_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.watch_group.name} ({self.session_date})"


class PrayerLeadershipEventKind(models.TextChoices):
    CLAIMED = "claimed", "Claimed"
    RELEASED = "released", "Released"
    SHARE_REQUESTED = "share_requested", "Admin share requested"
    SHARE_ACCEPTED = "share_accepted", "Admin share accepted"
    SHARE_DECLINED = "share_declined", "Admin share declined"
    ADMIN_LED_SOLO = "admin_led_solo", "Admin led (no volunteer)"
    GO_OVERRIDE = "go_override", "GO override"


class PrayerLeadershipEvent(models.Model):
    """Append-only audit trail — 'Record leadership assignments and
    handovers in the session history.' Never edited or deleted; every
    leadership-affecting service function writes exactly one of these
    alongside its PrayerDailySession update, in the same transaction."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(PrayerDailySession, on_delete=models.CASCADE, related_name="leadership_events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="prayer_leadership_events")
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    kind = models.CharField(max_length=24, choices=PrayerLeadershipEventKind.choices)
    note = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "prayer_leadership_event"
        indexes = [models.Index(fields=["session", "created_at"])]
        ordering = ["created_at"]


class PrayerPointSubmission(models.Model):
    """One prayer point per (session, user) — editable/deletable by its
    author only before the session's Stage 2 (members' prayer points)
    begins; see services.can_edit_prayer_point. The one-per-user
    constraint plus that time window is the spam control the spec asks
    for ('basic submission limits'), on top of this app's own existing
    group moderation (GroupBan/report) for anything worse."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(PrayerDailySession, on_delete=models.CASCADE, related_name="prayer_points")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="prayer_point_submissions")
    text = models.CharField(max_length=500)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "prayer_point_submission"
        unique_together = [("session", "user")]
        ordering = ["created_at"]
