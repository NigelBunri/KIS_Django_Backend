"""Permissions for guarding admin control endpoints."""
from rest_framework.permissions import BasePermission

from admin_control.roles import AdminAccessService


class IsAdminControlUser(BasePermission):
    """Only allow users with admin_control role flag."""

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not user or not getattr(user, "is_authenticated", False):
            return False
        resolver_kwargs = getattr(request, "resolver_match", None)
        app_label = None
        if resolver_kwargs:
            app_label = resolver_kwargs.kwargs.get("app_label")
        if not app_label and hasattr(view, "required_app_label"):
            app_label = view.required_app_label
        required_perm = getattr(view, "required_permission", "crud.access")
        return AdminAccessService.has_permission(user, app_label=app_label, permission=required_perm)
