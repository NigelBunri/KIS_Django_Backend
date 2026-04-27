from django.test import TestCase
from django.test import override_settings
from django.contrib.contenttypes.models import ContentType
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core import models
from apps.broadcasts.models import BroadcastHealthProfile, BroadcastHealthInstitution, BroadcastHealthInstitutionMember
from apps.core.money import (
    frontend_kisc_major_to_micro,
    frontend_kisc_major_to_usd_cents,
    parse_frontend_money_to_cents,
)
from common.media_urls import absolutize_backend_media, strip_backend_origin


class CommunityPermissionHelperTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone="+237670000001",
            password="StrongPass123",
            country="CM",
            email="core-tests@example.com",
        )
        self.community = models.Community.objects.create(
            slug="core-permission-tests",
            name="Core Permission Tests",
        )
        self.user_ct = ContentType.objects.get_for_model(User)
        self.community_ct = ContentType.objects.get_for_model(models.Community)
        self.permission = "community.manage"

    def _add_ace(self, *, effect: str, permissions: list[str]):
        return models.AccessControlEntry.objects.create(
            principal_content_type=self.user_ct,
            principal_object_id=str(self.user.id),
            target_content_type=self.community_ct,
            target_object_id=str(self.community.id),
            permissions=permissions,
            effect=effect,
        )

    def test_can_user_on_community_without_matching_aces_returns_false(self):
        allowed = models.CommunityPermissionHelper.can_user_on_community(
            self.user,
            self.community,
            self.permission,
        )
        self.assertFalse(allowed)

    def test_can_user_on_community_with_allow_ace_returns_true(self):
        self._add_ace(effect=models.AccessControlEntry.EFFECT_ALLOW, permissions=[self.permission])

        allowed = models.CommunityPermissionHelper.can_user_on_community(
            self.user,
            self.community,
            self.permission,
        )
        self.assertTrue(allowed)

    def test_can_user_on_community_deny_ace_overrides_allow(self):
        self._add_ace(effect=models.AccessControlEntry.EFFECT_ALLOW, permissions=[self.permission])
        self._add_ace(effect=models.AccessControlEntry.EFFECT_DENY, permissions=[self.permission])

        allowed = models.CommunityPermissionHelper.can_user_on_community(
            self.user,
            self.community,
            self.permission,
        )
        self.assertFalse(allowed)


class FrontendMoneyNormalizationTests(TestCase):
    def test_frontend_kisc_major_to_usd_cents_scales_by_ten_thousand(self):
        self.assertEqual(frontend_kisc_major_to_usd_cents("100"), 1_000_000)

    def test_frontend_kisc_major_to_micro_scales_by_one_hundred_thousand(self):
        self.assertEqual(frontend_kisc_major_to_micro("100"), 10_000_000)

    def test_parse_frontend_money_to_cents_keeps_cents_unchanged(self):
        self.assertEqual(parse_frontend_money_to_cents({"amount_cents": 1250}), 1250)

    def test_parse_frontend_money_to_cents_normalizes_major_unit_kisc(self):
        self.assertEqual(parse_frontend_money_to_cents({"amount_kisc": "100"}), 1_000_000)


class BackendMediaUrlNormalizationTests(TestCase):
    def test_strips_backend_origin_before_save(self):
        self.assertEqual(
            strip_backend_origin("http://10.112.162.99:8000/media/institutions/logo.png"),
            "/media/institutions/logo.png",
        )

    def test_keeps_external_image_url_unchanged(self):
        self.assertEqual(
            strip_backend_origin("https://cdn.example.com/media/logo.png"),
            "https://cdn.example.com/media/logo.png",
        )

    def test_absolutizes_existing_backend_url_against_current_request(self):
        request = self.client.get("/").wsgi_request
        with override_settings(API_BASE_URL="http://10.112.162.99:8000", SITE_URL="http://10.112.162.99:8000"):
            self.assertEqual(
                absolutize_backend_media("http://10.112.162.99:8000/media/institutions/logo.png", request=request),
                "http://10.112.162.99:8000/media/institutions/logo.png",
            )


class PatientCanonicalHealthProfileTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            phone="+237670000002",
            password="StrongPass123",
            country="CM",
            email="health-profile-tests@example.com",
        )
        self.client.force_authenticate(self.user)
        self.other_user = User.objects.create_user(
            phone="+237670000004",
            password="StrongPass123",
            country="CM",
            email="health-caregiver@example.com",
        )
        self.other_client = APIClient()
        self.other_client.force_authenticate(self.other_user)
        self.organization = models.HealthcareOrganization.objects.create(
            name="KIS Test Clinic",
            slug="kis-test-clinic",
        )
        self.patient = models.PatientMasterRecord.objects.create(
            mrn="KIS-HP-001",
            first_name="Nigel",
            last_name="Tester",
            gender=models.PatientMasterRecord.GENDER_MALE,
            primary_contact={
                "user_id": str(self.user.id),
                "email": self.user.email,
                "phone": self.user.phone,
            },
            emergency_contact={"name": "Emergency Contact", "phone": "+237699000000"},
            metadata={"blood_type": "O+"},
            organization=self.organization,
        )
        models.AllergyRecord.objects.create(
            patient=self.patient,
            agent="Peanuts",
            severity=models.AllergyRecord.SEVERITY_SEVERE,
            status=models.AllergyRecord.STATUS_ACTIVE,
        )
        models.MedicationOrder.objects.create(
            patient=self.patient,
            drug_name="Amoxicillin",
            status=models.MedicationOrder.STATUS_ACTIVE,
        )
        models.VitalSign.objects.create(
            patient=self.patient,
            vital_type=models.VitalSign.TYPE_TEMPERATURE,
            value="37.2",
            units="C",
        )
        models.WellnessMetric.objects.create(
            patient=self.patient,
            metric_type=models.WellnessMetric.METRIC_STEPS,
            source=models.WellnessMetric.SOURCE_APPLE_HEALTH,
            measurement_window=models.WellnessMetric.WINDOW_DAILY,
            value="6400",
            units="count",
            normalized_value="6400",
            normalized_units="count",
        )
        models.WellnessMetric.objects.create(
            patient=self.patient,
            metric_type=models.WellnessMetric.METRIC_WEIGHT,
            source=models.WellnessMetric.SOURCE_MANUAL,
            measurement_window=models.WellnessMetric.WINDOW_INSTANT,
            value="78.4",
            units="kg",
            normalized_value="78.4",
            normalized_units="kg",
        )
        models.ProblemRecord.objects.create(
            patient=self.patient,
            title="Asthma",
            clinical_status=models.ProblemRecord.STATUS_ACTIVE,
            severity=models.ProblemRecord.SEVERITY_MEDIUM,
        )
        models.ImmunizationRecord.objects.create(
            patient=self.patient,
            vaccine_name="Tetanus",
            status=models.ImmunizationRecord.STATUS_COMPLETED,
        )
        models.ProcedureRecord.objects.create(
            patient=self.patient,
            procedure_name="Appendectomy",
            status=models.ProcedureRecord.STATUS_COMPLETED,
        )
        models.HealthDocument.objects.create(
            patient=self.patient,
            title="Discharge Summary",
            category=models.HealthDocument.CATEGORY_DISCHARGE,
            file_url="https://example.com/discharge-summary.pdf",
        )
        health_profile = BroadcastHealthProfile.objects.create(profile=self.user.profile, payload={})
        institution = BroadcastHealthInstitution.objects.create(
            health_profile=health_profile,
            institution_uid="inst-001",
            name="KIS Prime Hospital",
            owner_user=self.user,
        )
        BroadcastHealthInstitutionMember.objects.create(
            institution=institution,
            member_uid="member-001",
            name="Nigel Tester",
            role="owner",
            user=self.user,
        )

    def test_my_health_profile_returns_canonical_payload(self):
        response = self.client.get("/api/v1/patients/master/my-health-profile/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(str(self.patient.id), payload["patient_id"])
        self.assertEqual(str(self.user.id), payload["linked_user_id"])
        self.assertEqual("Nigel Tester", payload["identity"]["full_name"])
        self.assertEqual("O+", payload["emergency"]["blood_type"])
        self.assertEqual(1, payload["care_summary"]["active_allergies_count"])
        self.assertEqual(1, payload["care_summary"]["active_medications_count"])
        self.assertEqual(1, payload["affiliations"]["total_institutions"])
        self.assertEqual("KIS Prime Hospital", payload["affiliations"]["owned_institutions"][0]["name"])

    def test_my_health_profile_returns_not_found_when_user_is_not_linked(self):
        other_user = User.objects.create_user(
            phone="+237670000003",
            password="StrongPass123",
            country="CM",
            email="health-profile-missing@example.com",
        )
        self.client.force_authenticate(other_user)

        response = self.client.get("/api/v1/patients/master/my-health-profile/")

        self.assertEqual(response.status_code, 404)
        self.assertEqual("patient_profile_not_linked", response.json()["code"])

    def test_legacy_broadcast_health_profile_write_syncs_core_patient_fields(self):
        response = self.client.post(
            "/api/v1/broadcasts/profiles/manage/",
            {
                "profile_type": "health_profile",
                "updates": {
                    "profile_name": "Nigel Health",
                    "identity": {
                        "first_name": "Nigel",
                        "last_name": "Updated",
                        "dob": "1998-06-20",
                        "gender": models.PatientMasterRecord.GENDER_MALE,
                    },
                    "emergency": {
                        "blood_type": "A+",
                        "medical_notes": "Carries inhaler",
                        "emergency_contact": {
                            "name": "Updated Emergency",
                            "phone": "+237688111222",
                        },
                    },
                    "primary_contact": {
                        "user_id": str(self.user.id),
                        "email": self.user.email,
                        "phone": self.user.phone,
                    },
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.patient.refresh_from_db()
        self.assertEqual("Updated", self.patient.last_name)
        self.assertEqual("1998-06-20", self.patient.dob.isoformat())
        self.assertEqual("A+", self.patient.metadata.get("blood_type"))
        self.assertEqual("Carries inhaler", self.patient.metadata.get("medical_notes"))
        self.assertEqual("Updated Emergency", self.patient.emergency_contact.get("name"))

        canonical = self.client.get("/api/v1/patients/master/my-health-profile/")
        self.assertEqual(canonical.status_code, 200)
        payload = canonical.json()
        self.assertEqual("Nigel Updated", payload["identity"]["full_name"])
        self.assertEqual("A+", payload["emergency"]["blood_type"])
        self.assertEqual("Carries inhaler", payload["emergency"]["medical_notes"])

    def test_health_summary_endpoint_returns_patient_facing_summary(self):
        response = self.client.get(f"/api/v1/patients/master/{self.patient.id}/health-summary/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(str(self.patient.id), payload["patient_id"])
        self.assertEqual("Nigel Tester", payload["identity"]["full_name"])
        self.assertEqual(1, payload["care_summary"]["active_medications_count"])
        self.assertEqual(1, len(payload["top_allergies"]))
        self.assertEqual(1, len(payload["problems"]))
        self.assertEqual(1, len(payload["immunizations"]))
        self.assertEqual(1, len(payload["procedures"]))
        self.assertEqual(1, len(payload["documents"]))
        self.assertIn("steps", payload["wellness"]["trends"])
        self.assertEqual("6400.0000", payload["wellness"]["trends"]["steps"]["latest"]["value"])

    def test_emergency_card_endpoint_returns_emergency_snapshot(self):
        response = self.client.get(f"/api/v1/patients/master/{self.patient.id}/emergency-card/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual("Nigel Tester", payload["identity"]["full_name"])
        self.assertEqual("O+", payload["emergency"]["blood_type"])
        self.assertEqual("Emergency Contact", payload["emergency"]["emergency_contact"]["name"])
        self.assertEqual(1, len(payload["severe_allergies"]))

    def test_problem_record_endpoint_creates_problem(self):
        response = self.client.post(
            "/api/v1/patients/problems/",
            {
                "patient": str(self.patient.id),
                "title": "Hypertension",
                "clinical_status": "active",
                "verification_status": "confirmed",
                "severity": "high",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.user.id, models.ProblemRecord.objects.filter(title="Hypertension").first().diagnosed_by_id)

    def test_wellness_metric_endpoint_normalizes_weight_from_pounds(self):
        response = self.client.post(
            "/api/v1/patients/wellness-metrics/",
            {
                "patient": str(self.patient.id),
                "metric_type": "weight",
                "source": "manual",
                "measurement_window": "instant",
                "value": "220",
                "units": "lb",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        metric = models.WellnessMetric.objects.get(id=response.json()["id"])
        self.assertEqual("kg", metric.normalized_units)
        self.assertGreater(float(metric.normalized_value), 99.0)

    def test_health_summary_denies_unrelated_user_without_access_grant(self):
        response = self.other_client.get(f"/api/v1/patients/master/{self.patient.id}/health-summary/")

        self.assertEqual(response.status_code, 403)

    def test_health_summary_allows_active_delegate_with_grant(self):
        models.HealthDataAccessGrant.objects.create(
            patient=self.patient,
            granted_to=self.other_user,
            granted_by=self.user,
            role=models.HealthDataAccessGrant.ROLE_CAREGIVER,
            scope=models.HealthDataAccessGrant.SCOPE_SUMMARY,
            status=models.HealthDataAccessGrant.STATUS_ACTIVE,
            allow_emergency_override=True,
        )

        response = self.other_client.get(f"/api/v1/patients/master/{self.patient.id}/health-summary/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(self.patient.id), response.json()["patient_id"])
        self.assertTrue(
            models.ComplianceAuditLog.objects.filter(
                action="patient.health_summary.read",
                target_id=str(self.patient.id),
            ).exists()
        )

    def test_export_bundle_returns_fhir_collection(self):
        response = self.client.get(f"/api/v1/patients/master/{self.patient.id}/export-bundle/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual("Bundle", payload["resourceType"])
        self.assertEqual("collection", payload["type"])
        resource_types = [entry["resource"]["resourceType"] for entry in payload["entry"]]
        self.assertIn("Patient", resource_types)
        self.assertIn("Condition", resource_types)
        self.assertIn("Immunization", resource_types)
        self.assertTrue(models.HealthRecordExchangeLog.objects.filter(patient=self.patient, direction="export").exists())

    def test_import_bundle_creates_records_and_log(self):
        response = self.client.post(
            f"/api/v1/patients/master/{self.patient.id}/import-bundle/",
            {
                "source_label": "test-provider",
                "bundle": {
                    "resourceType": "Bundle",
                    "entry": [
                        {
                            "resource": {
                                "resourceType": "Condition",
                                "code": {"text": "Diabetes"},
                                "clinicalStatus": {"text": "active"},
                                "verificationStatus": {"text": "confirmed"},
                                "severity": {"text": "high"},
                            }
                        },
                        {
                            "resource": {
                                "resourceType": "DocumentReference",
                                "description": "Imported Lab Result",
                                "type": {"text": "lab"},
                                "content": [
                                    {
                                        "attachment": {
                                            "url": "https://example.com/lab-result.pdf",
                                            "contentType": "application/pdf",
                                            "title": "Imported Lab Result",
                                        }
                                    }
                                ],
                            }
                        },
                    ],
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(models.ProblemRecord.objects.filter(patient=self.patient, title="Diabetes").exists())
        self.assertTrue(models.HealthDocument.objects.filter(patient=self.patient, title="Imported Lab Result").exists())
        log = models.HealthRecordExchangeLog.objects.filter(patient=self.patient, direction="import").latest("created_at")
        self.assertEqual("test-provider", log.source_label)
