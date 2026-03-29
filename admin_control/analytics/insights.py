"""Insight engine powering the admin analytics widgets."""
from datetime import timedelta
from typing import List, Dict

from django.db.models import Count, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.models import BillingReconciliation, WalletTransaction
from apps.chat.models import Conversation
from apps.core.models import HealthcareOrganization
from apps.events.models import TicketSale
from admin_control.activity.models import AdminUserActivity
from admin_control.analytics.micro import MicroAppInsightService
from admin_control.crud_engine import scan_models
from admin_control.monitoring import health_check


class AnalyticsInsightService:
    """Compute enterprise-grade metrics for the admin dashboard."""

    def __init__(self):
        self.now = timezone.now()
        self.activity_qs = AdminUserActivity.objects.all()

    def collect(self) -> Dict:
        widgets = self._build_widgets()
        graphs = self._build_graphs()
        return {
            "status": "ready",
            "generated_at": self.now,
            "widgets": widgets,
            "graphs": graphs,
            "top_institutions": self._top_institutions(),
            "suspicious_activity": self._suspicious_activity(),
            "system_health": health_check(),
            "database_growth": self._database_growth(),
            "micro_apps": self._per_app_micro_insights(),
            "micro_apps_detailed": MicroAppInsightService().collect(),
            "live_activity": self._recent_activity(),
            "api_usage_per_endpoint": self._api_usage_per_endpoint(),
            "institution_activity": self._institution_activity(),
            "institution_growth": self._institution_growth(),
            "chat_metrics": self._chat_metrics(),
            "message_throughput": self._message_throughput(),
        }

    def _build_widgets(self) -> Dict[str, Dict]:
        total_users = User.objects.count()
        active_24h = self._active_users(hours=24)
        active_7d = self._active_users(hours=168)
        active_30d = self._active_users(hours=720)
        rpm = self._requests_per_minute()
        revenue = self._revenue_cents() / 100.0
        return {
            "total_users": {"value": total_users, "label": "Total users"},
            "active_users": {
                "value": active_24h,
                "label": "Active (24h)",
                "aux": {"7d": active_7d, "30d": active_30d},
            },
            "requests_per_minute": {"value": rpm, "label": "Requests per minute"},
            "revenue": {"value": round(revenue, 2), "label": "Revenue (USD)"},
        }

    def _build_graphs(self) -> Dict[str, List[Dict]]:
        conversation_volume = Conversation.objects.filter(
            created_at__gte=self.now - timedelta(hours=24)
        ).count()
        ticket_volume = TicketSale.objects.filter(
            purchased_at__gte=self.now - timedelta(hours=24), status="completed"
        ).count()
        return {
            "active_user_trend": [
                {"label": "24h", "value": self._active_users(24)},
                {"label": "7d", "value": self._active_users(168)},
                {"label": "30d", "value": self._active_users(720)},
            ],
            "requests": [
                {"label": "1m", "value": self._requests_per_minute()},
                {"label": "15m", "value": self._requests_per_minute(window=15)},
                {"label": "1h", "value": self._requests_per_minute(window=60)},
            ],
            "error_rate": [
                {
                    "label": "Errors last hour",
                    "value": self._error_rate(window=60),
                }
            ],
            "message_volume": [
                {"label": "Chat volume (24h)", "value": conversation_volume}
            ],
            "booking_volume": [
                {"label": "Ticket sales (24h)", "value": ticket_volume}
            ],
            "revenue_trend": [
                {"label": "Revenue (USD)", "value": round(self._revenue_cents() / 100.0, 2)}
            ],
            "institution_activity": [
                {
                    "label": entry.get("status", "unknown"),
                    "value": entry.get("count", 0),
                }
                for entry in self._institution_activity()
            ],
        }

    def _active_users(self, hours: int) -> int:
        since = self.now - timedelta(hours=hours)
        return (
            self.activity_qs.filter(created_at__gte=since)
            .values("actor_id")
            .distinct()
            .count()
        )

    def _requests_per_minute(self, window: int = 1) -> int:
        since = self.now - timedelta(minutes=window)
        return self.activity_qs.filter(created_at__gte=since).count()

    def _error_rate(self, window: int = 60) -> float:
        since = self.now - timedelta(minutes=window)
        total = self.activity_qs.filter(created_at__gte=since).count() or 1
        errors = self.activity_qs.filter(created_at__gte=since, status_code__gte=400).count()
        return round((errors / total) * 100, 2)

    def _revenue_cents(self) -> int:
        return (
            WalletTransaction.objects.filter(status="success")
            .aggregate(total=Sum("amount_cents"))
            .get("total")
            or 0
        )

    def _database_growth(self) -> Dict[str, int]:
        return {
            "users": User.objects.count(),
            "transactions": WalletTransaction.objects.count(),
            "conversations": Conversation.objects.count(),
            "ticket_sales": TicketSale.objects.count(),
            "admin_activity": AdminUserActivity.objects.count(),
        }

    def _top_institutions(self) -> List[Dict]:
        entries = (
            BillingReconciliation.objects.filter(organization__isnull=False)
            .values("organization__name")
            .annotate(total=Sum("amount_cents"))
            .order_by("-total")[:5]
        )
        return [
            {
                "name": entry.get("organization__name") or "Unknown",
                "value": round(entry.get("total", 0) / 100.0, 2),
            }
            for entry in entries
        ]

    def _suspicious_activity(self) -> List[Dict]:
        offenders = (
            self.activity_qs.filter(status_code__gte=400)
            .values("actor_id")
            .annotate(count=Count("pk"))
            .filter(count__gt=5)
            .order_by("-count")[:5]
        )
        return [
            {"user_id": entry.get("actor_id"), "errors": entry.get("count")}
            for entry in offenders
        ]

    def _per_app_micro_insights(self) -> List[Dict]:
        registry = scan_models()
        insights = []
        for app_label, models_list in registry.items():
            activity = self.activity_qs.filter(path__icontains=app_label).count()
            insights.append(
                {
                    "app_label": app_label,
                    "model_count": len(models_list),
                    "recent_activity": activity,
                }
            )
        return insights

    def _recent_activity(self) -> List[Dict]:
        activity_qs = (
            self.activity_qs.order_by("-created_at")
            .values(
                "id",
                "actor_id",
                "path",
                "method",
                "status_code",
                "ip_address",
                "device",
                "duration_ms",
                "created_at",
            )
            .distinct()
        )
        return list(activity_qs[:10])

    def _api_usage_per_endpoint(self) -> List[Dict]:
        entries = (
            self.activity_qs.values("path")
            .annotate(count=Count("pk"))
            .order_by("-count")[:10]
        )
        return [
            {"endpoint": entry.get("path"), "count": entry.get("count", 0)}
            for entry in entries
        ]

    def _institution_activity(self) -> List[Dict]:
        entries = (
            HealthcareOrganization.objects.values("status")
            .annotate(count=Count("pk"))
            .order_by("-count")
        )
        return [
            {"status": entry.get("status"), "count": entry.get("count", 0)}
            for entry in entries
        ]

    def _institution_growth(self) -> List[Dict]:
        entries = (
            HealthcareOrganization.objects.annotate(month=TruncMonth("created_at"))
            .values("month")
            .annotate(count=Count("pk"))
            .order_by("month")
        )
        return [
            {
                "month": entry.get("month").isoformat()
                if entry.get("month") is not None
                else None,
                "count": entry.get("count", 0),
            }
            for entry in entries
        ]

    def _chat_metrics(self) -> List[Dict]:
        entries = (
            Conversation.objects.values("type")
            .annotate(total=Count("pk"))
            .order_by("-total")[:5]
        )
        return [
            {"type": entry.get("type"), "count": entry.get("total", 0)}
            for entry in entries
        ]

    def _message_throughput(self) -> Dict[str, int]:
        since = self.now - timedelta(hours=24)
        conversations = Conversation.objects.filter(last_message_at__gte=since).count()
        ticket_sales = TicketSale.objects.filter(
            purchased_at__gte=since, status="completed"
        ).count()
        return {
            "last_24h_conversations": conversations,
            "tickets_booked": ticket_sales,
        }
