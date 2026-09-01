# apps/partners/apps.py
from django.apps import AppConfig


class PartnersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.partners"
    label = "partners"
    verbose_name = "Partners"

    def ready(self) -> None:
        from .seed import ensure_kis_partner
        from . import signals  # noqa: F401
        from .media_hooks import register as register_media_hooks
        from .scheduler import register_scheduled_post_sweep

        ensure_kis_partner()
        register_media_hooks()
        register_scheduled_post_sweep()
