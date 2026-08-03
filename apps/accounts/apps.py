from django.apps import AppConfig

class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"

    def ready(self):
        # import signals to ensure they are registered
        from . import signals  # noqa
        from . import spectacular_extensions  # noqa: F401

        from .media_hooks import register as register_media_hooks

        register_media_hooks()
