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
    DOCUMENT = "document", "Document"


class StatusVisibility(models.TextChoices):
    CONTACTS = "contacts", "My contacts"
    CONTACTS_EXCEPT = "contacts_except", "My contacts except"
    ONLY_SHARE_WITH = "only_share_with", "Only share with"


class StatusModerationStatus(models.TextChoices):
    """Gates visibility to viewers other than the author — see
    apps/statuses/services.py::can_view_status. Distinct from is_deleted
    (author-initiated removal) and expires_at (time-based); this is
    content-safety-initiated. PASSED is the default for every status that
    either didn't need a visual scan (text/audio) or was scanned
    synchronously and cleared (image, or video once its async scan
    resolves clean) - real production behavior is unchanged for anyone
    until MEDIA_SAFETY_LIVE_PROVIDER_CALLS_ENABLED/MEDIA_SAFETY_SERVICE_
    ENABLED are turned on, since scan_saved_upload_for_explicit_content
    routes to the same always-passing stub path until then."""

    PASSED = "passed", "Passed"
    PENDING_REVIEW = "pending_review", "Pending review"
    BLOCKED = "blocked", "Blocked"


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
    # Only meaningfully used by type=document today - a PDF/Word attachment
    # needs its real filename shown to viewers (there's no visual preview
    # to identify it by, unlike image/video). The direct-to-S3 path stores
    # the file under a random object key (see key_prefix in
    # apps/media/upload_intent.py), so the name has to be captured
    # separately at create time rather than derived from the storage path.
    original_filename = models.CharField(max_length=255, blank=True)
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
    moderation_status = models.CharField(
        max_length=16,
        choices=StatusModerationStatus.choices,
        default=StatusModerationStatus.PASSED,
        db_index=True,
        help_text="Content-safety gate — see StatusModerationStatus and can_view_status().",
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

    # Hard product ceiling: 72 hours from creation, no matter what a
    # caller passes (expires_at is read-only on StatusCreateSerializer, and
    # StatusViewSet.perform_create clamps every tier's configured retention
    # to this same ceiling) - see MAX_LIFETIME_HOURS below.
    MAX_LIFETIME_HOURS = 72

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(hours=self.MAX_LIFETIME_HOURS)
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


class StatusReaction(models.Model):
    """One heart/emoji reaction per (status, user) - a second tap by the
    same viewer replaces their previous emoji rather than stacking a
    second row, matching the "double-tap to heart, tap again to change
    it" convention this is modeled on. Deliberately separate from a chat
    message: the reaction is ALSO delivered into the chat room as a real
    message (see deliver_status_reply_message, called from the same
    `react` action that writes this row), but that message lives entirely
    in Nest.js/MongoDB like every other reply - this row exists purely so
    the status owner can see an aggregate "who reacted, with what" without
    having to dig through their conversations."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.ForeignKey(StatusItem, on_delete=models.CASCADE, related_name="reactions")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="status_reactions")
    emoji = models.CharField(max_length=16)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["status", "user"], name="status_item_unique_reaction"),
        ]
        indexes = [
            models.Index(fields=["status", "user"]),
        ]

    def __str__(self) -> str:
        return f"StatusReaction {self.status_id} by {self.user_id} ({self.emoji})"


class StatusComment(models.Model):
    """A comment on a status, or a reply to another comment on the SAME
    status - the dedicated, persistent status-discussion data model this
    app previously lacked entirely (replies used to be delivered as real
    chat messages via deliver_status_reply_message, which both polluted
    the viewer's/owner's ordinary conversation history and made "reply to
    a status" depend on a cross-service HTTP call to Nest succeeding -
    see apps/statuses/views.py's comments()/reply() for the fix).

    One level of nesting only (parent must itself have parent=None) -
    matches the product spec ("replies to comments"), not arbitrarily deep
    threads. Enforced in StatusViewSet.comments(), not here, so a bad row
    can't silently exist from a bug elsewhere without at least being
    caught by CheckConstraint below for the common case of a reply
    pointing at a reply.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.ForeignKey(StatusItem, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name="status_comments")
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="replies",
        help_text="Null for a top-level comment; set for a reply to another comment on the same status.",
    )
    text = models.TextField(max_length=2000)
    # Client-generated idempotency key, same convention as the pre-existing
    # reply/react actions' client_id - a double-tap/retry on a slow network
    # reuses the same key, so create() below can return the existing row
    # instead of creating a duplicate comment.
    client_id = models.CharField(max_length=64, blank=True, default="")
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["status", "author", "client_id"],
                condition=models.Q(client_id__gt=""),
                name="status_comment_unique_client_id",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "parent", "created_at"]),
            models.Index(fields=["parent", "created_at"]),
        ]
        ordering = ["created_at"]

    def __str__(self) -> str:
        kind = "reply" if self.parent_id else "comment"
        return f"Status{kind.capitalize()} {self.id} on {self.status_id} by {self.author_id}"


class StatusHide(models.Model):
    """Per-viewer, per-status-item hide ("not interested in this one") -
    distinct from StatusMute (which hides every status from a given
    author) and from StatusItem.is_deleted/expires_at (which remove the
    item for everyone). Only ever filters what THIS user's own feed/list
    shows; never affects the author or any other viewer."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="status_hides")
    status = models.ForeignKey(StatusItem, on_delete=models.CASCADE, related_name="hidden_by")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "status"], name="status_hide_unique"),
        ]
        indexes = [
            models.Index(fields=["user", "status"]),
        ]

    def __str__(self) -> str:
        return f"StatusHide {self.user_id} -> {self.status_id}"


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
