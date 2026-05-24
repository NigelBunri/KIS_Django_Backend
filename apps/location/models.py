"""
Partner geolocation / attendance models.

Design principles:
- No silent tracking: members must explicitly check in.
- No continuous live location: only a one-time proximity proof per event.
- Precise coordinates are NEVER stored. Only rounded distance from the event
  center is kept, and only for the duration the event is active.
- Arrival numbers are assigned atomically inside a DB transaction.
"""
from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone

from apps.accounts.models import User
from apps.partners.models import Partner


class RecurrenceType(models.TextChoices):
    ONCE = "once", "Once"
    DAILY = "daily", "Daily"
    WEEKLY = "weekly", "Weekly"
    MONTHLY = "monthly", "Monthly"
    CUSTOM = "custom", "Custom days"


class TargetType(models.TextChoices):
    ALL = "all", "All members"
    ROLES = "roles", "Selected roles"
    USERS = "users", "Selected users"
    GROUP = "group", "Messaging group"
    COMMUNITY = "community", "Community"
    CHANNEL = "channel", "Channel / sub-room"


class EventStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    CLOSED = "closed", "Closed"
    CANCELLED = "cancelled", "Cancelled"


class PartnerLocationEvent(models.Model):
    """A geofenced attendance event created by a partner admin."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    partner = models.ForeignKey(
        Partner, on_delete=models.CASCADE, related_name="location_events"
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_location_events",
    )

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=16, choices=EventStatus.choices, default=EventStatus.DRAFT
    )

    # ── Timing ────────────────────────────────────────────────────────────────
    start_dt = models.DateTimeField()
    end_dt = models.DateTimeField()
    # Minutes before start_dt that check-in window opens
    checkin_opens_before_minutes = models.PositiveSmallIntegerField(default=15)
    # Minutes after start_dt that a check-in is marked late (0 = never late)
    late_after_minutes = models.PositiveSmallIntegerField(default=0)

    # ── Recurrence ────────────────────────────────────────────────────────────
    recurrence = models.CharField(
        max_length=16, choices=RecurrenceType.choices, default=RecurrenceType.ONCE
    )
    # [0..6] Mon-Sun for CUSTOM recurrence
    recurrence_days = models.JSONField(default=list, blank=True)
    recurrence_until = models.DateField(null=True, blank=True)

    # ── Target group ──────────────────────────────────────────────────────────
    target_type = models.CharField(
        max_length=16, choices=TargetType.choices, default=TargetType.ALL
    )
    target_roles = models.JSONField(default=list, blank=True)
    target_user_ids = models.JSONField(default=list, blank=True)
    target_ref_id = models.UUIDField(null=True, blank=True)

    # ── Geofence (primary zone, stored in PartnerLocationZone too) ────────────
    center_lat = models.DecimalField(max_digits=9, decimal_places=6)
    center_lng = models.DecimalField(max_digits=9, decimal_places=6)
    radius_meters = models.PositiveIntegerField(default=100)

    # ── Visibility ────────────────────────────────────────────────────────────
    show_arrival_order_to_members = models.BooleanField(default=False)
    show_checkin_count_to_members = models.BooleanField(default=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_dt"]
        db_table = "location_event"

    def __str__(self):
        return f"{self.title} ({self.partner.slug})"

    @property
    def checkin_opens_at(self):
        from datetime import timedelta
        return self.start_dt - timedelta(minutes=self.checkin_opens_before_minutes)

    @property
    def is_checkin_open(self):
        now = timezone.now()
        return self.checkin_opens_at <= now <= self.end_dt

    @property
    def is_accepting_checkins(self):
        return self.status == EventStatus.ACTIVE and self.is_checkin_open


class PartnerLocationZone(models.Model):
    """
    Optional additional geofence zone for an event (e.g. overflow, parking).
    The primary zone is on the event itself.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(
        PartnerLocationEvent, on_delete=models.CASCADE, related_name="extra_zones"
    )
    name = models.CharField(max_length=128)
    center_lat = models.DecimalField(max_digits=9, decimal_places=6)
    center_lng = models.DecimalField(max_digits=9, decimal_places=6)
    radius_meters = models.PositiveIntegerField(default=50)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "location_zone"

    def __str__(self):
        return f"{self.name} — {self.event.title}"


class PartnerLocationAttendance(models.Model):
    """
    A single member check-in for a location event.

    Arrival number is unique per event and assigned atomically.
    Only a rounded distance is stored — never a precise coordinate.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(
        PartnerLocationEvent, on_delete=models.CASCADE, related_name="attendances"
    )
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="location_attendances"
    )
    partner = models.ForeignKey(
        Partner, on_delete=models.CASCADE, related_name="location_attendances"
    )

    checked_in_at = models.DateTimeField(default=timezone.now)
    is_late = models.BooleanField(default=False)
    arrival_number = models.PositiveIntegerField()

    # Privacy: only rounded distance (to nearest 10 m), no lat/lng
    distance_from_center_m = models.PositiveIntegerField(default=0)
    location_verified = models.BooleanField(default=True)

    # Source: "app" (self check-in) or "manual_admin"
    source = models.CharField(max_length=32, default="app")
    device_os = models.CharField(max_length=16, blank=True)  # "ios" / "android"

    is_manual = models.BooleanField(default=False)
    manually_adjusted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="manual_checkins_performed",
    )
    manually_adjusted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("event", "user")]
        ordering = ["arrival_number"]
        db_table = "location_attendance"

    def __str__(self):
        return f"#{self.arrival_number} {self.user_id} @ {self.event.title}"


class PartnerLocationConsent(models.Model):
    """
    Tracks whether a user has granted consent for partner location attendance.
    Consent can be revoked at any time; once revoked the member cannot check in
    until they re-grant consent.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="location_consents"
    )
    partner = models.ForeignKey(
        Partner, on_delete=models.CASCADE, related_name="location_consents"
    )
    granted = models.BooleanField(default=False)
    granted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    is_minor = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = [("user", "partner")]
        db_table = "location_consent"

    def __str__(self):
        state = "granted" if self.granted else "revoked"
        return f"{self.user_id} consent {state} — {self.partner.slug}"


class AuditAction(models.TextChoices):
    EVENT_CREATED = "event_created", "Event created"
    EVENT_UPDATED = "event_updated", "Event updated"
    EVENT_DELETED = "event_deleted", "Event deleted"
    ZONE_CHANGED = "zone_changed", "Zone changed"
    CHECKIN_RECORDED = "checkin_recorded", "Check-in recorded"
    CHECKIN_MANUAL = "checkin_manual", "Manual check-in"
    ATTENDANCE_ADJUSTED = "attendance_adjusted", "Attendance adjusted"
    REPORT_EXPORTED = "report_exported", "Report exported"
    CONSENT_GRANTED = "consent_granted", "Consent granted"
    CONSENT_REVOKED = "consent_revoked", "Consent revoked"


class PartnerLocationAuditLog(models.Model):
    """Immutable audit trail for all location/attendance operations."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    partner = models.ForeignKey(
        Partner, on_delete=models.CASCADE, related_name="location_audit_logs"
    )
    event = models.ForeignKey(
        PartnerLocationEvent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    actor = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name="location_audit_actions"
    )
    action = models.CharField(max_length=32, choices=AuditAction.choices)
    target_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="location_audit_targets",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        db_table = "location_audit_log"

    def __str__(self):
        return f"{self.action} by {self.actor_id} on {self.created_at:%Y-%m-%d}"
