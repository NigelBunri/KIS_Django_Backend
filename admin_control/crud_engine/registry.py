"""Dynamic discovery layer for admin CRUD exposure."""
from typing import Dict, List
from django.apps import apps
from django.db import models


def scan_models() -> Dict[str, List[str]]:
    """Return all registered models grouped by app label."""
    registry: Dict[str, List[str]] = {}
    for model in apps.get_models():
        label = model._meta.app_label
        registry.setdefault(label, []).append(model.__name__)
    return registry


def get_model_fields(model: models.Model) -> List[str]:
    return [field.name for field in model._meta.get_fields() if not field.auto_created]
