"""Service layer orchestrating dashboard data."""
from typing import Dict

from admin_control.analytics.insights import AnalyticsInsightService
from admin_control.services.cache import AdminCacheService


class DashboardService:
    """Coordinate data collection for the admin dashboard with caching."""

    def gather(self) -> Dict:
        cached = AdminCacheService.get_dashboard()
        if cached:
            return cached
        payload = AnalyticsInsightService().collect()
        AdminCacheService.set_dashboard(payload)
        return payload
