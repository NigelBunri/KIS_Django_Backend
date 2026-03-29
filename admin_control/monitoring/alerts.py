"""Advanced monitoring helpers for admin control."""
from datetime import timedelta
from typing import Dict, List

from django.utils import timezone

from admin_control.activity.models import AdminUserActivity
from admin_control.audit.logging import SuspiciousActivityDetector


class MonitoringAlertService:
    """Detect anomalies and define alerts for the dashboard."""

    def __init__(self):
        self.now = timezone.now()
        self.activity_qs = AdminUserActivity.objects.all()
        self.detector = SuspiciousActivityDetector()

    def detect(self) -> List[Dict]:
        alerts = []
        alerts.extend(self._request_spike())
        alerts.extend(self._error_spike())
        alerts.extend(self._slow_requests())
        return [alert for alert in alerts if alert]

    def _request_spike(self) -> Dict:
        one_hour = self.now - timedelta(hours=1)
        window_count = self.activity_qs.filter(created_at__gte=one_hour).count()
        baseline = self.activity_qs.filter(created_at__lt=one_hour).count() or 1
        if window_count > baseline * 2:
            return {
                "type": "rpm_spike",
                "value": window_count,
                "baseline": baseline,
                "message": "Requests per minute doubled compared to historical volume",
+                "severity": "warning",
            }
        return {}

    def _error_spike(self) -> Dict:
        one_hour = self.now - timedelta(hours=1)
        errors = self.activity_qs.filter(created_at__gte=one_hour, status_code__gte=500).count()
        baseline = self.activity_qs.filter(status_code__gte=500).count() or 1
        if errors > baseline * 1.5:
            return {
                "type": "error_spike",
                "value": errors,
                "baseline": baseline,
                "message": "Server errors spiked in the last hour",
+                "severity": "critical",
            }
        return {}

    def _slow_requests(self) -> List[Dict]:
        slow_qs = self.activity_qs.filter(duration_ms__gt=2500, created_at__gte=self.now - timedelta(minutes=15))
        if slow_qs.exists():
            alerts = []
            for entry in slow_qs[:5]:
                alerts.append(
                    {
                        "type": "slow_request",
                        "path": entry.path,
                        "duration_ms": entry.duration_ms,
                        "severity": "warning",
                    }
                )
            return alerts
        return []
