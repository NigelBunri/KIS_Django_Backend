# apps/tasks/media_hooks.py
"""
Registers apps.tasks' domain rules onto the apps.media purpose registry —
same shape as apps.statuses.media_hooks. Called once from
apps/tasks/apps.py's AppConfig.ready().

task_report has no attach_handler (see apps/media/purposes.py:
allow_attach=False) — TaskSubmitView binds the confirmed asset directly by
creating a TaskAttachment row, not through the generic attach endpoint.
Only access_authorizer is registered here, reusing the exact same channel-
visibility check TaskDetailView/TaskCommentListCreateView already run
(partner_user_can_view_channel), not a second implementation of it.
"""
from __future__ import annotations

from apps.media.services.access import AccessDecision
from apps.partners.services import partner_user_can_view_channel


def can_view_task_report_media(user, asset) -> AccessDecision:
    if user is None or not getattr(user, "is_authenticated", False):
        return AccessDecision.deny("authentication_required")

    if not asset.target_id:
        # Uploaded but not yet submitted to any task — only the owner can
        # reach this via can_user_access_media's owner shortcut.
        return AccessDecision.deny("not_found")

    from .models import Task

    task = Task.objects.filter(id=asset.target_id, is_deleted=False).select_related("channel").first()
    if not task:
        return AccessDecision.deny("not_found")

    if partner_user_can_view_channel(task.channel, user):
        return AccessDecision.allow()
    return AccessDecision.deny("not_authorized")


def register() -> None:
    from apps.media import purposes

    purposes.register_access_authorizer("task_report", can_view_task_report_media)
