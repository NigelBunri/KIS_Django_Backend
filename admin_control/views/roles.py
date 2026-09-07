"""Views for managing admin roles and assignments."""
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from admin_control.roles import AdminAccessService, AdminRole, AdminRoleAssignment, AdminRolePermission
from admin_control.permissions import IsAdminControlUser
from admin_control.serializers import (
    AdminRoleAssignmentSerializer,
    AdminRolePermissionSerializer,
    AdminRoleSerializer,
)

# SECURITY: is_super_role/role-assignment are the two direct paths to full
# admin-panel compromise (a super-admin bypasses every permission check -
# see AdminAccessService.has_permission), so both must require the ACTOR
# to already be a super-admin, not just hold the generic roles.manage/
# roles.assign permission a much less trusted role could plausibly have.
# Found via a 2026-09-07 foundation audit alongside the CRUD-engine
# blocklist in crud_engine/operations.py - that fix stops editing an
# existing role's is_super_role via the generic engine, this fix closes
# the same escalation via these views' own, intended write paths.
_SUPER_ROLE_ACTION_MESSAGE = "Only an existing super-admin can create, assign, or activate a super-admin role."


class AdminRoleView(APIView):
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "roles.manage"
    required_app_label = "admin_control"

    def get(self, request):
        roles = AdminRole.objects.all()
        serializer = AdminRoleSerializer(roles, many=True)
        return Response(serializer.data)

    def post(self, request):
        if request.data.get("is_super_role") and not AdminAccessService.is_super_admin(request.user):
            raise PermissionDenied(_SUPER_ROLE_ACTION_MESSAGE)
        serializer = AdminRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        role = serializer.save()
        perm_data = request.data.get("permissions", [])
        if perm_data:
            AdminRolePermission.objects.bulk_create(
                [
                    AdminRolePermission(role=role, app_label=item.get("app_label", "*"), permissions=item.get("permissions", []))
                    for item in perm_data
                ]
            )
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class AdminRoleAssignmentView(APIView):
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "roles.assign"
    required_app_label = "admin_control"

    def get(self, request):
        assignments = AdminRoleAssignment.objects.select_related("role", "user").all()
        serializer = AdminRoleAssignmentSerializer(assignments, many=True)
        return Response(serializer.data)

    def post(self, request):
        role_id = request.data.get("role")
        if role_id and AdminRole.objects.filter(pk=role_id, is_super_role=True).exists():
            if not AdminAccessService.is_super_admin(request.user):
                raise PermissionDenied(_SUPER_ROLE_ACTION_MESSAGE)
        serializer = AdminRoleAssignmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class AdminRoleAssignmentDetailView(APIView):
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "roles.assign"
    required_app_label = "admin_control"

    def patch(self, request, pk):
        try:
            assignment = AdminRoleAssignment.objects.select_related("role").get(pk=pk)
        except AdminRoleAssignment.DoesNotExist:
            return Response({"detail": "assignment not found"}, status=status.HTTP_404_NOT_FOUND)
        # Covers both re-activating an existing super-role assignment
        # (is_active: true) and re-pointing this assignment at a different,
        # super role via `role` - either way the resulting live assignment
        # must not grant super-admin unless the actor already has it.
        target_role_id = request.data.get("role", assignment.role_id)
        target_is_active = request.data.get("is_active", assignment.is_active)
        if target_is_active and AdminRole.objects.filter(pk=target_role_id, is_super_role=True).exists():
            if not AdminAccessService.is_super_admin(request.user):
                raise PermissionDenied(_SUPER_ROLE_ACTION_MESSAGE)
        serializer = AdminRoleAssignmentSerializer(assignment, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class AccessOverviewView(APIView):
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "roles.view"
    required_app_label = "admin_control"

    TAB_RULES = {
        "dashboard": {"app": "admin_control", "permission": "dashboard.view"},
        "analytics": {"app": "admin_control", "permission": "micro.view"},
        "crud": {"app": "admin_control", "permission": "registry.view"},
        "activity": {"app": "admin_control", "permission": "activity.view"},
        "rbac": {"app": "admin_control", "permission": "roles.manage"},
        "monitoring": {"app": "admin_control", "permission": "monitoring.view"},
    }

    def get(self, request):
        user = request.user
        roles = AdminRole.objects.prefetch_related("permissions").all()
        assignments = AdminRoleAssignment.objects.select_related("role").filter(user=user, is_active=True)
        role_serializer = AdminRoleSerializer(roles, many=True)
        assignment_serializer = AdminRoleAssignmentSerializer(assignments, many=True)
        tabs = {
            key: AdminAccessService.has_permission(user, app_label=rule["app"], permission=rule["permission"])
            for key, rule in self.TAB_RULES.items()
        }
        return Response(
            {
                "is_super_admin": AdminAccessService.is_super_admin(user),
                "assignments": assignment_serializer.data,
                "roles": role_serializer.data,
                "tabs": tabs,
            }
        )
