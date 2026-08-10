"""
Regression tests proving apps.tiers is quarantined correctly: no longer
publicly URL-exposed, but still installed (migrations/table access intact
for any existing data), and its speculative Celery-reconcile signal no
longer fires.

10 of its 15 routes were previously live and reachable (organizations/,
plans/, entitlements/, usage/, invoices/, plan-features/, partner-settings/,
impact-settings/, campaigns/, holograms/, quantum/) — the other 4
(subscriptions/, users/, flags/, tickets/) were already shadowed by
earlier-registered apps and remain so.

Run:
  python3 manage.py test apps.tiers.test_quarantine --keepdb -v 2
"""
from unittest.mock import patch

from django.apps import apps
from django.test import TestCase
from django.urls import resolve
from django.urls.exceptions import Resolver404

PREVIOUSLY_LIVE_TIERS_ROUTES = [
    "/api/v1/organizations/",
    "/api/v1/plans/",
    "/api/v1/entitlements/",
    "/api/v1/usage/",
    "/api/v1/invoices/",
    "/api/v1/plan-features/",
    "/api/v1/partner-settings/",
    "/api/v1/impact-settings/",
    "/api/v1/campaigns/",
    "/api/v1/holograms/",
    "/api/v1/quantum/",
]

ACCOUNTS_ROUTES_THAT_PREVIOUSLY_SHADOWED_TIERS = {
    "/api/v1/subscriptions/": "apps.accounts.views.SubscriptionViewSet",
    "/api/v1/users/": "apps.accounts.views.UserViewSet",
}


class TiersQuarantineTests(TestCase):
    def test_previously_live_tiers_routes_now_404(self):
        for path in PREVIOUSLY_LIVE_TIERS_ROUTES:
            with self.assertRaises(Resolver404, msg=f"{path} should no longer resolve"):
                resolve(path)

    def test_accounts_routes_that_previously_shadowed_tiers_are_unaffected(self):
        for path, expected_cls_path in ACCOUNTS_ROUTES_THAT_PREVIOUSLY_SHADOWED_TIERS.items():
            match = resolve(path)
            actual = f"{match.func.cls.__module__}.{match.func.cls.__name__}"
            self.assertEqual(actual, expected_cls_path)

    def test_app_remains_installed_for_migration_and_data_access(self):
        self.assertIn("apps.tiers", [cfg.name for cfg in apps.get_app_configs()])
        from apps.tiers.models import BillingPlan
        # Must not raise — the ORM must still be able to query the table.
        self.assertEqual(BillingPlan.objects.count(), 0)

    def test_subscription_save_no_longer_queues_a_reconcile_task(self):
        from apps.tiers.models import BillingPlan, Subscription
        import uuid

        plan = BillingPlan.objects.create(slug=f"test-{uuid.uuid4().hex[:8]}", display_name="Test Plan")
        with patch("apps.tiers.tasks.reconcile_subscription.delay") as mock_delay:
            Subscription.objects.create(
                owner_type="user", owner_id=uuid.uuid4(), plan=plan, status="trialing",
            )
            mock_delay.assert_not_called()
