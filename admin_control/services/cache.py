"""Cache helpers used by admin analytics/services."""
from typing import Any

from django.core.cache import cache


class AdminCacheService:
    """Simple cache utility for dashboard and micro analytics."""

    DASHBOARD_KEY = "admin_control:dashboard:summary"
    MICRO_KEY = "admin_control:dashboard:micro_apps"
    MODEL_KEY_TEMPLATE = "admin_control:crud:{app}:{model}:list"
    DEFAULT_TIMEOUT = 60  # seconds

    @classmethod
    def get_dashboard(cls) -> Any:
        return cache.get(cls.DASHBOARD_KEY)

    @classmethod
    def set_dashboard(cls, payload: Any, timeout: int = None) -> None:
        cache.set(cls.DASHBOARD_KEY, payload, timeout or cls.DEFAULT_TIMEOUT)

    @classmethod
    def get_micro(cls) -> Any:
        return cache.get(cls.MICRO_KEY)

    @classmethod
    def set_micro(cls, payload: Any, timeout: int = None) -> None:
        cache.set(cls.MICRO_KEY, payload, timeout or cls.DEFAULT_TIMEOUT)

    @classmethod
    def invalidate_dashboard(cls) -> None:
        cache.delete(cls.DASHBOARD_KEY)

    @classmethod
    def invalidate_micro(cls) -> None:
        cache.delete(cls.MICRO_KEY)

    @classmethod
    def invalidate_model(cls, app_label: str, model_name: str) -> None:
        cache.delete(cls.MODEL_KEY_TEMPLATE.format(app=app_label, model=model_name))

    @classmethod
    def cache_model_list(cls, app_label: str, model_name: str, payload: Any, timeout: int = None) -> None:
        cache.set(
            cls.MODEL_KEY_TEMPLATE.format(app=app_label, model=model_name),
            payload,
            timeout or cls.DEFAULT_TIMEOUT,
        )

    @classmethod
    def get_cached_model_list(cls, app_label: str, model_name: str) -> Any:
        return cache.get(cls.MODEL_KEY_TEMPLATE.format(app=app_label, model=model_name))
