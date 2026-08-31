# apps/tasks/serializers.py
from rest_framework import serializers

from apps.accounts.models import User
from apps.media.models import MediaAsset

from .models import Task, TaskActivityLog, TaskAttachment, TaskComment, TaskPriority, TaskStatus


class TaskUserSummarySerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "display_name", "avatar_url"]

    def get_display_name(self, obj):
        return getattr(obj, "display_name", None) or getattr(obj, "username", None) or str(obj.phone or obj.email or obj.id)

    def get_avatar_url(self, obj):
        avatar = getattr(obj, "avatar_url", None) or getattr(obj, "profile_image_url", None)
        return avatar or None


class TaskAttachmentSerializer(serializers.ModelSerializer):
    uploaded_by = TaskUserSummarySerializer(read_only=True)
    file_name = serializers.SerializerMethodField()
    file_url = serializers.SerializerMethodField()
    mime_type = serializers.SerializerMethodField()
    size_bytes = serializers.SerializerMethodField()

    class Meta:
        model = TaskAttachment
        fields = ["id", "kind", "uploaded_by", "file_name", "file_url", "mime_type", "size_bytes", "created_at"]

    def get_file_name(self, obj):
        return obj.asset.original_filename if obj.asset else ""

    def get_file_url(self, obj):
        return obj.asset.canonical_url if obj.asset else None

    def get_mime_type(self, obj):
        return obj.asset.mime_type if obj.asset else ""

    def get_size_bytes(self, obj):
        return obj.asset.bytes if obj.asset else 0


class TaskCommentSerializer(serializers.ModelSerializer):
    author = TaskUserSummarySerializer(read_only=True)

    class Meta:
        model = TaskComment
        fields = ["id", "author", "body", "created_at"]


class TaskActivityLogSerializer(serializers.ModelSerializer):
    actor = TaskUserSummarySerializer(read_only=True)
    from_assignee = TaskUserSummarySerializer(read_only=True)
    to_assignee = TaskUserSummarySerializer(read_only=True)

    class Meta:
        model = TaskActivityLog
        fields = [
            "id", "event_type", "actor", "from_status", "to_status",
            "from_assignee", "to_assignee", "note", "created_at",
        ]


class TaskListSerializer(serializers.ModelSerializer):
    assigned_to = TaskUserSummarySerializer(read_only=True)
    created_by = TaskUserSummarySerializer(read_only=True)
    channel_name = serializers.CharField(source="channel.name", read_only=True)
    attachment_count = serializers.IntegerField(read_only=True, source="attachments.count")
    comment_count = serializers.IntegerField(read_only=True, source="comments.count")
    is_overdue = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            "id", "partner", "channel", "channel_name", "title", "status", "priority",
            "assigned_to", "created_by", "due_at", "created_at", "updated_at",
            "attachment_count", "comment_count", "is_overdue",
        ]

    def get_is_overdue(self, obj):
        from django.utils import timezone
        if not obj.due_at or obj.status in (TaskStatus.COMPLETED,):
            return False
        return obj.due_at < timezone.now()


class TaskDetailSerializer(TaskListSerializer):
    attachments = TaskAttachmentSerializer(many=True, read_only=True)
    comments = TaskCommentSerializer(many=True, read_only=True)
    activity = TaskActivityLogSerializer(many=True, read_only=True)

    class Meta(TaskListSerializer.Meta):
        fields = TaskListSerializer.Meta.fields + [
            "description", "review_note", "started_at", "submitted_at",
            "reviewed_at", "completed_at", "attachments", "comments", "activity",
        ]


class TaskCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    assigned_to_id = serializers.UUIDField(required=False, allow_null=True)
    priority = serializers.ChoiceField(choices=TaskPriority.choices, required=False, default=TaskPriority.MEDIUM)
    due_at = serializers.DateTimeField(required=False, allow_null=True)
    reference_asset_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_empty=True, default=list,
    )


class TaskUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    priority = serializers.ChoiceField(choices=TaskPriority.choices, required=False)
    due_at = serializers.DateTimeField(required=False, allow_null=True)


class TaskAssignSerializer(serializers.Serializer):
    assigned_to_id = serializers.UUIDField(allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class TaskSubmitSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default="")
    asset_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_empty=True, default=list,
    )

    def validate_asset_ids(self, value):
        if not value:
            return value
        request = self.context["request"]
        owned = set(
            str(pk) for pk in MediaAsset.objects.filter(id__in=value, owner=request.user).values_list("id", flat=True)
        )
        missing = [str(v) for v in value if str(v) not in owned]
        if missing:
            raise serializers.ValidationError(f"Unknown or unowned attachment id(s): {', '.join(missing)}")
        return value


STATUS_TRANSITION_CHOICES = (
    # in_progress is the one member-initiated ("start work") transition
    # TaskStatusView also accepts — see its own branching for why it's
    # not gated by the admin-only _require_task_manage check below.
    (TaskStatus.IN_PROGRESS, TaskStatus.IN_PROGRESS.label),
    (TaskStatus.UNDER_REVIEW, TaskStatus.UNDER_REVIEW.label),
    (TaskStatus.REVIEWED_PENDING, TaskStatus.REVIEWED_PENDING.label),
    (TaskStatus.COMPLETED, TaskStatus.COMPLETED.label),
    (TaskStatus.NOT_COMPLETED, TaskStatus.NOT_COMPLETED.label),
    (TaskStatus.REDO, TaskStatus.REDO.label),
)


class TaskStatusChangeSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=STATUS_TRANSITION_CHOICES)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class TaskCommentCreateSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=4000)
