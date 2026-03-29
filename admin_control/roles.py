"""Role & permission models for the admin control platform."""
from django.conf import settings
from django.db import models
from django.utils import timezone


class AdminRole(models.Model):
    """Defines reusable roles for admin control users."""

    name = models.CharField(max_length=64, unique=True)
    description = models.TextField(blank=True)
    is_super_role = models.BooleanField(default=False)

    def __str__(self):
        return self.name


class AdminRolePermission(models.Model):
    """Maps roles to app-level permission scopes."""

    ALLOWED_ALL = "*"

    role = models.ForeignKey(AdminRole, related_name="permissions", on_delete=models.CASCADE)
    app_label = models.CharField(max_length=64, default=ALLOWED_ALL)
    permissions = models.JSONField(default=list)

    class Meta:
        unique_together = ("role", "app_label")

    def allows(self, permission: str) -> bool:
        if not permission:
            return True
        perms = self.permissions or []
        if self.ALLOWED_ALL in perms:
            return True
        return permission in perms


class AdminRoleAssignment(models.Model):
    """Attach a role to a user."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="admin_roles")
    role = models.ForeignKey(AdminRole, on_delete=models.CASCADE, related_name="assignments")
    is_active = models.BooleanField(default=True)
    granted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ("user", "role")


class AdminAccessService:
    """Helper for checking whether a user can access a scoped area."""

    @classmethod
    def is_super_admin(cls, user) -> bool:
        return AdminRoleAssignment.objects.filter(
            user=user,
            is_active=True,
            role__is_super_role=True,
        ).exists()

    @classmethod
    def has_permission(cls, user, app_label: str = None, permission: str = "crud.access") -> bool:
        if not user or not user.is_authenticated:
            return False
        if cls.is_super_admin(user):
            return True
        assignments = AdminRoleAssignment.objects.filter(user=user, is_active=True).select_related("role").prefetch_related("role__permissions")
        for assignment in assignments:
            for perm in assignment.role.permissions.all():
                allowed_app = app_label or perm.ALLOWED_ALL
                if perm.app_label not in (allowed_app, perm.ALLOWED_ALL, None):
                    continue
                if perm.allows(permission):
                    return True
        return False
