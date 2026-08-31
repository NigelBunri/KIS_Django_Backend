# apps/tasks/views.py
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.channels.models import Channel
from apps.media.models import MediaAsset
from apps.partners.models import Partner
from apps.partners.services import (
    active_partner_member_ids,
    notify_nest_of_partner_event,
    partner_user_can_access,
    partner_user_can_manage,
    partner_user_can_view_channel,
    user_has_partner_permission,
)
from apps.partners.tiers import require_partner_feature

from .models import (
    MEMBER_ALLOWED_TRANSITIONS,
    UNDOABLE_STATUSES,
    Task,
    TaskActivityLog,
    TaskAttachment,
    TaskComment,
    TaskStatus,
)
from .serializers import (
    TaskAssignSerializer,
    TaskCommentCreateSerializer,
    TaskCommentSerializer,
    TaskCreateSerializer,
    TaskDetailSerializer,
    TaskListSerializer,
    TaskStatusChangeSerializer,
    TaskSubmitSerializer,
    TaskUpdateSerializer,
)

TASK_MANAGE_CODENAME = "partner.tasks.manage"


def _get_partner_with_feature(partner_id) -> Partner:
    partner = get_object_or_404(Partner, id=partner_id)
    require_partner_feature(
        partner, "task_management",
        "This organization's current plan does not include task management.",
    )
    return partner


def _get_channel(partner: Partner, channel_id) -> Channel:
    channel = Channel.objects.filter(id=channel_id, partner=partner).first()
    if not channel:
        raise NotFound("Channel not found in this organization.")
    return channel


def _require_channel_access(channel: Channel, user) -> None:
    if not partner_user_can_view_channel(channel, user):
        raise PermissionDenied("Not allowed to view this channel.")


def _can_manage_tasks(partner: Partner, user) -> bool:
    return partner_user_can_manage(partner, user) or user_has_partner_permission(partner, user, TASK_MANAGE_CODENAME)


def _require_task_manage(partner: Partner, user) -> None:
    if not _can_manage_tasks(partner, user):
        raise PermissionDenied("Not allowed to manage tasks in this organization.")


def _get_task_for_user(task_id, user) -> Task:
    task = get_object_or_404(Task.objects.select_related("partner", "channel"), id=task_id, is_deleted=False)
    _require_channel_access(task.channel, user)
    return task


def _log_activity(task: Task, actor, event_type: str, **kwargs) -> TaskActivityLog:
    return TaskActivityLog.objects.create(task=task, actor=actor, event_type=event_type, **kwargs)


def _notify_task_event(task: Task, event: str, user_ids, data: dict) -> None:
    clean_ids = {str(uid) for uid in user_ids if uid}
    if not clean_ids:
        return
    notify_nest_of_partner_event(
        partner_id=str(task.partner_id),
        event=event,
        user_ids=list(clean_ids),
        data={"taskId": str(task.id), "channelId": str(task.channel_id), **data},
    )


class TaskChannelListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, partner_id, channel_id):
        partner = _get_partner_with_feature(partner_id)
        channel = _get_channel(partner, channel_id)
        _require_channel_access(channel, request.user)

        qs = Task.objects.filter(channel=channel, is_deleted=False).select_related("assigned_to", "created_by")

        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        assignee_filter = request.query_params.get("assigned_to")
        if assignee_filter == "me":
            qs = qs.filter(assigned_to=request.user)
        elif assignee_filter:
            qs = qs.filter(assigned_to_id=assignee_filter)

        serializer = TaskListSerializer(qs, many=True)
        return Response({"tasks": serializer.data}, status=status.HTTP_200_OK)

    def post(self, request, partner_id, channel_id):
        partner = _get_partner_with_feature(partner_id)
        channel = _get_channel(partner, channel_id)
        _require_task_manage(partner, request.user)

        serializer = TaskCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        assignee = None
        assignee_id = data.get("assigned_to_id")
        if assignee_id:
            assignee = User.objects.filter(id=assignee_id).first()
            if not assignee:
                raise ValidationError({"assigned_to_id": "User not found."})

        with transaction.atomic():
            task = Task.objects.create(
                partner=partner,
                channel=channel,
                title=data["title"],
                description=data.get("description") or "",
                created_by=request.user,
                assigned_to=assignee,
                priority=data.get("priority") or "medium",
                due_at=data.get("due_at"),
            )
            _log_activity(task, request.user, TaskActivityLog.EventType.CREATED, to_status=task.status)
            if assignee:
                _log_activity(
                    task, request.user, TaskActivityLog.EventType.ASSIGNED,
                    to_assignee=assignee,
                )

            reference_ids = data.get("reference_asset_ids") or []
            if reference_ids:
                owned_assets = MediaAsset.objects.filter(id__in=reference_ids, owner=request.user)
                for asset in owned_assets:
                    TaskAttachment.objects.create(
                        task=task, asset=asset, kind=TaskAttachment.Kind.REFERENCE, uploaded_by=request.user,
                    )

        if assignee:
            _notify_task_event(
                task, "partner.task_assigned", [assignee.id],
                {"title": task.title, "assignedBy": str(request.user.id)},
            )

        return Response(TaskDetailSerializer(task).data, status=status.HTTP_201_CREATED)


class TaskDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, task_id):
        task = _get_task_for_user(task_id, request.user)
        return Response(TaskDetailSerializer(task).data, status=status.HTTP_200_OK)

    def patch(self, request, task_id):
        task = _get_task_for_user(task_id, request.user)
        _require_task_manage(task.partner, request.user)

        serializer = TaskUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for field, value in serializer.validated_data.items():
            setattr(task, field, value)
        task.save()
        return Response(TaskDetailSerializer(task).data, status=status.HTTP_200_OK)

    def delete(self, request, task_id):
        task = _get_task_for_user(task_id, request.user)
        _require_task_manage(task.partner, request.user)

        task.is_deleted = True
        task.deleted_at = timezone.now()
        task.save(update_fields=["is_deleted", "deleted_at", "updated_at"])
        _log_activity(task, request.user, TaskActivityLog.EventType.DELETED)

        notify_ids = active_partner_member_ids(task.partner)
        _notify_task_event(task, "partner.task_deleted", notify_ids, {"title": task.title})
        return Response(status=status.HTTP_204_NO_CONTENT)


class TaskAssignView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, task_id):
        task = _get_task_for_user(task_id, request.user)
        _require_task_manage(task.partner, request.user)

        serializer = TaskAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        assignee_id = serializer.validated_data["assigned_to_id"]
        note = serializer.validated_data.get("note", "")

        new_assignee = None
        if assignee_id:
            new_assignee = User.objects.filter(id=assignee_id).first()
            if not new_assignee:
                raise ValidationError({"assigned_to_id": "User not found."})

        previous_assignee = task.assigned_to
        task.assigned_to = new_assignee
        task.save(update_fields=["assigned_to", "updated_at"])

        event_type = (
            TaskActivityLog.EventType.REASSIGNED if previous_assignee else TaskActivityLog.EventType.ASSIGNED
        )
        _log_activity(
            task, request.user, event_type,
            from_assignee=previous_assignee, to_assignee=new_assignee, note=note,
        )

        notify_ids = [uid for uid in (new_assignee.id if new_assignee else None, previous_assignee.id if previous_assignee else None) if uid]
        _notify_task_event(task, "partner.task_reassigned", notify_ids, {"title": task.title})

        return Response(TaskDetailSerializer(task).data, status=status.HTTP_200_OK)


class TaskSubmitView(APIView):
    """Assignee marks their task done and attaches a report (any file
    type). Only the current assignee may call this — not task-management
    admins, who use TaskStatusView instead to record their review."""
    permission_classes = [IsAuthenticated]

    def post(self, request, task_id):
        task = _get_task_for_user(task_id, request.user)
        if task.assigned_to_id != request.user.id:
            raise PermissionDenied("Only the assignee can submit this task.")
        if task.status not in (TaskStatus.NOT_STARTED, TaskStatus.IN_PROGRESS, TaskStatus.REDO):
            raise ValidationError({"detail": f"Cannot submit a task in status '{task.status}'."})

        serializer = TaskSubmitSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        asset_ids = serializer.validated_data.get("asset_ids") or []
        note = serializer.validated_data.get("note", "")

        with transaction.atomic():
            previous_status = task.status
            task.status = TaskStatus.SUBMITTED
            task.submitted_at = timezone.now()
            task.save(update_fields=["status", "submitted_at", "updated_at"])

            if asset_ids:
                owned_assets = MediaAsset.objects.filter(id__in=asset_ids, owner=request.user)
                for asset in owned_assets:
                    TaskAttachment.objects.create(
                        task=task, asset=asset, kind=TaskAttachment.Kind.REPORT, uploaded_by=request.user,
                    )
                for _ in owned_assets:
                    _log_activity(task, request.user, TaskActivityLog.EventType.ATTACHMENT_ADDED)

            _log_activity(
                task, request.user, TaskActivityLog.EventType.STATUS_CHANGED,
                from_status=previous_status, to_status=task.status, note=note,
            )

        admin_ids = _partner_task_admin_ids(task.partner)
        _notify_task_event(task, "partner.task_submitted", admin_ids, {"title": task.title})
        return Response(TaskDetailSerializer(task).data, status=status.HTTP_200_OK)


