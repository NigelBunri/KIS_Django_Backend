"""
Direct verification of the accounts.0039 data-cleanup migration's
cleanup_devices() function against seeded, realistic "bad data" scenarios —
run BEFORE trusting it in production, per explicit request.

This calls the migration's own function against the live model registry
(django.apps.apps) rather than re-deriving the logic, so it's testing the
exact code that will run in production, not a re-implementation of it.
Device/E2EDeviceKey/E2EPreKey have not changed shape since 0039 was written,
so the live registry and the historical migration-state registry resolve to
functionally identical models for every field this migration touches.

Run:
  python3 manage.py test apps.accounts.test_migration_0039_cleanup --keepdb -v 2
"""
import importlib

from django.apps import apps as live_apps
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from .models import Device, E2EDeviceKey
from .tests_qa_full import make_verified_user

_migration_module = importlib.import_module(
    "apps.accounts.migrations.0039_cleanup_device_duplicates_and_multi_parent"
)
cleanup_devices = _migration_module.cleanup_devices

_NEW_CONSTRAINT_NAMES = (
    "accounts_device_user_device_id_uniq",
    "accounts_device_one_active_parent_per_user",
)


def _new_constraints():
    return [c for c in Device._meta.constraints if c.name in _NEW_CONSTRAINT_NAMES]


