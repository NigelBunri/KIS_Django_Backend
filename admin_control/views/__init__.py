"""Views package for admin_control."""

from .activity import ActivityStreamView
from .audit import AuditActionView, AuditTrailView, SuspiciousActivityView
from .crud import ModelRegistryView, ModelDataView
from .dashboard import DashboardOverviewView
from .instance import ModelInstanceView
from .live_metrics import LiveMetricsView
from .micro import MicroAnalyticsView
from .monitoring import MonitoringAlertView
from .performance import PerformanceInsightsView
from .roles import (
    AccessOverviewView,
    AdminRoleAssignmentDetailView,
    AdminRoleAssignmentView,
    AdminRoleView,
)
from .user_management import (
    AdminUserListView,
    AdminUserDetailView,
    AdminUserBanView,
    AdminUserUnbanView,
    AdminUserTierChangeView,
    AdminPlatformStatsView,
)
from .content_moderation import (
    AdminContentQueueView,
    AdminContentQueueSummaryView,
    AdminContentActionView,
    AdminContentTrendView,
)
from .partner_oversight import (
    AdminPartnerListView,
    AdminPartnerDetailView,
    AdminPartnerStatsView,
    AdminRevenueStatsView,
    AdminEngagementStatsView,
    AdminAnalyticsDashboardsView,
)

__all__ = [
    "DashboardOverviewView",
    "ModelRegistryView",
    "ModelDataView",
    "ModelInstanceView",
    "ActivityStreamView",
    "LiveMetricsView",
    "MicroAnalyticsView",
    "AuditTrailView",
    "AuditActionView",
    "SuspiciousActivityView",
    "AdminRoleView",
    "AdminRoleAssignmentView",
    "AdminRoleAssignmentDetailView",
    "AccessOverviewView",
    "MonitoringAlertView",
    "PerformanceInsightsView",
    # User management
    "AdminUserListView",
    "AdminUserDetailView",
    "AdminUserBanView",
    "AdminUserUnbanView",
    "AdminUserTierChangeView",
    "AdminPlatformStatsView",
    # Content moderation
    "AdminContentQueueView",
    "AdminContentQueueSummaryView",
    "AdminContentActionView",
    "AdminContentTrendView",
    # Partner oversight
    "AdminPartnerListView",
    "AdminPartnerDetailView",
    "AdminPartnerStatsView",
    "AdminRevenueStatsView",
    "AdminEngagementStatsView",
    "AdminAnalyticsDashboardsView",
]
