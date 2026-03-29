from django.apps import AppConfig


class AdminControlConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "admin_control"
    verbose_name = "Custom Admin Control Panel"

    def ready(self):
        import admin_control.signals  # noqa: F401
