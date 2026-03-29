"""CRUD engine package for admin_control."""

from .operations import (
    apply_filters,
    apply_ordering,
    apply_search,
    base_queryset,
    bulk_action,
    field_metadata,
    instance_to_dict,
    list_concrete_fields,
    paginate,
    relation_warnings,
    resolve_instance,
    resolve_model,
    update_instance,
)
from .registry import get_model_fields, scan_models

__all__ = [
    "scan_models",
    "get_model_fields",
    "resolve_model",
    "resolve_instance",
    "instance_to_dict",
    "field_metadata",
    "relation_warnings",
    "update_instance",
    "base_queryset",
    "apply_filters",
    "apply_search",
    "apply_ordering",
    "paginate",
    "list_concrete_fields",
    "bulk_action",
]
