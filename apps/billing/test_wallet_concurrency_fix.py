"""Regression test for a 2026-09-07 wallet double-spend finding.

record_ledger/debit_wallet_balance/lock_wallet_funds_for_booking/
release_locked_booking_funds/refund_locked_booking_funds/transfer_balance
all used to read WalletAccount.balance_cents, check it, and write a new
value with no row-level locking at all. Two concurrent requests against
the same wallet (a double-tapped "pay" button, a scripted attack, or just
a slow connection triggering a client retry) could both read the same
starting balance, both pass an "is this enough?" check, and both commit -
a genuine double-spend, not a theoretical one, with no DB constraint to
catch the resulting negative balance afterward either.

Fixed by taking a real SELECT ... FOR UPDATE lock (via
get_wallet_account(user, for_update=True)) across the read-check-write
sequence in every wallet-mutating function, so a second concurrent caller
blocks until the first transaction commits and then sees the REAL,
already-decremented balance - not the stale one it started with.

Uses real threads + TransactionTestCase (not mocked/sequential calls),
mirroring apps/billing/test_phase6_concurrency.py's own pattern, since a
race condition fix that's only exercised sequentially proves nothing.
"""
from __future__ import annotations

import threading

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase

from apps.billing.models import WalletAccount
from apps.billing.services import debit_wallet_balance, get_wallet_account

User = get_user_model()


def _make_user(phone: str) -> User:
    return User.objects.create_user(phone=phone, country="CM", password="pass1234")


class ConcurrentWalletDebitTests(TransactionTestCase):
    def test_two_concurrent_debits_that_together_exceed_balance_cannot_both_succeed(self):
        """The exact double-spend shape from the finding: a $10.00 wallet,
        two concurrent $8.00 debits. Before the fix, both could read
        balance_cents=1000, both pass the `< amount_cents` check, and both
        commit - a final balance of $2.00 instead of one being correctly
        rejected. After the fix, exactly one succeeds and the other sees
        the real post-debit balance and is rejected."""
        user = _make_user("+237699600001")
        wallet = get_wallet_account(user)
        wallet.balance_cents = 1000  # $10.00
        wallet.save(update_fields=["balance_cents"])

        results = []
        errors = []
        barrier = threading.Barrier(2)

        def worker():
            try:
                barrier.wait(timeout=5)  # maximize the chance both threads overlap
                debit_wallet_balance(user=user, amount_cents=800, reference="concurrent-debit-test")
                results.append("success")
            except ValueError as exc:
                errors.append(str(exc))
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly one debit succeeded, the other was correctly rejected -
        # never both succeeding (the double-spend) and never both failing.
        self.assertEqual(len(results), 1, f"expected exactly 1 success, got {results} (errors: {errors})")
        self.assertEqual(len(errors), 1, f"expected exactly 1 rejection, got {errors}")
        self.assertIn("Insufficient wallet balance", errors[0])

        wallet.refresh_from_db()
        self.assertEqual(wallet.balance_cents, 200)  # $10.00 - $8.00, not -$6.00

    def test_ten_concurrent_debits_of_a_shared_balance_never_overdraw_it(self):
        """A higher-concurrency version of the same race - 10 threads each
        trying to debit $3.00 from a $10.00 wallet. At most 3 can
        legitimately succeed (leaving $1.00); the fix must make this exact
        every time, not just 'usually' correct under load."""
        user = _make_user("+237699600002")
        wallet = get_wallet_account(user)
        wallet.balance_cents = 1000  # $10.00
        wallet.save(update_fields=["balance_cents"])

        results = []
        barrier = threading.Barrier(10)

        def worker():
            try:
                barrier.wait(timeout=5)
                debit_wallet_balance(user=user, amount_cents=300, reference="concurrent-debit-multi")
                results.append("success")
            except ValueError:
                results.append("rejected")
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successes = results.count("success")
        self.assertEqual(successes, 3, f"expected exactly 3 successful debits, got {successes} ({results})")

        wallet.refresh_from_db()
        self.assertEqual(wallet.balance_cents, 1000 - (successes * 300))
        self.assertGreaterEqual(wallet.balance_cents, 0)