class TaskStatusView(APIView):
    """Admin records a review decision. Members never call this directly —
    their only self-service transitions are start-work (handled inline
    below) and submit (TaskSubmitView)."""
    permission_classes = [IsAuthenticated]

    def post(self, request, task_id):
        task = _get_task_for_user(task_id, request.user)

        serializer = TaskStatusChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_status = serializer.validated_data["status"]
        note = serializer.validated_data.get("note", "")

        if new_status == TaskStatus.IN_PROGRESS:
            # Assignee self-service "start work" — the one member-initiated
            # transition this view also accepts, since it's not a review
            # decision and doesn't belong behind the admin-only gate below.
            if task.assigned_to_id != request.user.id:
                raise PermissionDenied("Only the assignee can start work on this task.")
            if TaskStatus.IN_PROGRESS not in MEMBER_ALLOWED_TRANSITIONS.get(task.status, set()):
                raise ValidationError({"detail": f"Cannot move from '{task.status}' to 'in_progress'."})
            previous = task.status
            task.status = TaskStatus.IN_PROGRESS
            task.started_at = task.started_at or timezone.now()
            task.save(update_fields=["status", "started_at", "updated_at"])
            _log_activity(
                task, request.user, TaskActivityLog.EventType.STATUS_CHANGED,
                from_status=previous, to_status=task.status, note=note,
            )
            return Response(TaskDetailSerializer(task).data, status=status.HTTP_200_OK)

        _require_task_manage(task.partner, request.user)
        previous = task.status
        task.status = new_status
        task.review_note = note
        now = timezone.now()
        if new_status in (TaskStatus.UNDER_REVIEW, TaskStatus.REVIEWED_PENDING):
            task.reviewed_at = now
        elif new_status == TaskStatus.COMPLETED:
            task.reviewed_at = task.reviewed_at or now
            task.completed_at = now
        elif new_status == TaskStatus.REDO:
            task.completed_at = None
        task.save()

        _log_activity(
            task, request.user, TaskActivityLog.EventType.STATUS_CHANGED,
            from_status=previous, to_status=new_status, note=note,
        )

        notify_ids = [task.assigned_to_id] if task.assigned_to_id else []
        _notify_task_event(
            task, "partner.task_status_changed", notify_ids,
            {"title": task.title, "status": new_status},
        )
        return Response(TaskDetailSerializer(task).data, status=status.HTTP_200_OK)


class TaskUndoView(APIView):
    """Reverts a task to whatever status it held immediately before its
    most recent status-change event — a history operation, not a status
    of its own. Admin-only, matching the review actions it's undoing."""
    permission_classes = [IsAuthenticated]

    def post(self, request, task_id):
        task = _get_task_for_user(task_id, request.user)
        _require_task_manage(task.partner, request.user)

        if task.status not in UNDOABLE_STATUSES:
            raise ValidationError({"detail": f"Nothing to undo from status '{task.status}'."})

        last_change = (
            TaskActivityLog.objects.filter(task=task, event_type=TaskActivityLog.EventType.STATUS_CHANGED)
            .order_by("-created_at")
            .first()
        )
        if not last_change or not last_change.from_status:
            raise ValidationError({"detail": "No prior status recorded to undo to."})

        previous = task.status
        task.status = last_change.from_status
        if task.status != TaskStatus.COMPLETED:
            task.completed_at = None
        task.save()

        _log_activity(
            task, request.user, TaskActivityLog.EventType.UNDO,
            from_status=previous, to_status=task.status,
        )

        notify_ids = [task.assigned_to_id] if task.assigned_to_id else []
        _notify_task_event(task, "partner.task_status_changed", notify_ids, {"title": task.title, "status": task.status})
        return Response(TaskDetailSerializer(task).data, status=status.HTTP_200_OK)


class TaskCommentListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, task_id):
        task = _get_task_for_user(task_id, request.user)
        comments = task.comments.filter(is_deleted=False).select_related("author")
        return Response({"comments": TaskCommentSerializer(comments, many=True).data}, status=status.HTTP_200_OK)

    def post(self, request, task_id):
        task = _get_task_for_user(task_id, request.user)
        serializer = TaskCommentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        comment = TaskComment.objects.create(task=task, author=request.user, body=serializer.validated_data["body"])
        _log_activity(task, request.user, TaskActivityLog.EventType.COMMENTED)

        notify_ids = {task.assigned_to_id, task.created_by_id} - {request.user.id, None}
        _notify_task_event(task, "partner.task_commented", notify_ids, {"title": task.title})
        return Response(TaskCommentSerializer(comment).data, status=status.HTTP_201_CREATED)


class TaskActivityListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, task_id):
        task = _get_task_for_user(task_id, request.user)
        from .serializers import TaskActivityLogSerializer
        entries = task.activity.select_related("actor", "from_assignee", "to_assignee")
        return Response({"activity": TaskActivityLogSerializer(entries, many=True).data}, status=status.HTTP_200_OK)


class PartnerMyTasksView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, partner_id):
        partner = _get_partner_with_feature(partner_id)
        if not partner_user_can_access(partner, request.user):
            raise PermissionDenied("Not allowed to view this organization's tasks.")
        qs = (
            Task.objects.filter(partner=partner, assigned_to=request.user, is_deleted=False)
            .select_related("assigned_to", "created_by", "channel")
        )
        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        return Response({"tasks": TaskListSerializer(qs, many=True).data}, status=status.HTTP_200_OK)


class PartnerAllTasksView(APIView):
    """GET /api/v1/partners/<partner_id>/tasks/ — the "Task Boards" admin
    overview: every task across every channel in the organization, not
    scoped to one channel or to "assigned to me" like the other two list
    views. Admin-only (same gate as creating/managing tasks) since a
    regular member has no reason to see tasks outside channels they're in,
    and this deliberately bypasses per-channel membership filtering to do
    that — TaskChannelListCreateView is what respects channel membership."""
    permission_classes = [IsAuthenticated]

    def get(self, request, partner_id):
        partner = _get_partner_with_feature(partner_id)
        _require_task_manage(partner, request.user)

        qs = (
            Task.objects.filter(partner=partner, is_deleted=False)
            .select_related("assigned_to", "created_by", "channel")
        )
        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        channel_filter = request.query_params.get("channel_id")
        if channel_filter:
            qs = qs.filter(channel_id=channel_filter)
        assignee_filter = request.query_params.get("assigned_to")
        if assignee_filter == "unassigned":
            qs = qs.filter(assigned_to__isnull=True)
        elif assignee_filter:
            qs = qs.filter(assigned_to_id=assignee_filter)

        qs = qs.order_by("-created_at")
        return Response({"tasks": TaskListSerializer(qs, many=True).data}, status=status.HTTP_200_OK)


class PartnerTaskSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, partner_id, channel_id=None):
        from django.db.models import Count

        partner = _get_partner_with_feature(partner_id)
        qs = Task.objects.filter(partner=partner, is_deleted=False)
        if channel_id:
            channel = _get_channel(partner, channel_id)
            _require_channel_access(channel, request.user)
            qs = qs.filter(channel=channel)
        elif not partner_user_can_access(partner, request.user):
            raise PermissionDenied("Not allowed to view this organization's tasks.")

        counts = {choice.value: 0 for choice in TaskStatus}
        for row in qs.values("status").annotate(n=Count("id")):
            counts[row["status"]] = row["n"]

        return Response({"counts": counts, "total": sum(counts.values())}, status=status.HTTP_200_OK)


def _partner_task_admin_ids(partner: Partner):
    from apps.partners.models import PartnerMembership, PartnerMembershipStatus, PartnerRoleAssignment

    ids = set()
    ids.add(partner.owner_id)
    for uid in PartnerMembership.objects.filter(
        partner=partner, status=PartnerMembershipStatus.MEMBER, role__in=("admin", "manager"),
    ).values_list("user_id", flat=True):
        ids.add(uid)
    for assignment in PartnerRoleAssignment.objects.filter(partner=partner).select_related("role"):
        if assignment.role and TASK_MANAGE_CODENAME in (assignment.role.permissions or []):
            ids.add(assignment.user_id)
    return [uid for uid in ids if uid]
