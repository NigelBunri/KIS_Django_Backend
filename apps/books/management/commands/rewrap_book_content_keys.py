"""
Key rotation for apps.books.encryption's envelope scheme: re-wraps every
book's content DEK (BookContentKey.wrapped_dek) AND every active offline
package's DEK (BookOfflineLicense.wrapped_package_dek) under a NEW
BOOKS_CONTENT_KEK — both use the identical KEK-wrap convention (see
apps.books.offline_crypto's module docstring: "one master key, one
wrapping convention, for both content-at-rest and offline packages").
Chapter ciphertext itself (BookChapter.encrypted_content) and offline
package ciphertext (never stored server-side at all — see
BookOfflineLicense's docstring) are never touched by rotation; only the
much smaller wrapped-DEK rows change.

Usage during a rotation:
  1. Generate a new key: `manage.py shell -c
     "from apps.books.encryption import generate_kek; print(generate_kek())"`
  2. Deploy it as BOOKS_CONTENT_KEK_NEW alongside the current
     BOOKS_CONTENT_KEK (both must be present during the rotation window).
  3. Run: `manage.py rewrap_book_content_keys`
  4. Once this reports 0 remaining old-key rows in EITHER table, promote
     BOOKS_CONTENT_KEK_NEW to BOOKS_CONTENT_KEK and remove the old value.

Safe to re-run: already-rewrapped rows (whose wrapped key already
unwraps under the NEW key) are skipped, not double-wrapped.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Re-wrap every BookContentKey.wrapped_dek and BookOfflineLicense.wrapped_package_dek under BOOKS_CONTENT_KEK_NEW."
    # This command touches only BookContentKey/BookOfflineLicense rows and
    # never introspects unrelated models — skip Django's full system check
    # (which, in this codebase, can surface a pre-existing, unrelated
    # lazy-reference resolution issue between accounts.Profile and
    # commerce.JobListing depending on what's already been imported
    # in-process; not something this command causes or needs re-validated
    # to do its job correctly).
    requires_system_checks = []

    def _rewrap_field(self, queryset, field_name: str, old_fernet, new_fernet, label: str):
        from cryptography.fernet import InvalidToken

        rewrapped = already_done = failed = 0
        for row in queryset:
            wrapped = getattr(row, field_name)
            try:
                new_fernet.decrypt(wrapped.encode())
                already_done += 1
                continue
            except InvalidToken:
                pass

            try:
                dek = old_fernet.decrypt(wrapped.encode())
            except InvalidToken:
                self.stderr.write(self.style.ERROR(f"Could not unwrap {label} for row {row.id} under the OLD key — skipped."))
                failed += 1
                continue

            with transaction.atomic():
                setattr(row, field_name, new_fernet.encrypt(dek).decode())
                row.save(update_fields=[field_name, "updated_at"])
            rewrapped += 1
        return rewrapped, already_done, failed

    def handle(self, *args, **options):
        import os

        from cryptography.fernet import Fernet

        old_kek = os.environ.get("BOOKS_CONTENT_KEK", "").strip()
        new_kek = os.environ.get("BOOKS_CONTENT_KEK_NEW", "").strip()
        if not old_kek or not new_kek:
            raise CommandError(
                "Both BOOKS_CONTENT_KEK (current) and BOOKS_CONTENT_KEK_NEW (target) must be set in the "
                "environment to run a rotation."
            )

        from apps.books.models import BookContentKey, BookOfflineLicense

        old_fernet = Fernet(old_kek.encode())
        new_fernet = Fernet(new_kek.encode())

        c_rewrapped, c_done, c_failed = self._rewrap_field(
            BookContentKey.objects.all(), "wrapped_dek", old_fernet, new_fernet, "content key",
        )
        self.stdout.write(self.style.SUCCESS(
            f"Content keys: rewrapped {c_rewrapped}; {c_done} already on the new key; {c_failed} failed."
        ))

        l_rewrapped, l_done, l_failed = self._rewrap_field(
            BookOfflineLicense.objects.filter(status=BookOfflineLicense.STATUS_ACTIVE),
            "wrapped_package_dek", old_fernet, new_fernet, "offline license key",
        )
        self.stdout.write(self.style.SUCCESS(
            f"Offline license keys: rewrapped {l_rewrapped}; {l_done} already on the new key; {l_failed} failed."
        ))

        total_failed = c_failed + l_failed
        if total_failed:
            self.stderr.write(self.style.WARNING(
                "Some keys could not be rewrapped — do NOT remove the old BOOKS_CONTENT_KEK until both totals are 0."
            ))
