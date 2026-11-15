"""Regression test for Defect D6 (KIS_E2EE_KEY_LIFECYCLE_AUDIT.md).

E2EEFetchBundleView.get / _serialize_e2ee_bundle_for_device consume a
device's oldest unconsumed one-time prekey under
`E2EPreKey.objects.select_for_update().order_by("created_at").first()`.
Without `skip_locked=True`, a second concurrent caller for the SAME device
doesn't just wait its turn and get the next prekey afterwards - if another
transaction is mid-flight holding the lock on that oldest row, this call
BLOCKS on that exact row, and once unblocked, Postgres re-checks
consumed_at__isnull=True under READ COMMITTED and finds it no longer
matches (the other transaction already consumed it) - so `.first()`
returns None even though a second, perfectly good, unlocked prekey is
sitting one row down. The caller gets a signed-prekey-only bundle (weaker
forward secrecy) instead of the real available one-time prekey.

Uses real threads + TransactionTestCase (not mocked/sequential calls),
mirroring apps/billing/test_wallet_concurrency_fix.py's own pattern, since
a locking fix that's only exercised sequentially proves nothing.
"""
from __future__ import annotations

import threading

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import TransactionTestCase

from apps.accounts.models import Device, E2EDeviceKey, E2EPreKey
from apps.accounts.views import _serialize_e2ee_bundle_for_device

User = get_user_model()


class PrekeySkipLockedConcurrencyTests(TransactionTestCase):
    def test_second_caller_gets_the_next_prekey_instead_of_missing_out_while_the_first_is_locked(self):
        user = User.objects.create_user(phone="+237699700001", password="TestPass12!", country="CM")
        device = Device.objects.create(user=user, device_id="dev-skiplock", platform="ios")
        E2EDeviceKey.objects.create(
            user=user, device=device, identity_key="id", signed_prekey_id=1,
            signed_prekey="spk", signed_prekey_signature="sig", registration_id=1,
        )
        pk1 = E2EPreKey.objects.create(user=user, device=device, prekey_id=1, prekey="key-one")
        pk2 = E2EPreKey.objects.create(user=user, device=device, prekey_id=2, prekey="key-two")
        self.assertLess(pk1.created_at, pk2.created_at)

        row_locked = threading.Event()
        release_lock = threading.Event()
        second_caller_result: dict = {}

        def holder():
            # Simulates another in-flight fetch-bundle transaction that has
            # already locked the oldest row (pk1) via select_for_update, but
            # hasn't committed its consumption yet.
            with transaction.atomic():
                E2EPreKey.objects.select_for_update().get(pk=pk1.pk)
                row_locked.set()
                release_lock.wait(timeout=5)
            connection.close()

        def second_caller():
            row_locked.wait(timeout=5)
            second_caller_result["bundle"] = _serialize_e2ee_bundle_for_device(user, device)
            second_caller_result["done"] = True
            connection.close()

        holder_thread = threading.Thread(target=holder)
        second_thread = threading.Thread(target=second_caller)

        holder_thread.start()
        self.assertTrue(row_locked.wait(timeout=5), "holder never acquired its lock")

        second_thread.start()
        # The whole point of skip_locked: this must return promptly without
        # waiting for the holder to release pk1's lock. If this join times
        # out, the second caller is blocked on the locked row - the bug.
        second_thread.join(timeout=5)
        self.assertTrue(
            second_caller_result.get("done"),
            "second caller blocked on the locked row instead of skipping to the next prekey",
        )

        release_lock.set()
        holder_thread.join(timeout=5)

        bundle = second_caller_result.get("bundle")
        self.assertIsNotNone(bundle)
        self.assertIsNotNone(bundle["one_time_prekey"], "fell back to signed-prekey-only despite pk2 being available")
        self.assertEqual(bundle["one_time_prekey"]["id"], 2)

        pk1.refresh_from_db()
        pk2.refresh_from_db()
        self.assertIsNone(pk1.consumed_at, "holder never consumed pk1 - it must still be unconsumed")
        self.assertIsNotNone(pk2.consumed_at, "second caller must have consumed pk2")
