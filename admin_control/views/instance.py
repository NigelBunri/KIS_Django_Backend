"""Instance-level CRUD operations for admin_control."""
from typing import Dict

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from admin_control.audit.logging import AuditLogger
from admin_control.crud_engine import (
    field_metadata,
    instance_to_dict,
    relation_warnings,
    resolve_instance,
    update_instance,
)
from admin_control.models import AdminAuditEntry
from admin_control.permissions import IsAdminControlUser
from admin_control.services import AdminCacheService


class ModelInstanceView(APIView):
    """Handles detail fetching, updates, and delete warnings for every model."""

    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "crud.update"
    required_app_label = "admin_control"

    def get(self, request, app_label: str, model_name: str, pk: str):
        instance = resolve_instance(app_label, model_name, pk)
        fields_meta = field_metadata(instance.__class__)
        return Response(
            {
                "data": instance_to_dict(instance),
                "fields_meta": fields_meta,
                "relation_warnings": relation_warnings(instance),
            }
        )

    def patch(self, request, app_label: str, model_name: str, pk: str):
        instance = resolve_instance(app_label, model_name, pk)
        payload = request.data
        update_instance(instance, payload)
        AuditLogger.log(
            actor=request.user if request.user and request.user.is_authenticated else None,
            action_type=f"crud.update",
            target_app=app_label,
            target_model=model_name,
            target_pk=pk,
            metadata={"updated_fields": list(payload.keys())},
        )
        AdminCacheService.invalidate_model(app_label, model_name)
        AdminCacheService.invalidate_dashboard()
        AdminCacheService.invalidate_micro()
        return Response({"data": instance_to_dict(instance)})

    def delete(self, request, app_label: str, model_name: str, pk: str):
        instance = resolve_instance(app_label, model_name, pk)
        warnings = relation_warnings(instance)
        confirm = request.query_params.get("confirm", "false").lower() == "true"
        if warnings and not confirm:
            return Response(
                {"detail": "Cascade dependencies detected", "cascade": warnings},
                status=status.HTTP_409_CONFLICT,
            )
        if hasattr(instance, "is_deleted"):
            instance.is_deleted = True
            instance.save(update_fields=["is_deleted"])
        else:
            instance.delete()
        AuditLogger.log(
            actor=request.user if request.user and request.user.is_authenticated else None,
            action_type="crud.delete",
            target_app=app_label,
            target_model=model_name,
            target_pk=pk,
            severity=AdminAuditEntry.Severity.WARNING,
            metadata={"cascade_warnings": warnings},
        )
        AdminCacheService.invalidate_model(app_label, model_name)
        AdminCacheService.invalidate_micro()
        AdminCacheService.invalidate_dashboard()
        return Response(status=status.HTTP_204_NO_CONTENT)
