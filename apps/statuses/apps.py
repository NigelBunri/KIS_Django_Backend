from django.apps import AppConfig


class StatusesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.statuses"
    verbose_name = "Statuses"

    def ready(self):
        from .media_hooks import register as register_media_hooks

        register_media_hooks()
