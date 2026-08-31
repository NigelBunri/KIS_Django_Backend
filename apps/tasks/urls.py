# apps/tasks/urls.py
from django.urls import path

from .views import (
    PartnerAllTasksView,
    PartnerMyTasksView,
    PartnerTaskSummaryView,
    TaskActivityListView,
    TaskAssignView,
    TaskChannelListCreateView,
    TaskCommentListCreateView,
    TaskDetailView,
    TaskStatusView,
    TaskSubmitView,
    TaskUndoView,
)

app_name = "tasks"

urlpatterns = [
    path(
        "partners/<uuid:partner_id>/channels/<uuid:channel_id>/tasks/",
        TaskChannelListCreateView.as_view(),
        name="channel-task-list",
    ),
    path(
        "partners/<uuid:partner_id>/channels/<uuid:channel_id>/tasks/summary/",
        PartnerTaskSummaryView.as_view(),
        name="channel-task-summary",
    ),
    path("partners/<uuid:partner_id>/tasks/mine/", PartnerMyTasksView.as_view(), name="partner-my-tasks"),
    path("partners/<uuid:partner_id>/tasks/summary/", PartnerTaskSummaryView.as_view(), name="partner-task-summary"),
    path("partners/<uuid:partner_id>/tasks/", PartnerAllTasksView.as_view(), name="partner-all-tasks"),
    path("tasks/<uuid:task_id>/", TaskDetailView.as_view(), name="task-detail"),
    path("tasks/<uuid:task_id>/assign/", TaskAssignView.as_view(), name="task-assign"),
    path("tasks/<uuid:task_id>/submit/", TaskSubmitView.as_view(), name="task-submit"),
    path("tasks/<uuid:task_id>/status/", TaskStatusView.as_view(), name="task-status"),
    path("tasks/<uuid:task_id>/undo/", TaskUndoView.as_view(), name="task-undo"),
    path("tasks/<uuid:task_id>/comments/", TaskCommentListCreateView.as_view(), name="task-comments"),
    path("tasks/<uuid:task_id>/activity/", TaskActivityListView.as_view(), name="task-activity"),
]
