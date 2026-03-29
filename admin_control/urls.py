"""Routing for the custom admin control API."""
from django.urls import path

from admin_control.views import (
    ActivityStreamView,
    AccessOverviewView,
    AdminRoleAssignmentDetailView,
    AdminRoleAssignmentView,
    AdminRoleView,
    AuditActionView,
    AuditTrailView,
    DashboardOverviewView,
    LiveMetricsView,
    MicroAnalyticsView,
    MonitoringAlertView,
    ModelDataView,
    ModelInstanceView,
    ModelRegistryView,
    PerformanceInsightsView,
    SuspiciousActivityView,
)

urlpatterns = [
    path("dashboard/overview/", DashboardOverviewView.as_view(), name="admin-control-dashboard"),
    path("registry/models/", ModelRegistryView.as_view(), name="admin-control-registry"),
    path("crud/<str:app_label>/<str:model_name>/", ModelDataView.as_view(), name="admin-control-crud"),
    path("crud/<str:app_label>/<str:model_name>/<str:pk>/", ModelInstanceView.as_view(), name="admin-control-instance"),
    path("activity/stream/", ActivityStreamView.as_view(), name="admin-control-activity"),
    path("audit/entries/", AuditTrailView.as_view(), name="admin-control-audit"),
    path("activity/flags/", SuspiciousActivityView.as_view(), name="admin-control-flags"),
    path("monitoring/alerts/", MonitoringAlertView.as_view(), name="admin-control-alerts"),
    path("monitoring/performance/", PerformanceInsightsView.as_view(), name="admin-control-performance"),
    path("access/overview/", AccessOverviewView.as_view(), name="admin-control-access"),
    path("live/metrics/", LiveMetricsView.as_view(), name="admin-control-live-metrics"),
    path("micro/apps/", MicroAnalyticsView.as_view(), name="admin-control-micro"),
    path("roles/", AdminRoleView.as_view(), name="admin-control-roles"),
    path("roles/assignments/", AdminRoleAssignmentView.as_view(), name="admin-control-role-assignments"),
    path(
        "roles/assignments/<int:pk>/",
        AdminRoleAssignmentDetailView.as_view(),
        name="admin-control-role-assignment-detail",
    ),
    path("audit/actions/", AuditActionView.as_view(), name="admin-control-audit-actions"),
]
