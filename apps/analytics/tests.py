from django.test import TestCase
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from unittest.mock import patch

from .models import Metric


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
