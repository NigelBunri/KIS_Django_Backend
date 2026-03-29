"""High-level widgets composing the main control dashboard."""
from typing import Dict

from admin_control.services.dashboard_service import DashboardService


def collect_global_metrics() -> Dict:
    """Return analytics-driven metrics for the Phase 1 dashboard."""
    return DashboardService().gather()
