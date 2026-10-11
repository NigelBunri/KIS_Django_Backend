"""
`manage.py check` only validates that ModelAdmin field names exist on the
model - it does not catch a changelist that 500s because list_display pulls
in a non-existent lookup, a raw_id_fields target that isn't a real relation,
etc. This hits every apps.broadcasts admin changelist as a superuser to
prove each one actually renders.

Run:
  python3 manage.py test apps.broadcasts.test_admin_smoke --keepdb -v 2
"""
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from . import models as broadcast_models

User = get_user_model()


class BroadcastAdminChangelistSmokeTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            email="broadcast-admin-smoke@example.com", password="pw123456", country="NG",
        )
        self.client.force_login(self.superuser)

    def test_every_registered_broadcast_model_changelist_renders(self):
        broadcast_model_classes = {
            name for name in dir(broadcast_models)
            if isinstance(getattr(broadcast_models, name), type)
            and issubclass(getattr(broadcast_models, name), broadcast_models.models.Model)
        }
        registered = [
            model for model in admin.site._registry
            if model.__name__ in broadcast_model_classes
        ]
        self.assertTrue(registered, "expected at least one apps.broadcasts model registered in admin")

        failures = []
        for model in registered:
            url = reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")
            response = self.client.get(url)
            if response.status_code != 200:
                failures.append((model.__name__, response.status_code))

        self.assertEqual(failures, [], f"admin changelists failed to render: {failures}")
