from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.broadcasts.models import BroadcastHealthInstitution, BroadcastHealthProfile
from apps.health_dashboard.models import HealthDashboardInstitution
from apps.health_ops.models import HealthInstitution

User = get_user_model()


def _create_user(phone: str, username: str):
    return User.objects.create_user(
        phone=phone,
        country="CM",
        password="pass1234",
        username=username,
        display_name=username.title(),
        phone_country_code="+237",
        phone_number=phone.replace("+237", ""),
    )


@override_settings(SECURE_SSL_REDIRECT=False)
class LegacyBroadcastInstitutionIdentityTests(APITestCase):
    """Three separate "health institution" representations exist in this
    codebase: apps.broadcasts' client-editable JSON blob (BroadcastHealthProfile
    .payload), its SQL projection (BroadcastHealthInstitution), and
    apps.health_dashboard's CMS layer (already a real FK onto the broadcast
    row). apps.health_ops.HealthInstitution — the clinically-authoritative
    model everything in this session's work anchors to — previously had only
    a string key (settings["legacy_institution_id"]) linking it to the
    other three, not a real relationship. This suite proves the new
    `legacy_broadcast_institution` FK keeps that identity consistent."""

    def setUp(self):
        self.client = APIClient()
        self.owner = _create_user("+237697000001", "legacy_owner")
        self.broadcast_profile = BroadcastHealthProfile.objects.create(
            profile=self.owner.profile,
            payload={
                "institutions": [
                    {
                        "id": "legacy-clinic-001",
                        "name": "Legacy Sunrise Clinic",
                        "type": "clinic",
                        "ownerContact": {"userId": str(self.owner.id)},
                    }
                ]
            },
        )
        self.broadcast_institution = BroadcastHealthInstitution.objects.create(
            health_profile=self.broadcast_profile,
            institution_uid="legacy-clinic-001",
            name="Legacy Sunrise Clinic",
            owner_user=self.owner,
        )

    def _detail_url(self, institution_id):
        return reverse("health-ops-institution-detail", kwargs={"institution_id": institution_id})

    def test_bootstrap_creates_health_institution_with_real_fk(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self._detail_url("legacy-clinic-001"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)

        institution = HealthInstitution.objects.get(id=resp.data["institution"]["id"])
        self.assertEqual(institution.legacy_broadcast_institution_id, self.broadcast_institution.id)
        self.assertEqual(institution.settings.get("legacy_institution_id"), "legacy-clinic-001")

    def test_bootstrap_is_idempotent_across_repeated_calls(self):
        self.client.force_authenticate(self.owner)
        first = self.client.get(self._detail_url("legacy-clinic-001"))
        second = self.client.get(self._detail_url("legacy-clinic-001"))
        self.assertEqual(first.data["institution"]["id"], second.data["institution"]["id"])
        self.assertEqual(
            HealthInstitution.objects.filter(legacy_broadcast_institution=self.broadcast_institution).count(), 1,
        )

    def test_fk_lookup_is_used_over_settings_key_when_both_present(self):
        self.client.force_authenticate(self.owner)
        self.client.get(self._detail_url("legacy-clinic-001"))
        institution = HealthInstitution.objects.get(legacy_broadcast_institution=self.broadcast_institution)

        # Deliberately corrupt the settings-key copy to simulate drift —
        # the FK must still be treated as the source of truth and a second
        # bootstrap call must not create a duplicate or get confused.
        institution.settings["legacy_institution_id"] = "stale-mismatched-id"
        institution.save(update_fields=["settings"])

        resp = self.client.get(self._detail_url("legacy-clinic-001"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["institution"]["id"], str(institution.id))
        self.assertEqual(HealthInstitution.objects.count(), 1)

    def test_legacy_fk_resolves_through_health_dashboard_chain(self):
        """Proves the full cross-app identity chain is walkable and
        consistent: health_ops.HealthInstitution <-> BroadcastHealthInstitution
        <-> HealthDashboardInstitution all agree on the same real-world
        institution."""
        dashboard_institution = HealthDashboardInstitution.objects.create(
            broadcast_institution=self.broadcast_institution,
            institution_uid=self.broadcast_institution.institution_uid,
            owner_user=self.owner,
            name="Legacy Sunrise Clinic",
        )
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self._detail_url("legacy-clinic-001"))
        health_ops_institution = HealthInstitution.objects.get(id=resp.data["institution"]["id"])

        self.assertEqual(
            health_ops_institution.legacy_broadcast_institution_id,
            dashboard_institution.broadcast_institution_id,
        )

    def test_backfill_migration_logic_matches_live_bootstrap_resolution(self):
        """Simulates a pre-migration row (FK null, only the settings key
        set) and confirms the same resolution helper used by migration
        0026's backfill would find the correct, unambiguous match."""
        from apps.health_ops.views import _find_legacy_health_institution

        pre_migration_institution = HealthInstitution.objects.create(
            owner=self.owner, name="Pre-migration row",
            settings={"legacy_institution_id": "legacy-clinic-001", "legacy_source": "broadcast_health_profile"},
        )
        resolved = _find_legacy_health_institution("legacy-clinic-001", self.broadcast_institution)
        self.assertEqual(resolved.id, pre_migration_institution.id)
