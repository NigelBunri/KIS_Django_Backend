import uuid
from datetime import timedelta

from django.db import models
from django.utils import timezone

from apps.accounts.models import User


class StatusType(models.TextChoices):
    IMAGE = "image", "Image"
    VIDEO = "video", "Video"
    AUDIO = "audio", "Audio"
    TEXT = "text", "Text"


class StatusVisibility(models.TextChoices):
    CONTACTS = "contacts", "My contacts"
    CONTACTS_EXCEPT = "contacts_except", "My contacts except"
    ONLY_SHARE_WITH = "only_share_with", "Only share with"


class StatusReplyPermission(models.TextChoices):
    CONTACTS = "contacts", "Contacts"
    NOBODY = "nobody", "Nobody"


def status_upload_path(instance: "StatusItem", filename: str) -> str:
    return f"statuses/{instance.user_id}/{timezone.now().strftime('%Y/%m/%d')}/{filename}"


class StatusItem(models.Model):
    """
    Lightweight status item (WhatsApp-style).

    Stored on the Django media volume for now; can be swapped to S3 later.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="status_items")
    type = models.CharField(max_length=16, choices=StatusType.choices)
    text = models.TextField(blank=True)
    file = models.FileField(upload_to=status_upload_path, null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    style = models.JSONField(default=dict, blank=True)
    visibility = models.CharField(
        max_length=32,
        choices=StatusVisibility.choices,
        default=StatusVisibility.CONTACTS,
        db_index=True,
    )
    reply_permission = models.CharField(
        max_length=16,
        choices=StatusReplyPermission.choices,
        default=StatusReplyPermission.CONTACTS,
    )
    expires_at = models.DateTimeField(db_index=True)
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "expires_at"]),
            models.Index(fields=["user", "created_at"]),
        ]

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(days=2)
        super().save(*args, **kwargs)

    def is_active(self) -> bool:
        return (not self.is_deleted) and self.expires_at > timezone.now()

    def __str__(self) -> str:
        return f"Status {self.id} ({self.type})"


class StatusAudienceTarget(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.ForeignKey(
        StatusItem,
        on_delete=models.CASCADE,
        related_name="audience_targets",
    )
    target_user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="status_audience_targets",
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["status", "target_user"],
                name="status_audience_target_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "target_user"]),
            models.Index(fields=["target_user", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"StatusAudienceTarget {self.status_id} -> {self.target_user_id}"


class StatusItemView(models.Model):
    """
    Tracks who has viewed a status item.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.ForeignKey(StatusItem, on_delete=models.CASCADE, related_name="views")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="status_views")
    viewed_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["status", "user"], name="status_item_unique_view"),
        ]
        indexes = [
            models.Index(fields=["status", "user"]),
            models.Index(fields=["user", "viewed_at"]),
        ]

    def __str__(self) -> str:
        return f"StatusView {self.status_id} by {self.user_id}"


class StatusMute(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="status_mutes",
    )
    muted_user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="muted_in_statuses",
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "muted_user"],
                name="status_mute_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "muted_user"]),
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"StatusMute {self.user_id} -> {self.muted_user_id}"
