from django.apps import AppConfig


class HealthOpsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.health_ops"
    verbose_name = "Health Operations"

    def ready(self):
        from . import signals  # noqa: F401

