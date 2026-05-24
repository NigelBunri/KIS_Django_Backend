"""
Tests for partner geolocation / attendance.

Covers:
- Creating location events (admin only)
- Role/permission checks
- Consent required before check-in
- Geofence boundary enforcement
- Duplicate check-in prevention (idempotency)
- Atomic arrival number assignment
- Admin vs member visibility rules
- Manual admin check-in
- Consent grant/revoke
- Audit log creation
"""
from __future__ import annotations

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.partners.models import Partner, PartnerMembership
from apps.location.models import (
    PartnerLocationEvent,
    PartnerLocationAttendance,
    PartnerLocationConsent,
    PartnerLocationAuditLog,
    EventStatus,
    AuditAction,
)

_counter = 0


def _make_user(email, *, is_superuser=False, is_staff=False, **kw):
    global _counter
    _counter += 1
    phone = f"+2376540{_counter:05d}"
    if is_superuser:
        user = User.objects.create_superuser(
            email=email, password="test1234!", phone=phone, country="CM",
            is_staff=True, is_superuser=True, **kw,
        )
    else:
        user = User.objects.create_user(
            phone=phone, email=email, password="test1234!", country="CM", **kw,
        )
    return user


def _make_partner(slug, owner):
    return Partner.objects.create(slug=slug, name=slug.upper(), owner=owner, is_active=True)


def _make_membership(partner, user, role="member"):
    PartnerMembership.objects.update_or_create(
        partner=partner, user=user,
        defaults={"status": "member", "role": role},
    )


def _make_event(partner, creator, **kw) -> PartnerLocationEvent:
    from django.utils import timezone
    from datetime import timedelta
    now = timezone.now()
    defaults = dict(
        title="Sunday Service",
        start_dt=now - timedelta(minutes=5),
        end_dt=now + timedelta(hours=2),
        center_lat="3.848000",
        center_lng="11.502000",
        radius_meters=100,
        status=EventStatus.ACTIVE,
        checkin_opens_before_minutes=15,
    )
    defaults.update(kw)
    return PartnerLocationEvent.objects.create(partner=partner, created_by=creator, **defaults)


def _grant_consent(user, partner):
    from django.utils import timezone
    consent, _ = PartnerLocationConsent.objects.get_or_create(user=user, partner=partner)
    consent.granted = True
    consent.granted_at = timezone.now()
    consent.save()
    return consent


# ─────────────────────────────────────────────────────────────────────────────

class EventPermissionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = _make_user("owner@test.com")
        self.partner = _make_partner("testchurch", self.owner)
        _make_membership(self.partner, self.owner, role="admin")
        self.member = _make_user("member@test.com")
        _make_membership(self.partner, self.member)
        self.stranger = _make_user("stranger@test.com")

    def _create_event_payload(self):
        from django.utils import timezone
        from datetime import timedelta
        now = timezone.now()
        return {
            "title": "Morning Prayer",
            "start_dt": (now + timedelta(hours=1)).isoformat(),
            "end_dt": (now + timedelta(hours=3)).isoformat(),
            "center_lat": "3.848000",
            "center_lng": "11.502000",
            "radius_meters": 50,
        }

    def test_admin_can_create_event(self):
        self.client.force_authenticate(user=self.owner)
        resp = self.client.post(
            f"/api/v1/partners/{self.partner.id}/location/events/",
            self._create_event_payload(),
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIn("event", resp.data)

    def test_member_cannot_create_event(self):
        self.client.force_authenticate(user=self.member)
        resp = self.client.post(
            f"/api/v1/partners/{self.partner.id}/location/events/",
            self._create_event_payload(),
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_stranger_cannot_create_event(self):
        self.client.force_authenticate(user=self.stranger)
        resp = self.client.post(
            f"/api/v1/partners/{self.partner.id}/location/events/",
            self._create_event_payload(),
            format="json",
        )
        self.assertIn(resp.status_code, [status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND])

    def test_unauthenticated_denied(self):
        resp = self.client.get(f"/api/v1/partners/{self.partner.id}/location/events/")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_member_can_list_active_events(self):
        _make_event(self.partner, self.owner)
        self.client.force_authenticate(user=self.member)
        resp = self.client.get(f"/api/v1/partners/{self.partner.id}/location/events/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(resp.data["events"]), 1)

    def test_member_cannot_see_draft_events(self):
        _make_event(self.partner, self.owner, status=EventStatus.DRAFT)
        self.client.force_authenticate(user=self.member)
        resp = self.client.get(f"/api/v1/partners/{self.partner.id}/location/events/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data["events"]), 0)

    def test_admin_can_see_draft_events(self):
        _make_event(self.partner, self.owner, status=EventStatus.DRAFT)
        self.client.force_authenticate(user=self.owner)
        resp = self.client.get(f"/api/v1/partners/{self.partner.id}/location/events/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data["events"]), 1)


class ConsentTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = _make_user("owner@consent.com")
        self.partner = _make_partner("testorg", self.owner)
        _make_membership(self.partner, self.owner, role="admin")
        self.member = _make_user("member@consent.com")
        _make_membership(self.partner, self.member)

    def test_member_can_get_consent_record(self):
        self.client.force_authenticate(user=self.member)
        resp = self.client.get(f"/api/v1/partners/{self.partner.id}/location/consent/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data["consent"]["granted"])

    def test_member_can_grant_consent(self):
        self.client.force_authenticate(user=self.member)
        resp = self.client.post(
            f"/api/v1/partners/{self.partner.id}/location/consent/",
            {"granted": True},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["consent"]["granted"])

    def test_member_can_revoke_consent(self):
        _grant_consent(self.member, self.partner)
        self.client.force_authenticate(user=self.member)
        resp = self.client.post(
            f"/api/v1/partners/{self.partner.id}/location/consent/",
            {"granted": False},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data["consent"]["granted"])
        # Audit log
        self.assertTrue(PartnerLocationAuditLog.objects.filter(
            action=AuditAction.CONSENT_REVOKED, actor=self.member
        ).exists())


class CheckinTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = _make_user("owner@checkin.com")
        self.partner = _make_partner("testmin", self.owner)
        _make_membership(self.partner, self.owner, role="admin")
        self.member = _make_user("member@checkin.com")
        _make_membership(self.partner, self.member)
        # Event center: ~3.848, ~11.502 — member checks in from 20 m away
        self.event = _make_event(self.partner, self.owner)

    def test_checkin_requires_consent(self):
        self.client.force_authenticate(user=self.member)
        resp = self.client.post(
            f"/api/v1/partners/{self.partner.id}/location/events/{self.event.id}/checkin/",
            {"lat": 3.848100, "lng": 11.502050},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("consent", resp.data["detail"].lower())

    def test_checkin_inside_geofence_succeeds(self):
        _grant_consent(self.member, self.partner)
        self.client.force_authenticate(user=self.member)
        # 20 m from center — well within 100 m radius
        resp = self.client.post(
            f"/api/v1/partners/{self.partner.id}/location/events/{self.event.id}/checkin/",
            {"lat": 3.848180, "lng": 11.502000},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        att = resp.data["attendance"]
        self.assertEqual(att["arrival_number"], 1)

    def test_checkin_outside_geofence_rejected(self):
        _grant_consent(self.member, self.partner)
        self.client.force_authenticate(user=self.member)
        # ~2 km away
        resp = self.client.post(
            f"/api/v1/partners/{self.partner.id}/location/events/{self.event.id}/checkin/",
            {"lat": 3.870000, "lng": 11.502000},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("outside", resp.data["detail"].lower())

    def test_checkin_duplicate_is_idempotent(self):
        _grant_consent(self.member, self.partner)
        self.client.force_authenticate(user=self.member)
        resp1 = self.client.post(
            f"/api/v1/partners/{self.partner.id}/location/events/{self.event.id}/checkin/",
            {"lat": 3.848180, "lng": 11.502000},
            format="json",
        )
        self.assertEqual(resp1.status_code, status.HTTP_201_CREATED)
        resp2 = self.client.post(
            f"/api/v1/partners/{self.partner.id}/location/events/{self.event.id}/checkin/",
            {"lat": 3.848180, "lng": 11.502000},
            format="json",
        )
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)
        self.assertTrue(resp2.data["already_checked_in"])
        # Only one attendance record
        self.assertEqual(PartnerLocationAttendance.objects.filter(event=self.event, user=self.member).count(), 1)

    def test_checkin_closed_event_rejected(self):
        _grant_consent(self.member, self.partner)
        self.event.status = EventStatus.CLOSED
        self.event.save()
        self.client.force_authenticate(user=self.member)
        resp = self.client.post(
            f"/api/v1/partners/{self.partner.id}/location/events/{self.event.id}/checkin/",
            {"lat": 3.848180, "lng": 11.502000},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_missing_lat_lng_rejected(self):
        _grant_consent(self.member, self.partner)
        self.client.force_authenticate(user=self.member)
        resp = self.client.post(
            f"/api/v1/partners/{self.partner.id}/location/events/{self.event.id}/checkin/",
            {},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class AtomicArrivalNumberTests(TestCase):
    def setUp(self):
        self.owner = _make_user("owner@atomic.com")
        self.partner = _make_partner("atomictest", self.owner)
        _make_membership(self.partner, self.owner, role="admin")
        self.event = _make_event(self.partner, self.owner)

    def test_arrival_numbers_are_sequential(self):
        users = []
        for i in range(5):
            u = _make_user(f"user{i}@atomic.com")
            _make_membership(self.partner, u)
            _grant_consent(u, self.partner)
            users.append(u)

        client = APIClient()
        for i, u in enumerate(users):
            client.force_authenticate(user=u)
            resp = client.post(
                f"/api/v1/partners/{self.partner.id}/location/events/{self.event.id}/checkin/",
                {"lat": 3.848180, "lng": 11.502000},
                format="json",
            )
            self.assertEqual(resp.status_code, status.HTTP_201_CREATED, f"User {i} checkin failed")

        numbers = list(
            PartnerLocationAttendance.objects.filter(event=self.event)
            .order_by("arrival_number")
            .values_list("arrival_number", flat=True)
        )
        self.assertEqual(numbers, list(range(1, 6)))

    def test_no_duplicate_arrival_numbers(self):
        u = _make_user("solo@atomic.com")
        _make_membership(self.partner, u)
        _grant_consent(u, self.partner)
        client = APIClient()
        client.force_authenticate(user=u)
        client.post(
            f"/api/v1/partners/{self.partner.id}/location/events/{self.event.id}/checkin/",
            {"lat": 3.848180, "lng": 11.502000},
            format="json",
        )
        numbers = list(
            PartnerLocationAttendance.objects.filter(event=self.event)
            .values_list("arrival_number", flat=True)
        )
        self.assertEqual(len(numbers), len(set(numbers)))


class AdminAttendanceViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = _make_user("owner@admin.com")
        self.partner = _make_partner("adminpartner", self.owner)
        _make_membership(self.partner, self.owner, role="admin")
        self.member = _make_user("member@admin.com")
        _make_membership(self.partner, self.member)
        self.event = _make_event(self.partner, self.owner)
        _grant_consent(self.member, self.partner)
        # Check member in
        PartnerLocationAttendance.objects.create(
            event=self.event, user=self.member, partner=self.partner,
            arrival_number=1, distance_from_center_m=20,
        )

    def test_admin_can_view_attendance(self):
        self.client.force_authenticate(user=self.owner)
        resp = self.client.get(
            f"/api/v1/partners/{self.partner.id}/location/events/{self.event.id}/attendance/"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data["attendance"]), 1)
        self.assertEqual(resp.data["summary"]["total_checked_in"], 1)

    def test_member_cannot_view_full_attendance(self):
        self.client.force_authenticate(user=self.member)
        resp = self.client.get(
            f"/api/v1/partners/{self.partner.id}/location/events/{self.event.id}/attendance/"
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_my_status_returns_own_attendance(self):
        self.client.force_authenticate(user=self.member)
        resp = self.client.get(
            f"/api/v1/partners/{self.partner.id}/location/events/{self.event.id}/my-status/"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["checked_in"])
        self.assertEqual(resp.data["attendance"]["arrival_number"], 1)

    def test_arrival_order_hidden_from_members_by_default(self):
        self.client.force_authenticate(user=self.member)
        resp = self.client.get(
            f"/api/v1/partners/{self.partner.id}/location/events/{self.event.id}/my-status/"
        )
        self.assertIsNone(resp.data["arrival_list"])

    def test_arrival_order_visible_when_enabled(self):
        self.event.show_arrival_order_to_members = True
        self.event.save()
        self.client.force_authenticate(user=self.member)
        resp = self.client.get(
            f"/api/v1/partners/{self.partner.id}/location/events/{self.event.id}/my-status/"
        )
        self.assertIsNotNone(resp.data["arrival_list"])

    def test_admin_manual_checkin(self):
        new_member = _make_user("new@admin.com")
        _make_membership(self.partner, new_member)
        self.client.force_authenticate(user=self.owner)
        resp = self.client.post(
            f"/api/v1/partners/{self.partner.id}/location/events/{self.event.id}/attendance/manual-checkin/",
            {"user_id": str(new_member.id)},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(resp.data["attendance"]["is_manual"])
        self.assertEqual(resp.data["attendance"]["arrival_number"], 2)


class AuditLogTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = _make_user("owner@audit.com")
        self.partner = _make_partner("auditpartner", self.owner)
        _make_membership(self.partner, self.owner, role="admin")

    def test_audit_log_created_on_event_creation(self):
        from django.utils import timezone
        from datetime import timedelta
        now = timezone.now()
        self.client.force_authenticate(user=self.owner)
        self.client.post(
            f"/api/v1/partners/{self.partner.id}/location/events/",
            {
                "title": "Audit Test Event",
                "start_dt": (now + timedelta(hours=1)).isoformat(),
                "end_dt": (now + timedelta(hours=3)).isoformat(),
                "center_lat": "3.848000",
                "center_lng": "11.502000",
                "radius_meters": 50,
            },
            format="json",
        )
        self.assertTrue(PartnerLocationAuditLog.objects.filter(
            partner=self.partner, action=AuditAction.EVENT_CREATED
        ).exists())

    def test_admin_can_view_audit_log(self):
        PartnerLocationAuditLog.objects.create(
            partner=self.partner, actor=self.owner, action=AuditAction.EVENT_CREATED, metadata={}
        )
        self.client.force_authenticate(user=self.owner)
        resp = self.client.get(f"/api/v1/partners/{self.partner.id}/location/audit/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(resp.data["logs"]), 1)


class TargetScopeTests(TestCase):
    """Verify that check-in enforces target_type / target_roles / target_user_ids."""

    def setUp(self):
        self.client = APIClient()
        self.owner = _make_user("owner@scope.com")
        self.partner = _make_partner("scopepartner", self.owner)
        _make_membership(self.partner, self.owner, role="admin")

        # A manager-role member
        self.manager = _make_user("manager@scope.com")
        _make_membership(self.partner, self.manager, role="manager")
        _grant_consent(self.manager, self.partner)

        # A regular member
        self.member = _make_user("member@scope.com")
        _make_membership(self.partner, self.member, role="member")
        _grant_consent(self.member, self.partner)

    def _make_scoped_event(self, **kw):
        return _make_event(self.partner, self.owner, **kw)

    def _checkin(self, user, event):
        self.client.force_authenticate(user=user)
        return self.client.post(
            f"/api/v1/partners/{self.partner.id}/location/events/{event.id}/checkin/",
            {"lat": 3.848180, "lng": 11.502000},
            format="json",
        )

    def test_all_scope_allows_any_member(self):
        event = self._make_scoped_event(target_type="all")
        resp = self._checkin(self.member, event)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_roles_scope_allows_matching_role(self):
        event = self._make_scoped_event(target_type="roles", target_roles=["manager", "admin"])
        resp = self._checkin(self.manager, event)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_roles_scope_blocks_non_matching_role(self):
        event = self._make_scoped_event(target_type="roles", target_roles=["manager", "admin"])
        resp = self._checkin(self.member, event)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_users_scope_allows_listed_user(self):
        event = self._make_scoped_event(target_type="users", target_user_ids=[str(self.member.id)])
        resp = self._checkin(self.member, event)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_users_scope_blocks_unlisted_user(self):
        event = self._make_scoped_event(target_type="users", target_user_ids=[str(self.owner.id)])
        resp = self._checkin(self.member, event)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
