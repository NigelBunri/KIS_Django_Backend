from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("admin_control", "0002_activity_audit"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AdminRole",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=64, unique=True)),
                ("description", models.TextField(blank=True)),
                ("is_super_role", models.BooleanField(default=False)),
            ],
        ),
        migrations.CreateModel(
            name="AdminRolePermission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("app_label", models.CharField(default="*", max_length=64)),
                ("permissions", models.JSONField(default=list)),
                ("role", models.ForeignKey(on_delete=models.CASCADE, related_name="permissions", to="admin_control.adminrole")),
            ],
            options={
                "unique_together": {("role", "app_label")},
            },
        ),
        migrations.CreateModel(
            name="AdminRoleAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_active", models.BooleanField(default=True)),
                ("granted_at", models.DateTimeField(auto_now_add=True)),
                ("role", models.ForeignKey(on_delete=models.CASCADE, related_name="assignments", to="admin_control.adminrole")),
                ("user", models.ForeignKey(on_delete=models.CASCADE, related_name="admin_roles", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "unique_together": {("user", "role")},
            },
        ),
    ]
