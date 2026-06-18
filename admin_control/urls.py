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
    # User management
    AdminUserListView,
    AdminUserDetailView,
    AdminUserBanView,
    AdminUserUnbanView,
    AdminUserTierChangeView,
    AdminPlatformStatsView,
    # Content moderation
    AdminContentQueueView,
    AdminContentQueueSummaryView,
    AdminContentActionView,
    AdminContentTrendView,
    # Partner oversight
    AdminPartnerListView,
    AdminPartnerDetailView,
    AdminPartnerStatsView,
    AdminRevenueStatsView,
    AdminEngagementStatsView,
    AdminAnalyticsDashboardsView,
)

urlpatterns = [
    # ── Existing dashboard & analytics ────────────────────────────────────
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

    # ── User management ───────────────────────────────────────────────────
    path("users/", AdminUserListView.as_view(), name="admin-users-list"),
    path("users/platform-stats/", AdminPlatformStatsView.as_view(), name="admin-platform-stats"),
    path("users/<str:user_id>/", AdminUserDetailView.as_view(), name="admin-user-detail"),
    path("users/<str:user_id>/ban/", AdminUserBanView.as_view(), name="admin-user-ban"),
    path("users/<str:user_id>/unban/", AdminUserUnbanView.as_view(), name="admin-user-unban"),
    path("users/<str:user_id>/set-tier/", AdminUserTierChangeView.as_view(), name="admin-user-set-tier"),

    # ── Content moderation ────────────────────────────────────────────────
    path("content/queue/", AdminContentQueueView.as_view(), name="admin-content-queue"),
    path("content/summary/", AdminContentQueueSummaryView.as_view(), name="admin-content-summary"),
    path("content/trends/", AdminContentTrendView.as_view(), name="admin-content-trends"),
    path("content/flags/<str:flag_id>/action/", AdminContentActionView.as_view(), name="admin-content-action"),

    # ── Partner oversight ─────────────────────────────────────────────────
    path("partners/", AdminPartnerListView.as_view(), name="admin-partners-list"),
    path("partners/stats/", AdminPartnerStatsView.as_view(), name="admin-partner-stats"),
    path("partners/<str:partner_id>/", AdminPartnerDetailView.as_view(), name="admin-partner-detail"),

    # ── Platform analytics ────────────────────────────────────────────────
    path("analytics/revenue/", AdminRevenueStatsView.as_view(), name="admin-revenue-stats"),
    path("analytics/engagement/", AdminEngagementStatsView.as_view(), name="admin-engagement-stats"),
    path("analytics/dashboards/", AdminAnalyticsDashboardsView.as_view(), name="admin-analytics-dashboards"),
]