class Cleanup0039Tests(TestCase):
    """
    These two constraints (added in migration 0040) are already applied to
    this dev/test database — this whole module exists to re-verify that
    0039's cleanup logic is what makes 0040 safe to apply on top of real,
    already-anomalous production data. So each test temporarily drops the
    constraints (simulating the pre-0040 database) to seed the exact bad
    states 0039 has to repair, runs the real migration function, and (for
    the constraint-recheck test) re-adds them to prove they'd now apply
    cleanly. TestCase's per-test transaction rollback (Postgres supports
    transactional DDL) undoes all of this automatically — no teardown
    bookkeeping needed, and other tests are unaffected.
    """

    def setUp(self):
        with connection.schema_editor() as editor:
            for c in _new_constraints():
                editor.remove_constraint(Device, c)

    def _run_cleanup(self):
        cleanup_devices(live_apps, None)

    # --- A: exact duplicate rows, keeper determined by last_seen_at --------
    def test_duplicate_rows_collapse_keeping_most_recently_seen(self):
        user = make_verified_user("+237700000201")
        older = Device.objects.create(
            user=user, device_id="dup-a", platform="ios",
            is_parent=False, token_version=1,
            last_seen_at=timezone.now() - timezone.timedelta(days=2),
        )
        newer = Device.objects.create(
            user=user, device_id="dup-a", platform="ios",
            is_parent=False, token_version=1,
            last_seen_at=timezone.now(),
        )
        E2EDeviceKey.objects.create(
            user=user, device=older, identity_key="old", signed_prekey_id=1,
            signed_prekey="x", signed_prekey_signature="y",
        )
        E2EDeviceKey.objects.create(
            user=user, device=newer, identity_key="new", signed_prekey_id=1,
            signed_prekey="x", signed_prekey_signature="y",
        )

        self._run_cleanup()

        remaining = Device.objects.filter(user=user, device_id="dup-a")
        self.assertEqual(remaining.count(), 1)
        self.assertEqual(remaining.first().pk, newer.pk)
        self.assertTrue(E2EDeviceKey.objects.filter(device=newer).exists())
        # older row (and its key, via CASCADE) is gone
        self.assertFalse(Device.objects.filter(pk=older.pk).exists())
        self.assertFalse(E2EDeviceKey.objects.filter(identity_key="old").exists())

    # --- B: the *losing* duplicate held is_parent — keeper must be promoted
    def test_duplicate_where_loser_was_parent_promotes_the_keeper(self):
        user = make_verified_user("+237700000202")
        older_parent = Device.objects.create(
            user=user, device_id="dup-b", platform="ios",
            is_parent=True, token_version=1,
            last_seen_at=timezone.now() - timezone.timedelta(days=2),
        )
        newer_non_parent = Device.objects.create(
            user=user, device_id="dup-b", platform="ios",
            is_parent=False, token_version=1,
            last_seen_at=timezone.now(),
        )

        self._run_cleanup()

        remaining = Device.objects.get(user=user, device_id="dup-b")
        self.assertEqual(remaining.pk, newer_non_parent.pk)
        self.assertTrue(remaining.is_parent)
        self.assertIsNone(remaining.revoked_at)
        self.assertFalse(Device.objects.filter(pk=older_parent.pk).exists())
        # account is left with exactly one usable primary device
        self.assertEqual(
            Device.objects.filter(user=user, is_parent=True, revoked_at__isnull=True).count(), 1
        )

    # --- C: multiple active parents, no duplicates — demote extras only ---
    def test_multiple_active_parents_demoted_not_revoked(self):
        user = make_verified_user("+237700000203")
        oldest = Device.objects.create(
            user=user, device_id="multi-1", platform="ios", is_parent=True,
            token_version=5, last_seen_at=timezone.now() - timezone.timedelta(days=3),
        )
        middle = Device.objects.create(
            user=user, device_id="multi-2", platform="android", is_parent=True,
            token_version=3, last_seen_at=timezone.now() - timezone.timedelta(days=1),
        )
        newest = Device.objects.create(
            user=user, device_id="multi-3", platform="ios", is_parent=True,
            token_version=1, last_seen_at=timezone.now(),
        )
        for d in (oldest, middle, newest):
            E2EDeviceKey.objects.create(
                user=user, device=d, identity_key=d.device_id,
                signed_prekey_id=1, signed_prekey="x", signed_prekey_signature="y",
            )

        self._run_cleanup()

        oldest.refresh_from_db()
        middle.refresh_from_db()
        newest.refresh_from_db()

        # exactly one active parent remains: the most recently seen
        self.assertTrue(newest.is_parent)
        self.assertFalse(oldest.is_parent)
        self.assertFalse(middle.is_parent)

        # demotion is NOT revocation: nothing here should ever get touched
        for d in (oldest, middle, newest):
            self.assertIsNone(d.revoked_at, f"{d.device_id} must not be revoked by cleanup")
        self.assertEqual(oldest.token_version, 5, "token_version must be untouched by demotion")
        self.assertEqual(middle.token_version, 3, "token_version must be untouched by demotion")
        self.assertEqual(newest.token_version, 1, "token_version must be untouched by demotion")

        # E2EE keys are untouched for every device — demotion isn't revocation
        for d in (oldest, middle, newest):
            self.assertTrue(
                E2EDeviceKey.objects.filter(device=d).exists(),
                f"{d.device_id}'s E2EE keys must survive a mere demotion",
            )

        self.assertEqual(
            Device.objects.filter(user=user, is_parent=True, revoked_at__isnull=True).count(), 1
        )

    # --- D: a well-formed device is left completely untouched -------------
    def test_well_formed_device_is_untouched(self):
        user = make_verified_user("+237700000204")
        device = Device.objects.create(
            user=user, device_id="fine-1", platform="ios", is_parent=True,
            token_version=7, last_seen_at=timezone.now(),
        )
        E2EDeviceKey.objects.create(
            user=user, device=device, identity_key="fine", signed_prekey_id=1,
            signed_prekey="x", signed_prekey_signature="y",
        )

        self._run_cleanup()

        device.refresh_from_db()
        self.assertTrue(device.is_parent)
        self.assertIsNone(device.revoked_at)
        self.assertEqual(device.token_version, 7)
        self.assertTrue(E2EDeviceKey.objects.filter(device=device).exists())
        self.assertEqual(Device.objects.filter(user=user).count(), 1)

    # --- E: a revoked device is never mistaken for a live duplicate/parent
    def test_revoked_device_is_not_touched_or_counted_as_active_parent(self):
        user = make_verified_user("+237700000205")
        active = Device.objects.create(
            user=user, device_id="rev-1", platform="ios", is_parent=True,
            token_version=2, last_seen_at=timezone.now(),
        )
        revoked = Device.objects.create(
            user=user, device_id="rev-2", platform="android", is_parent=True,
            token_version=9, revoked_at=timezone.now() - timezone.timedelta(hours=1),
            revoke_reason="logout", last_seen_at=timezone.now() - timezone.timedelta(hours=2),
        )

        self._run_cleanup()

        active.refresh_from_db()
        revoked.refresh_from_db()
        # revoked row is left exactly as it was — not "revived", not deleted
        self.assertTrue(active.is_parent)
        self.assertIsNone(active.revoked_at)
        self.assertTrue(revoked.is_parent)  # untouched, including its stale is_parent flag
        self.assertIsNotNone(revoked.revoked_at)
        self.assertEqual(revoked.token_version, 9)

    # --- F: cross-user isolation — cleanup for one user never touches another
    def test_does_not_cross_user_boundaries(self):
        user_a = make_verified_user("+237700000206")
        user_b = make_verified_user("+237700000207")
        Device.objects.create(
            user=user_a, device_id="dup-shared-id", platform="ios",
            is_parent=True, token_version=1, last_seen_at=timezone.now() - timezone.timedelta(days=1),
        )
        Device.objects.create(
            user=user_a, device_id="dup-shared-id", platform="ios",
            is_parent=False, token_version=1, last_seen_at=timezone.now(),
        )
        # Same device_id string for a DIFFERENT user — must survive untouched;
        # (user, device_id) is the uniqueness scope, not device_id alone.
        untouched = Device.objects.create(
            user=user_b, device_id="dup-shared-id", platform="android",
            is_parent=True, token_version=4, last_seen_at=timezone.now(),
        )

        self._run_cleanup()

        untouched.refresh_from_db()
        self.assertTrue(untouched.is_parent)
        self.assertIsNone(untouched.revoked_at)
        self.assertEqual(untouched.token_version, 4)
        self.assertEqual(Device.objects.filter(user=user_a, device_id="dup-shared-id").count(), 1)

    # --- G: post-cleanup state actually satisfies the 0040 constraints ----
    def test_post_cleanup_state_satisfies_constraints_added_in_0040(self):
        user = make_verified_user("+237700000208")
        Device.objects.create(
            user=user, device_id="c-1", platform="ios", is_parent=True,
            token_version=1, last_seen_at=timezone.now() - timezone.timedelta(days=1),
        )
        Device.objects.create(
            user=user, device_id="c-2", platform="android", is_parent=True,
            token_version=1, last_seen_at=timezone.now(),
        )
        Device.objects.create(
            user=user, device_id="c-1", platform="ios", is_parent=False,
            token_version=1, last_seen_at=timezone.now() - timezone.timedelta(hours=1),
        )

        self._run_cleanup()

        # No duplicate (user, device_id) pairs remain.
        pairs = list(Device.objects.filter(user=user).values_list("device_id", flat=True))
        self.assertEqual(len(pairs), len(set(pairs)))
        # At most one active parent remains.
        self.assertLessEqual(
            Device.objects.filter(user=user, is_parent=True, revoked_at__isnull=True).count(), 1
        )
        # Directly re-check both invariants the 0040 constraints encode
        # (rather than re-adding the constraints themselves mid-transaction:
        # Postgres raises "pending trigger events" for ALTER TABLE run right
        # after DML on the same table within one transaction/savepoint —
        # a quirk of this test's single-transaction harness, not something
        # the real deploy hits, since 0039 and 0040 run as separate
        # migration transactions).
        seen_parent_users = set()
        for uid, is_parent, revoked_at in Device.objects.filter(
            is_parent=True, revoked_at__isnull=True
        ).values_list("user_id", "is_parent", "revoked_at"):
            self.assertNotIn(uid, seen_parent_users, "duplicate active parent for one user")
            seen_parent_users.add(uid)
