"""App-level micro analytics for the admin control dashboard."""
from collections import Counter, defaultdict
from typing import Dict, List, Optional

from django.utils import timezone

from admin_control.activity.models import AdminUserActivity
from admin_control.crud_engine import scan_models
from admin_control.services.cache import AdminCacheService


class MicroAppInsightService:
    """Computes per-app usage frequency, top models/users, conversions, and heatmaps."""

    def __init__(self):
        self.now = timezone.now()
        self.activity_qs = AdminUserActivity.objects.all()
        self.registry = scan_models()

    def collect(self) -> List[Dict]:
        cached = AdminCacheService.get_micro()
        if cached:
            return cached
        insights: List[Dict] = []
        for app_label, models in self.registry.items():
            insight = self._app_insight(app_label, models)
            if insight:
                insights.append(insight)
        AdminCacheService.set_micro(insights)
        return insights

    def _app_insight(self, app_label: str, models: List[str]) -> Optional[Dict]:
        app_activity = self.activity_qs.filter(path__icontains=f"/crud/{app_label}/")
        total_actions = app_activity.count()
        if total_actions == 0:
            return None
        model_counter = Counter(self._parse_model_from_path(item.path) for item in app_activity)
        top_models = [
            {"model": model, "actions": count}
            for model, count in model_counter.most_common(3)
            if model
        ]
        user_counter = Counter(app_activity.values_list("actor_id", flat=True))
        top_users = [
            {"user_id": user, "actions": count}
            for user, count in user_counter.most_common(3)
            if user
        ]
        heatmap = self._crud_heatmap(app_activity)
        conversion = self._conversion_ratio(app_activity)
        adopted_features = len({item for item in model_counter if item})
        adoption_rate = adopted_features / max(len(models), 1)
        return {
            "app_label": app_label,
            "usage_frequency": total_actions,
            "top_models": top_models,
            "top_users": top_users,
            "conversion_rate": round(conversion, 2),
            "feature_adoption": round(adoption_rate * 100, 1),
            "crud_heatmap": heatmap,
        }

    def _parse_model_from_path(self, path: str) -> Optional[str]:
        parts = [segment for segment in path.strip("/").split("/") if segment]
        if "crud" in parts:
            idx = parts.index("crud")
            if idx + 2 < len(parts):
                return parts[idx + 2]
        return None

    def _crud_heatmap(self, queryset) -> Dict[str, Dict[int, int]]:
        buckets: Dict[str, Counter] = defaultdict(Counter)
        for entry in queryset.values("method", "created_at"):
            method = (entry.get("method") or "UNKNOWN").upper()
            timestamp = entry.get("created_at")
            hour = timestamp.hour if timestamp else self.now.hour
            buckets[method][hour] += 1
        return {
            method: dict(counter)
            for method, counter in buckets.items()
        }

    def _conversion_ratio(self, queryset) -> float:
        writes = queryset.filter(method__in=["POST", "PUT", "PATCH", "DELETE"]).count()
        reads = queryset.filter(method="GET").count() or 1
        return writes / reads
