"""Helper operations powering the admin CRUD controller."""
from typing import Dict, Iterable, List, Optional

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import models
from django.db.models import CharField, Model, Q, TextField


def resolve_instance(app_label: str, model_name: str, pk: str):
    model = resolve_model(app_label, model_name)
    return model.objects.get(pk=pk)


def instance_to_dict(instance: models.Model, fields: Optional[List[str]] = None) -> Dict:
    if fields:
        values = {field: getattr(instance, field) for field in fields}
    else:
        values = {
            field.name: getattr(instance, field.name)
            for field in instance._meta.concrete_fields
            if not field.auto_created
        }
    return values


def field_metadata(model: Model) -> List[Dict]:
    metadata = []
    for field in model._meta.concrete_fields:
        if field.auto_created:
            continue
        descriptor = {
            "name": field.name,
            "type": field.get_internal_type(),
            "required": not field.blank and not getattr(field, "null", False),
            "read_only": not field.editable,
            "help_text": field.help_text,
            "default": field.get_default(),
            "choices": getattr(field, "choices", None),
            "primary_key": field.primary_key,
        }
        if field.is_relation and getattr(field, "related_model", None):
            descriptor["related_model"] = (
                f"{field.related_model._meta.app_label}.{field.related_model.__name__}"
            )
        metadata.append(descriptor)
    return metadata


def relation_warnings(instance: models.Model) -> List[Dict]:
    warnings = []
    for rel in instance._meta.related_objects:
        accessor = rel.get_accessor_name()
        value = getattr(instance, accessor, None)
        if value is None:
            continue
        if hasattr(value, "all"):
            count = value.all().count()
        elif hasattr(value, "count"):
            count = value.count()
        else:
            count = 1
        if count == 0:
            continue
        warnings.append(
            {
                "related_model": f"{rel.related_model._meta.app_label}.{rel.related_model.__name__}",
                "count": count,
                "cascade": rel.on_delete == models.CASCADE,
                "field_name": rel.field.name,
            }
        )
    return warnings


def update_instance(instance: models.Model, payload: Dict) -> models.Model:
    for key, value in payload.items():
        try:
            field = instance._meta.get_field(key)
        except FieldDoesNotExist:
            continue
        if not field.editable or field.auto_created:
            continue
        setattr(instance, key, value)
    instance.save()
    return instance


def resolve_model(app_label: str, model_name: str) -> Model:
    return apps.get_model(app_label, model_name)


def list_concrete_fields(model: Model) -> List[str]:
    return [field.name for field in model._meta.concrete_fields if not field.auto_created]


def apply_filters(queryset: models.QuerySet, filters: Dict[str, str]) -> models.QuerySet:
    for key, value in filters.items():
        if value is None:
            continue
        try:
            queryset.model._meta.get_field(key)
        except (LookupError, ValueError):
            continue
        queryset = queryset.filter(**{key: value})
    return queryset


def apply_search(queryset: models.QuerySet, search_term: Optional[str]) -> models.QuerySet:
    if not search_term:
        return queryset
    search_fields = [
        field.name
        for field in queryset.model._meta.concrete_fields
        if isinstance(field, (models.CharField, models.TextField))
    ]
    if not search_fields:
        return queryset
    query = Q()
    for field in search_fields:
        query |= Q(**{f"{field}__icontains": search_term})
    return queryset.filter(query)


def apply_ordering(queryset: models.QuerySet, ordering: Optional[str]) -> models.QuerySet:
    if not ordering:
        return queryset
    ordering_parts = [part.strip() for part in ordering.split(",") if part.strip()]
    return queryset.order_by(*ordering_parts)


def paginate(queryset: models.QuerySet, page: int, per_page: int, fields: Optional[List[str]] = None) -> Dict:
    paginator = Paginator(queryset, per_page)
    try:
        page_obj = paginator.page(page)
    except (EmptyPage, PageNotAnInteger):
        page_obj = paginator.page(1)
    items = page_obj.object_list
    if fields:
        payload = list(items.values(*fields))
    else:
        payload = list(items.values())
    return {
        "page": page_obj.number,
        "per_page": per_page,
        "total_pages": paginator.num_pages,
        "total_items": paginator.count,
        "items": payload,
    }


def base_queryset(model: Model, include_deleted: bool = False) -> models.QuerySet:
    qs = model.objects.all()
    if not include_deleted and hasattr(model, "is_deleted"):
        qs = qs.filter(is_deleted=False)
    return qs


def bulk_action(model: Model, action: str, ids: Iterable) -> int:
    qs = model.objects.filter(pk__in=ids)
    if action == "soft_delete" and hasattr(model, "is_deleted"):
        return qs.update(is_deleted=True)
    if action == "restore" and hasattr(model, "is_deleted"):
        return qs.filter(is_deleted=True).update(is_deleted=False)
    if action == "hard_delete":
        deleted, _ = qs.delete()
        return deleted
    return 0
