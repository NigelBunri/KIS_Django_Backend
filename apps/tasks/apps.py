from django.apps import AppConfig


class TasksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.tasks"
    verbose_name = "Partner Tasks"

    def ready(self):
        from .media_hooks import register as register_media_hooks

        register_media_hooks()
