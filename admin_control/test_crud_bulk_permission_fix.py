"""Regression tests for two related generic-CRUD-engine gaps found while
building enterprise admin tooling:

1. ModelDataView assigned `required_permission = "crud.read"` twice (the
   second silently shadowed the first), so a role granted only read
   access to a model could still POST a bulk soft_delete/hard_delete/
   restore against it. Fixed by making `required_permission` a
   per-request property: "crud.write" for POST, "crud.read" otherwise.

2. `crud_engine.bulk_action()` took no tenant context - an id list could
   act on any partner's rows regardless of which organization the caller
   was scoped to. Fixed by an optional `partner_id` filter.

Run:
  python3 manage.py test admin_control.test_crud_bulk_permission_fix --keepdb -v 2
"""
from __future__ import annotations

import uuid

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chat.models import Conversation, ConversationType
from apps.channels.models import Channel
from apps.partners.models import Partner
from apps.tasks.models import Task
from admin_control.roles import AdminRole, AdminRoleAssignment, AdminRolePermission

_counter = 0


def _make_user(email, **kw):
    global _counter
    _counter += 1
    phone = kw.pop("phone", f"+2376551{_counter:05d}")
    country = kw.pop("country", "CM")
    return User.objects.create_user(phone=phone, email=email, password="test1234!", country=country, **kw)


def _make_scoped_admin_role(user, *, app_label="tasks", permissions):
    global _counter
    _counter += 1
    role = AdminRole.objects.create(name=f"scoped-{user.id}-{_counter}", is_super_role=False)
    AdminRolePermission.objects.create(role=role, app_label=app_label, permissions=list(permissions))
    AdminRoleAssignment.objects.create(user=user, role=role, is_active=True)
    return role


def _make_partner(owner):
    global _counter
    _counter += 1
    return Partner.objects.create(id=uuid.uuid4(), name=f"Org {_counter}", slug=f"org-{_counter}", owner=owner)


def _make_channel(partner, owner):
    global _counter
    _counter += 1
    conversation = Conversation.objects.create(
        type=ConversationType.CHANNEL, title=f"Channel {_counter}", description="", created_by=owner,
    )
    return Channel.objects.create(
        partner=partner, name=f"Channel {_counter}", slug=f"channel-{_counter}", owner=owner, conversation=conversation,
    )


class ReadWritePermissionSplitTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = _make_user("crud-owner@test.com")
        self.partner = _make_partner(self.owner)
        self.channel = _make_channel(self.partner, self.owner)
        self.task = Task.objects.create(
            partner=self.partner, channel=self.channel, title="Some task", description="", created_by=self.owner,
        )
        self.url = f"/control/admin/crud/tasks/Task/"

    def test_read_only_role_cannot_bulk_delete(self):
        reader = _make_user("crud-reader@test.com")
        _make_scoped_admin_role(reader, app_label="tasks", permissions=["crud.read"])
        self.client.force_authenticate(user=reader)

        get_resp = self.client.get(self.url)
        self.assertEqual(get_resp.status_code, status.HTTP_200_OK)

        post_resp = self.client.post(
            self.url, {"action": "soft_delete", "ids": [str(self.task.id)]}, format="json"
        )
        self.assertEqual(post_resp.status_code, status.HTTP_403_FORBIDDEN)
        self.task.refresh_from_db()
        self.assertFalse(self.task.is_deleted)

    def test_write_role_can_bulk_delete(self):
        writer = _make_user("crud-writer@test.com")
        _make_scoped_admin_role(writer, app_label="tasks", permissions=["crud.read", "crud.write"])
        self.client.force_authenticate(user=writer)

        post_resp = self.client.post(
            self.url, {"action": "soft_delete", "ids": [str(self.task.id)]}, format="json"
        )
        self.assertEqual(post_resp.status_code, status.HTTP_200_OK, post_resp.data)
        self.task.refresh_from_db()
        self.assertTrue(self.task.is_deleted)


class BulkActionPartnerScopingTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = _make_user("crud-owner2@test.com")
        self.partner_a = _make_partner(self.owner)
        self.partner_b = _make_partner(self.owner)
        self.channel_a = _make_channel(self.partner_a, self.owner)
        self.channel_b = _make_channel(self.partner_b, self.owner)
        self.task_a = Task.objects.create(
            partner=self.partner_a, channel=self.channel_a, title="Partner A task", description="", created_by=self.owner,
        )
        self.task_b = Task.objects.create(
            partner=self.partner_b, channel=self.channel_b, title="Partner B task", description="", created_by=self.owner,
        )
        self.writer = _make_user("crud-writer2@test.com")
        _make_scoped_admin_role(self.writer, app_label="tasks", permissions=["crud.read", "crud.write"])
        self.client.force_authenticate(user=self.writer)

    def test_partner_scoped_bulk_action_only_touches_that_partners_rows(self):
        resp = self.client.post(
            "/control/admin/crud/tasks/Task/",
            {
                "action": "soft_delete",
                "ids": [str(self.task_a.id), str(self.task_b.id)],
                "partner_id": str(self.partner_a.id),
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data["affected"], 1)
        self.task_a.refresh_from_db()
        self.task_b.refresh_from_db()
        self.assertTrue(self.task_a.is_deleted)
        self.assertFalse(self.task_b.is_deleted)  # different partner - untouched

    def test_without_partner_id_behaves_as_before_platform_wide(self):
        resp = self.client.post(
            "/control/admin/crud/tasks/Task/",
            {"action": "soft_delete", "ids": [str(self.task_a.id), str(self.task_b.id)]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data["affected"], 2)
