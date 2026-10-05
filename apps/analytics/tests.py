from django.test import TestCase
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from unittest.mock import patch
from datetime import date

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.core.models import HealthcareOrganization, MedicalProfile
from .models import ClinicalAnalyticsReport, Metric


class AnalyticsSmokeTest(TestCase):
    def test_metric_and_prediction(self):
        with patch("apps.analytics.signals.compute_predictive_metrics.delay") as mocked_delay:
            m = Metric.objects.create(kind="system", name="test", value=10.0)
        self.assertIsNotNone(m.id)
        mocked_delay.assert_called_once_with(str(m.id))


class AnalyticsMigrationDependencyTests(TestCase):
    def test_health_phase3_migration_depends_on_core_health_models(self):
        loader = MigrationLoader(connection, ignore_no_migrations=True)
        migration = loader.disk_migrations[("analytics", "0002_health_analytics_phase3")]
        self.assertIn(
            ("core", "0003_patientmasterrecord_patientfamilyprofile_and_more"),
            migration.dependencies,
        )


class AnalyticsAccessBoundaryTests(TestCase):
    def setUp(self):
        auth_user = get_user_model()
        self.owner = auth_user.objects.create_user(phone="+237670004101", password="TestPass123!", country="CM")
        self.other = auth_user.objects.create_user(phone="+237670004102", password="TestPass123!", country="CM")
        self.staff = auth_user.objects.create_user(
            phone="+237670004103",
            password="TestPass123!",
            country="CM",
            is_staff=True,
        )
        self.owner_org = HealthcareOrganization.objects.create(name="Owner Clinic", slug="owner-clinic", owner=self.owner)
        self.other_org = HealthcareOrganization.objects.create(name="Other Clinic", slug="other-clinic", owner=self.other)
        self.owner_profile = MedicalProfile.objects.create(
            organization=self.owner_org,
            name="Owner Profile",
            slug="owner-profile",
            created_by=self.owner,
        )
        self.other_profile = MedicalProfile.objects.create(
            organization=self.other_org,
            name="Other Profile",
            slug="other-profile",
            created_by=self.other,
        )
        self.owner_report = ClinicalAnalyticsReport.objects.create(
            profile=self.owner_profile,
            organization=self.owner_org,
            report_type="clinical_summary",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            created_by=self.owner,
        )
        self.other_report = ClinicalAnalyticsReport.objects.create(
            profile=self.other_profile,
            organization=self.other_org,
            report_type="clinical_summary",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            created_by=self.other,
        )
        self.client = APIClient()

    def _rows(self, response):
        payload = response.json()
        return payload.get("results", payload)

    def test_clinical_reports_are_limited_to_owned_healthcare_orgs(self):
        self.client.force_authenticate(self.owner)
        response = self.client.get("/api/v1/clinical-reports/")
        self.assertEqual(response.status_code, 200)
        ids = {row["id"] for row in self._rows(response)}
        self.assertIn(str(self.owner_report.id), ids)
        self.assertNotIn(str(self.other_report.id), ids)

    def test_staff_can_see_all_clinical_reports(self):
        self.client.force_authenticate(self.staff)
        response = self.client.get("/api/v1/clinical-reports/")
        self.assertEqual(response.status_code, 200)
        ids = {row["id"] for row in self._rows(response)}
        self.assertIn(str(self.owner_report.id), ids)
        self.assertIn(str(self.other_report.id), ids)

    def test_platform_metrics_are_staff_only(self):
        with patch("apps.analytics.signals.compute_predictive_metrics.delay"):
            Metric.objects.create(kind="system", name="sensitive", value=1)
        self.client.force_authenticate(self.owner)
        denied = self.client.get("/api/v1/metrics/")
        self.assertEqual(denied.status_code, 403)

        self.client.force_authenticate(self.staff)
        allowed = self.client.get("/api/v1/metrics/")
        self.assertEqual(allowed.status_code, 200)


class PlatformInsightsViewTests(TestCase):
    """Covers the real KPI endpoint that replaced the dead /api/v1/dashboards/
    wiring - the one thing that actually matters here is permission scoping:
    a regular user must never reach platform-wide data, and every target
    must return the {kpis, series, breakdown, distribution, top_items}
    shape the RN client expects."""

    def setUp(self):
        auth_user = get_user_model()
        self.user = auth_user.objects.create_user(phone="+237670004201", password="TestPass123!", country="CM")
        self.staff = auth_user.objects.create_user(
            phone="+237670004202", password="TestPass123!", country="CM", is_staff=True,
        )
        self.client = APIClient()

    def _assert_shape(self, response):
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        for key in ("kpis", "series", "breakdown", "distribution", "top_items"):
            self.assertIn(key, data)

    def test_analytics_target_is_staff_only(self):
        self.client.force_authenticate(self.user)
        denied = self.client.get("/api/v1/analytics/insights/", {"target": "analytics"})
        self.assertEqual(denied.status_code, 403)

        self.client.force_authenticate(self.staff)
        allowed = self.client.get("/api/v1/analytics/insights/", {"target": "analytics"})
        self._assert_shape(allowed)

    def test_profile_target_is_open_to_any_authenticated_user(self):
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/v1/analytics/insights/", {"target": "profile"})
        self._assert_shape(response)

    def test_partner_target_returns_empty_kpis_for_a_user_with_no_partner_org(self):
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/v1/analytics/insights/", {"target": "partner"})
        self._assert_shape(response)
        self.assertEqual(response.json()["data"]["kpis"], [])

    def test_unauthenticated_request_is_rejected(self):
        response = self.client.get("/api/v1/analytics/insights/", {"target": "profile"})
        self.assertEqual(response.status_code, 401)

    def test_unknown_target_falls_back_to_profile_scope_not_platform_data(self):
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/v1/analytics/insights/", {"target": "not-a-real-target"})
        self._assert_shape(response)
