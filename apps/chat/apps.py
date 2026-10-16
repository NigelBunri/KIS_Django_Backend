from django.apps import AppConfig


class ChatConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.chat'

    def ready(self):
        from . import signals  # noqa: F401
        from .media_hooks import register as register_media_hooks

        register_media_hooks()
