"""Serializers for admin roles & permissions."""
from rest_framework import serializers

from admin_control.roles import AdminRole, AdminRoleAssignment, AdminRolePermission


class AdminRolePermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdminRolePermission
        fields = ["app_label", "permissions"]


class AdminRoleSerializer(serializers.ModelSerializer):
    permissions = AdminRolePermissionSerializer(many=True, required=False)

    class Meta:
        model = AdminRole
        fields = ["id", "name", "description", "is_super_role", "permissions"]


class AdminRoleAssignmentSerializer(serializers.ModelSerializer):
    role_name = serializers.CharField(source="role.name", read_only=True)
    user_email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = AdminRoleAssignment
        fields = [
            "id",
            "user",
            "role",
            "is_active",
            "granted_at",
            "role_name",
            "user_email",
        ]
