from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.health_ops.models import ContentBlockType, EngineContentBlock, EngineRegistry

User = get_user_model()


def _create_user(phone: str, username: str, is_staff: bool = False):
    return User.objects.create_user(
        phone=phone,
        country="CM",
        password="pass1234",
        username=username,
        display_name=username.title(),
        phone_country_code="+237",
        phone_number=phone.replace("+237", ""),
        is_staff=is_staff,
    )


@override_settings(SECURE_SSL_REDIRECT=False)
class EngineContentBlockPermissionTests(APITestCase):
    """EngineContentBlock is global, platform-wide content attached to an
    engine — shown to every patient of every institution that uses that
    engine. A plain authenticated patient must never be able to author or
    edit it; only platform staff may."""

    def setUp(self):
        self.client = APIClient()
        self.patient = _create_user("+237692000001", "ecb_patient")
        self.admin = _create_user("+237692000002", "ecb_admin", is_staff=True)
        self.engine = EngineRegistry.objects.create(
            code="ecb-test-engine", name="ECB Test Engine", category="workflow",
        )

    def _list_create_url(self):
        return reverse("health-ops-engine-content-list-create", kwargs={"engine_id": self.engine.id})

    def test_patient_cannot_create_content_block(self):
        self.client.force_authenticate(self.patient)
        resp = self.client.post(
            self._list_create_url(),
            {"block_type": ContentBlockType.VIDEO, "title": "Malicious", "text_content": "x"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(EngineContentBlock.objects.filter(engine=self.engine).exists())

    def test_admin_can_create_content_block(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            self._list_create_url(),
            {"block_type": ContentBlockType.VIDEO, "title": "Intro", "text_content": "Welcome"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.content)

    def test_patient_can_still_read_content_blocks(self):
        EngineContentBlock.objects.create(
            engine=self.engine, created_by=self.admin, block_type=ContentBlockType.VIDEO,
            title="Intro", order=1,
        )
        self.client.force_authenticate(self.patient)
        resp = self.client.get(self._list_create_url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data["results"]), 1)

    def test_patient_cannot_edit_or_delete_content_block(self):
        block = EngineContentBlock.objects.create(
            engine=self.engine, created_by=self.admin, block_type=ContentBlockType.VIDEO,
            title="Intro", order=1,
        )
        detail_url = reverse(
            "health-ops-engine-content-detail",
            kwargs={"engine_id": self.engine.id, "content_block_id": block.id},
        )
        self.client.force_authenticate(self.patient)
        resp = self.client.patch(detail_url, {"title": "Hijacked"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        resp = self.client.delete(detail_url)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        block.refresh_from_db()
        self.assertEqual(block.title, "Intro")
