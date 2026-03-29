"""CRUD discovery views for admin_control."""
from typing import Any, Dict

from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from admin_control.audit.logging import AuditLogger
from admin_control.crud_engine import (
    apply_filters,
    apply_ordering,
    apply_search,
    bulk_action,
    field_metadata,
    list_concrete_fields,
    paginate,
    resolve_model,
    scan_models,
)
from admin_control.permissions import IsAdminControlUser
from admin_control.services import AdminCacheService
from admin_control.serializers import ModelRegistrySerializer


class ModelRegistryView(APIView):
    """Lists every Django model grouped by app label."""

    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "registry.view"
    required_app_label = "admin_control"

    def get(self, request):
        registry = scan_models()
        payload = [{"app_label": key, "models": value} for key, value in registry.items()]
        serializer = ModelRegistrySerializer(payload, many=True)
        return Response(serializer.data)


class ModelDataView(APIView):
    """Dynamic CRUD controller for any Django model."""

    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "crud.read"
    required_app_label = "admin_control"
    required_permission = "crud.read"

    def _resolve(self, app_label: str, model_name: str):
        try:
            return resolve_model(app_label, model_name)
        except LookupError:
            raise NotFound(f"Model {app_label}.{model_name} not found")

    def get(self, request, app_label: str, model_name: str):
        model = self._resolve(app_label, model_name)
        include_deleted = request.query_params.get("include_deleted", "false").lower() == "true"
        filters: Dict[str, Any] = {
            key: value
            for key, value in request.query_params.items()
            if key
            not in {
                "page",
                "per_page",
                "search",
                "ordering",
                "include_deleted",
            }
        }
        try:
            page = max(1, int(request.query_params.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        try:
            per_page = max(1, int(request.query_params.get("per_page", 25)))
        except (TypeError, ValueError):
            per_page = 25
        qs = model.objects.all()
        if not include_deleted and hasattr(model, "is_deleted"):
            qs = qs.filter(is_deleted=False)
        qs = apply_ordering(
            apply_search(
                apply_filters(qs, filters),
                request.query_params.get("search"),
            ),
            request.query_params.get("ordering"),
        )
        cached = AdminCacheService.get_cached_model_list(app_label, model_name)
        if cached:
            page_payload = cached
        else:
            page_payload = paginate(qs, page, per_page, fields=list_concrete_fields(model))
            AdminCacheService.cache_model_list(app_label, model_name, page_payload)
        return Response(
            {
                "app_label": app_label,
                "model": model_name,
                "fields": list_concrete_fields(model),
                "fields_meta": field_metadata(model),
                "data": page_payload["items"],
                "pagination": {
                    "page": page_payload["page"],
                    "per_page": page_payload["per_page"],
                    "total_pages": page_payload["total_pages"],
                    "total_items": page_payload["total_items"],
                },
            }
        )

    def post(self, request, app_label: str, model_name: str):
        model = self._resolve(app_label, model_name)
        action = request.data.get("action")
        if not action or action not in {"soft_delete", "restore", "hard_delete"}:
            raise ValidationError("Invalid or missing action for bulk operation.")
        ids = request.data.get("ids", [])
        if not isinstance(ids, list) or not ids:
            raise ValidationError("Provide a list of primary keys in `ids`.")
        affected = bulk_action(model, action, ids)
        AuditLogger.log(
            actor=request.user if request.user and request.user.is_authenticated else None,
            action_type=f"crud.bulk_action.{action}",
            target_app=app_label,
            target_model=model_name,
            metadata={"ids": ids},
        )
        AdminCacheService.invalidate_model(app_label, model_name)
        AdminCacheService.invalidate_micro()
        AdminCacheService.invalidate_dashboard()
        return Response(
            {"action": action, "affected": affected},
            status=status.HTTP_200_OK,
        )
