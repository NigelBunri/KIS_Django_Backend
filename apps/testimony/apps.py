from django.apps import AppConfig

class TestimonyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.testimony"

    def ready(self):
        from .media_hooks import register as register_media_hooks

        register_media_hooks()
