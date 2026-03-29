"""Views for managing admin roles and assignments."""
from rest_framework import status
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


class AdminRoleView(APIView):
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "roles.manage"
    required_app_label = "admin_control"

    def get(self, request):
        roles = AdminRole.objects.all()
        serializer = AdminRoleSerializer(roles, many=True)
        return Response(serializer.data)

    def post(self, request):
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
