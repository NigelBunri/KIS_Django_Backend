# apps/tasks/models.py
"""
Task/collaboration board scoped to a Partner-owned Channel (a structured,
admin-governed org channel — see apps.channels.models.Channel — not the
more informal apps.groups.models.Group). Gated behind the "task_management"
Partner-tier feature (apps.accounts.tier_presets) so it's only usable inside
Partner and Partner Pro organizations, matching how every other Partner-only
surface in this codebase is gated (apps.partners.tiers.require_partner_feature).

Status model, in the order a task normally moves through:
    NOT_STARTED -> IN_PROGRESS -> SUBMITTED -> UNDER_REVIEW
        -> REVIEWED_PENDING -> COMPLETED
                             -> NOT_COMPLETED
                             -> REDO -> (back to IN_PROGRESS by the assignee)

REDO and NOT_COMPLETED are not dead ends — REDO explicitly means "assignee,
do this again"; NOT_COMPLETED just records the reviewer's verdict without
implying the task is closed for good. "Undo" (see TaskActivityLog and
TaskViewSet.undo) is not a status of its own — it's an action that reverts
a task to whatever status it held immediately before the most recent status
change, using TaskActivityLog as the source of truth for "immediately
before". This matches how undo works in every mainstream collaboration
tool (Asana, Trello, ClickUp): it's a history operation, not a state.
"""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import BaseEntity

USER = settings.AUTH_USER_MODEL


class TaskStatus(models.TextChoices):
    NOT_STARTED = "not_started", "Not started"
    IN_PROGRESS = "in_progress", "In progress"
    SUBMITTED = "submitted", "Submitted"
    UNDER_REVIEW = "under_review", "Under review"
    REVIEWED_PENDING = "reviewed_pending", "Reviewed (pending)"
    COMPLETED = "completed", "Completed"
    NOT_COMPLETED = "not_completed", "Not completed"
    REDO = "redo", "Redo"


# Transitions a member (the assignee, without admin task-management
# permission) may perform themselves.
MEMBER_ALLOWED_TRANSITIONS = {
    TaskStatus.NOT_STARTED: {TaskStatus.IN_PROGRESS},
    TaskStatus.IN_PROGRESS: set(),  # member submits via the dedicated submit action instead
    TaskStatus.REDO: {TaskStatus.IN_PROGRESS},
}

# Terminal-ish states from which "undo" (revert to the prior status) makes
# sense. Kept as an explicit allowlist rather than "anything with history"
# so a task can't be undone back past its own creation.
UNDOABLE_STATUSES = {
    TaskStatus.SUBMITTED,
    TaskStatus.UNDER_REVIEW,
    TaskStatus.REVIEWED_PENDING,
    TaskStatus.COMPLETED,
    TaskStatus.NOT_COMPLETED,
    TaskStatus.REDO,
}


class TaskPriority(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"
    URGENT = "urgent", "Urgent"


class Task(BaseEntity):
    partner = models.ForeignKey(
        "partners.Partner", on_delete=models.CASCADE, related_name="tasks",
    )
    channel = models.ForeignKey(
        "channels.Channel", on_delete=models.CASCADE, related_name="tasks",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")

    created_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, related_name="tasks_created")
    assigned_to = models.ForeignKey(
        USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks_assigned",
    )

    status = models.CharField(max_length=24, choices=TaskStatus.choices, default=TaskStatus.NOT_STARTED, db_index=True)
    priority = models.CharField(max_length=12, choices=TaskPriority.choices, default=TaskPriority.MEDIUM)

    due_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Reviewer's free-text note left with the most recent status decision
    # (why it was marked not_completed / sent back for redo, etc.) —
    # surfaced to the assignee alongside the status change.
    review_note = models.TextField(blank=True, default="")

    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["partner", "channel", "status"]),
            models.Index(fields=["assigned_to", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.status})"


class TaskAttachment(BaseEntity):
    class Kind(models.TextChoices):
        REFERENCE = "reference", "Reference (added when creating/updating the task)"
        REPORT = "report", "Report (submitted by the assignee)"

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="attachments")
    asset = models.ForeignKey(
        "media.MediaAsset", on_delete=models.SET_NULL, null=True, related_name="task_attachments",
    )
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.REPORT)
    uploaded_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, related_name="task_attachments")

    class Meta:
        ordering = ["created_at"]


class TaskComment(BaseEntity):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, related_name="task_comments")
    body = models.TextField()
    is_deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at"]


class TaskActivityLog(models.Model):
    """Append-only activity feed for a task — every status change,
    (re)assignment, comment, and attachment. `from_status`/`to_status` are
    only populated for status-change events; TaskViewSet.undo reads the
    most recent status-change row to find what to revert to."""

    class EventType(models.TextChoices):
        CREATED = "created", "Created"
        ASSIGNED = "assigned", "Assigned"
        REASSIGNED = "reassigned", "Reassigned"
        STATUS_CHANGED = "status_changed", "Status changed"
        UNDO = "undo", "Undo"
        COMMENTED = "commented", "Commented"
        ATTACHMENT_ADDED = "attachment_added", "Attachment added"
        DELETED = "deleted", "Deleted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="activity")
    actor = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, related_name="task_activity_events")
    event_type = models.CharField(max_length=24, choices=EventType.choices)
    from_status = models.CharField(max_length=24, blank=True, default="")
    to_status = models.CharField(max_length=24, blank=True, default="")
    from_assignee = models.ForeignKey(
        USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    to_assignee = models.ForeignKey(
        USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["task", "created_at"])]
