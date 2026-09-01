"""
Security incident tracking - previously no incident-response system of
any kind existed anywhere in the codebase. See SecurityIncident's
docstring for the honest scope of what this is (a record-keeping/workflow
tool) versus what it is NOT (a claim of regulatory compliance by itself).

Run:
  python3 manage.py test admin_control.test_incidents --keepdb -v 2
"""
from __future__ import annotations

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from admin_control.models import AdminAuditEntry, SecurityIncident
from admin_control.roles import AdminRole, AdminRoleAssignment, AdminRolePermission

User = get_user_model()

LIST_URL = "/control/admin/incidents/"
SUMMARY_URL = "/control/admin/incidents/summary/"


def _grant_role(user, *, permissions):
    role = AdminRole.objects.create(name=f"role-{user.id}")
    AdminRolePermission.objects.create(role=role, app_label="*", permissions=permissions)
    AdminRoleAssignment.objects.create(user=user, role=role, is_active=True)


def _detail_url(incident_id):
    return f"{LIST_URL}{incident_id}/"


class IncidentAccessControlTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_a_staff_user_with_no_admin_control_role_cannot_create_an_incident(self):
        staff = User.objects.create_user(
            phone="+2349000000001", password="pw123456", country="NG", is_staff=True,
        )
        self.client.force_authenticate(staff)

        res = self.client.post(
            LIST_URL,
            {"title": "Suspicious login pattern", "discovered_at": timezone.now().isoformat()},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_user_with_incidents_manage_permission_can_create_an_incident(self):
        admin = User.objects.create_user(phone="+2349000000002", password="pw123456", country="NG")
        _grant_role(admin, permissions=["incidents.manage"])
        self.client.force_authenticate(admin)

        res = self.client.post(
            LIST_URL,
            {"title": "Suspicious login pattern", "discovered_at": timezone.now().isoformat(), "severity": "high"},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(SecurityIncident.objects.count(), 1)
        incident = SecurityIncident.objects.get()
        self.assertEqual(incident.reported_by_id, admin.id)
        self.assertEqual(incident.status, SecurityIncident.Status.OPEN)

    def test_a_role_scoped_to_an_unrelated_permission_cannot_create_an_incident(self):
        admin = User.objects.create_user(phone="+2349000000003", password="pw123456", country="NG")
        _grant_role(admin, permissions=["content.moderate"])
        self.client.force_authenticate(admin)

        res = self.client.post(
            LIST_URL,
            {"title": "Test", "discovered_at": timezone.now().isoformat()},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)


class IncidentWorkflowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(phone="+2349000000004", password="pw123456", country="NG")
        _grant_role(self.admin, permissions=["incidents.manage"])
        self.client.force_authenticate(self.admin)

    def test_creating_an_incident_writes_an_admin_audit_entry(self):
        self.client.post(
            LIST_URL,
            {"title": "Data export anomaly", "discovered_at": timezone.now().isoformat()},
            format="json",
        )

        entry = AdminAuditEntry.objects.filter(action_type="incident.reported").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor_id, self.admin.id)

    def test_transitioning_to_resolved_stamps_resolved_at(self):
        create_res = self.client.post(
            LIST_URL,
            {"title": "Test", "discovered_at": timezone.now().isoformat()},
            format="json",
        )
        incident_id = create_res.data["id"]

        res = self.client.patch(
            _detail_url(incident_id),
            {"status": "resolved", "resolution_summary": "False alarm."},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        incident = SecurityIncident.objects.get(id=incident_id)
        self.assertIsNotNone(incident.resolved_at)

    def test_updating_an_already_resolved_incident_does_not_move_resolved_at(self):
        incident = SecurityIncident.objects.create(
            title="Test",
            discovered_at=timezone.now(),
            status=SecurityIncident.Status.RESOLVED,
            resolved_at=timezone.now() - datetime.timedelta(days=5),
        )
        original_resolved_at = incident.resolved_at

        self.client.patch(_detail_url(incident.id), {"description": "adding more detail"}, format="json")

        incident.refresh_from_db()
        self.assertEqual(incident.resolved_at, original_resolved_at)

    def test_updating_an_unrelated_field_does_not_change_status(self):
        incident = SecurityIncident.objects.create(title="Test", discovered_at=timezone.now())

        self.client.patch(_detail_url(incident.id), {"severity": "critical"}, format="json")

        incident.refresh_from_db()
        self.assertEqual(incident.status, SecurityIncident.Status.OPEN)
        self.assertEqual(incident.severity, SecurityIncident.Severity.CRITICAL)

    def test_list_filters_by_status(self):
        SecurityIncident.objects.create(title="Open one", discovered_at=timezone.now(), status="open")
        SecurityIncident.objects.create(title="Closed one", discovered_at=timezone.now(), status="closed")

        res = self.client.get(LIST_URL, {"status": "open"})

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        titles = [i["title"] for i in res.data["incidents"]]
        self.assertIn("Open one", titles)
        self.assertNotIn("Closed one", titles)

    def test_detail_404_for_nonexistent_incident(self):
        res = self.client.get(_detail_url("00000000-0000-0000-0000-000000000000"))

        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)


class IncidentSummaryTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(phone="+2349000000005", password="pw123456", country="NG")
        _grant_role(self.admin, permissions=["incidents.manage"])
        self.client.force_authenticate(self.admin)

    def test_counts_incidents_pending_a_notification_decision(self):
        SecurityIncident.objects.create(
            title="Undetermined", discovered_at=timezone.now(),
            status="open", regulatory_notification_required=None,
        )
        SecurityIncident.objects.create(
            title="Decided", discovered_at=timezone.now(),
            status="open", regulatory_notification_required=False,
        )

        res = self.client.get(SUMMARY_URL)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["pending_notification_decision"], 1)

    def test_counts_notifications_owed_but_not_yet_sent(self):
        SecurityIncident.objects.create(
            title="Owed", discovered_at=timezone.now(),
            regulatory_notification_required=True, regulatory_notification_sent_at=None,
        )
        SecurityIncident.objects.create(
            title="Already sent", discovered_at=timezone.now(),
            regulatory_notification_required=True, regulatory_notification_sent_at=timezone.now(),
        )

        res = self.client.get(SUMMARY_URL)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["notification_owed_not_sent"], 1)
