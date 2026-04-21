from django.apps import AppConfig
from django.db.utils import OperationalError, ProgrammingError


class CommerceConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.commerce'
    verbose_name = 'Commerce & Shops'
    
    def ready(self):
        # import signals to ensure they are registered
        from . import signals  # noqa
        from .category_catalog import ensure_catalog_categories

        try:
            ensure_catalog_categories()
        except (OperationalError, ProgrammingError):
            pass
