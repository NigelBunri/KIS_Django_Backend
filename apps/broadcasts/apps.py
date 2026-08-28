from django.apps import AppConfig


class BroadcastsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.broadcasts"
    label = "broadcasts"
    verbose_name = "Broadcasts"

    def ready(self):
        from . import signals  # noqa: F401
        from .media_hooks import register as register_media_hooks

        register_media_hooks()
