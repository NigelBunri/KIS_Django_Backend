"""
Regression tests for setup_kcan_superadmin's tier grant.

Previously this set user.tier = "Partner Pro" directly — no Subscription
row, no audit trail, and (for a brand-new account) a raw .update() to work
around post_save signal timing. It's now routed through the same canonical
apply_tier_upgrade() used by real paid upgrades, with an indefinite
(never-expiring) Subscription tagged source="admin_grant" so it's
distinguishable from a real payment and excludable from future referral-
commission logic.

Run:
  python3 manage.py test apps.partners.test_setup_kcan_superadmin --keepdb -v 2
"""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.accounts.models import AuditLog, Subscription, User
from apps.partners.management.commands.setup_kcan_superadmin import (
    SUPERADMIN_EMAIL,
    SUPERADMIN_TIER,
)


class SetupKcanSuperadminTierGrantTests(TestCase):
    def _run(self, **kwargs):
        return call_command("setup_kcan_superadmin", stdout=StringIO(), **kwargs)

    def test_new_account_gets_a_real_subscription_not_a_direct_tier_mutation(self):
        self._run(password="TestPass123!")
        user = User.objects.get(email__iexact=SUPERADMIN_EMAIL)
        self.assertEqual(user.tier, SUPERADMIN_TIER)

        sub = Subscription.objects.filter(user=user, status="active").select_related("tier").first()
        self.assertIsNotNone(sub)
        self.assertEqual(sub.tier.name, SUPERADMIN_TIER)
        self.assertIsNone(sub.ends_at, "an admin grant must not expire on the normal 30-day schedule")
        self.assertEqual(sub.billing_meta.get("source"), "admin_grant")

    def test_creates_an_audit_log_entry(self):
        self._run(password="TestPass123!")
        user = User.objects.get(email__iexact=SUPERADMIN_EMAIL)
        entry = AuditLog.objects.filter(actor_id=user.id, action="billing.tier_upgrade").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.meta.get("source"), "admin_grant")
        self.assertEqual(entry.meta.get("tier_name"), SUPERADMIN_TIER)

    def test_rerunning_is_idempotent_and_does_not_duplicate_the_grant(self):
        self._run(password="TestPass123!")
        self._run()  # existing account — no password needed

        user = User.objects.get(email__iexact=SUPERADMIN_EMAIL)
        active_subs = Subscription.objects.filter(
            user=user, status="active", billing_meta__source="admin_grant",
        )
        self.assertEqual(active_subs.count(), 1)

        grant_events = AuditLog.objects.filter(actor_id=user.id, action="billing.tier_upgrade")
        self.assertEqual(grant_events.count(), 1)

    def test_running_again_after_tier_was_manually_changed_regrants_it(self):
        self._run(password="TestPass123!")
        user = User.objects.get(email__iexact=SUPERADMIN_EMAIL)
        user.tier = "Free"
        user.save(update_fields=["tier"])

        self._run()

        user.refresh_from_db()
        self.assertEqual(user.tier, SUPERADMIN_TIER)
        active_subs = Subscription.objects.filter(
            user=user, status="active", billing_meta__source="admin_grant",
        )
        self.assertEqual(active_subs.count(), 1)

    def test_grants_superuser_and_staff_flags(self):
        self._run(password="TestPass123!")
        user = User.objects.get(email__iexact=SUPERADMIN_EMAIL)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_staff)
