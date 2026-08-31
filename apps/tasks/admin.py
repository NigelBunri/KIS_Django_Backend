# apps/tasks/admin.py
from django.contrib import admin

from .models import Task, TaskActivityLog, TaskAttachment, TaskComment


class TaskAttachmentInline(admin.TabularInline):
    model = TaskAttachment
    extra = 0
    raw_id_fields = ("asset", "uploaded_by")


class TaskCommentInline(admin.TabularInline):
    model = TaskComment
    extra = 0
    raw_id_fields = ("author",)


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = (
        "title", "partner", "channel", "status", "priority",
        "assigned_to", "created_by", "due_at", "is_deleted", "created_at",
    )
    list_filter = ("status", "priority", "is_deleted", "partner")
    search_fields = ("title", "description", "partner__name", "channel__name")
    raw_id_fields = ("partner", "channel", "created_by", "assigned_to")
    inlines = [TaskAttachmentInline, TaskCommentInline]


@admin.register(TaskActivityLog)
class TaskActivityLogAdmin(admin.ModelAdmin):
    list_display = ("task", "event_type", "actor", "from_status", "to_status", "created_at")
    list_filter = ("event_type",)
    raw_id_fields = ("task", "actor", "from_assignee", "to_assignee")
