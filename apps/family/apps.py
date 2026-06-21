# apps/family/apps.py
from django.apps import AppConfig


class FamilyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.family"
    label = "family"
    verbose_name = "Family"
