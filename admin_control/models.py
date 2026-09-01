"""Domain models supporting the admin control platform."""
from django.conf import settings
from django.db import models


class AdminAction(models.Model):
    """Immutable log of admin interactions."""

    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="admin_control_%(class)s_actions",
    )
    action = models.CharField(max_length=128)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        abstract = True


class AdminAuditEntry(AdminAction):
    """Audit trail entries for admin control operations."""

    class Severity(models.TextChoices):
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        CRITICAL = "critical", "Critical"

    action_type = models.CharField(max_length=64)
    target_app = models.CharField(max_length=128, blank=True)
    target_model = models.CharField(max_length=128, blank=True)
    target_pk = models.CharField(max_length=64, blank=True)
    severity = models.CharField(max_length=16, choices=Severity.choices, default=Severity.INFO)

    class Meta:
        ordering = ["-created_at"]


class SuspiciousActivityFlag(models.Model):
    """Tracks flagged admin behaviors requiring attention."""

    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="suspicious_flags",
    )
    reason = models.CharField(max_length=256)
    path = models.CharField(max_length=512, blank=True)
    severity = models.CharField(max_length=16, choices=AdminAuditEntry.Severity.choices, default=AdminAuditEntry.Severity.WARNING)
    metadata = models.JSONField(default=dict, blank=True)
    resolved = models.BooleanField(default=False, db_index=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

class SecurityIncident(models.Model):
    """
    ENGINEERING RECOMMENDATION, not a claim of regulatory compliance by
    itself: this is the record-keeping/workflow tool an incident-response
    process needs (log an incident, track its status, note whether
    regulatory notification is owed and when it was sent) - it doesn't by
    itself satisfy any jurisdiction's breach-notification deadline (e.g.
    NDPA/GDPR's ~72-hour windows), which is a legal/process obligation on
    the humans using this tool, not something software can discharge on
    its own. Before this, no incident-tracking of any kind existed
    anywhere in the codebase.
    """

    class Severity(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        CRITICAL = "critical", "Critical"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        INVESTIGATING = "investigating", "Investigating"
        CONTAINED = "contained", "Contained"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    severity = models.CharField(max_length=16, choices=Severity.choices, default=Severity.MEDIUM)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN, db_index=True)
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reported_security_incidents",
    )
    discovered_at = models.DateTimeField()
    # Best-effort scoping for a breach-notification assessment - who might
    # need to be told, and about what. Neither field being populated is
    # itself a legal judgment; it's the input a human uses to make one.
    affected_user_count = models.PositiveIntegerField(null=True, blank=True)
    data_categories_affected = models.JSONField(default=list, blank=True)
    regulatory_notification_required = models.BooleanField(null=True, blank=True)
    regulatory_notification_sent_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_summary = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "severity"]),
        ]


class AdminUserActivity(AdminAction):
    """Persisted stream of admin control requests."""

    path = models.CharField(max_length=512)
    method = models.CharField(max_length=10)
    status_code = models.PositiveSmallIntegerField(default=200, db_index=True)
    ip_address = models.GenericIPAddressField(default="0.0.0.0")
    device = models.CharField(max_length=512, blank=True)
    duration_ms = models.FloatField(default=0.0)
    response_size = models.PositiveIntegerField(default=0)
    is_deleted = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["-created_at"]
