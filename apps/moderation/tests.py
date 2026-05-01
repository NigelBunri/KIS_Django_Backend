from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from . import models


class ModerationAccessBoundaryTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(phone="+237670002001", password="TestPass123!", country="CM")
        self.admin = User.objects.create_user(
            phone="+237670002002",
            password="TestPass123!",
            country="CM",
            is_staff=True,
        )
        self.client.force_authenticate(self.user)

    def test_non_staff_cannot_list_moderation_audit_logs(self):
        response = self.client.get("/api/v1/audit-logs/")

        self.assertEqual(response.status_code, 403)

    def test_non_staff_flag_create_cannot_spoof_reporter_or_source(self):
        other_id = self.admin.id
        response = self.client.post(
            "/api/v1/flags/",
            {
                "source": "SYSTEM",
                "target_type": "USER",
                "target_id": str(other_id),
                "reporter_id": str(other_id),
                "reason": "abuse",
                "severity": "LOW",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        flag = models.Flag.objects.get(id=response.data["id"])
        self.assertEqual(flag.source, "USER")
        self.assertEqual(str(flag.reporter_id), str(self.user.id))
