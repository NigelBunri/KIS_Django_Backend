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
