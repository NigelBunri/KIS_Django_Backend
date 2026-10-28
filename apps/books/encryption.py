"""
Envelope encryption for book content at rest (Part D of the Books spec).

  Data-Encryption-Key (DEK): one random 32-byte key PER BOOK, used to
  encrypt that book's chapter content. Generated once, stored only in its
  wrapped (encrypted) form in BookContentKey.wrapped_dek.

  Key-Encryption-Key (KEK): settings.BOOKS_CONTENT_KEK, the single master
  key that wraps every book's DEK. Never stored in the database — lives in
  settings/env only (a real secrets manager/KMS in production).

This gives a real, if modest, defense specifically against a
database-only compromise: an attacker who dumps books_bookchapter and
books_bookcontentkey gets ciphertext in both tables, not plaintext — they
also need BOOKS_CONTENT_KEK, which lives outside the database entirely.
It does NOT protect against a compromised application server (which can
read the KEK from its own environment and decrypt everything, same as
any server-side-only encryption scheme) or a malicious authenticated
user with legitimate read access (they already have plaintext via the
normal, authorized read path) — those are different threat models with
different mitigations (see apps.books.permissions and
apps.books.services.user_has_reading_access for authorization, which
runs BEFORE decryption on every read in views.py).

Key rotation: set BOOKS_CONTENT_KEK_NEW alongside the current
BOOKS_CONTENT_KEK, run `manage.py rewrap_book_content_keys` to re-wrap
every BookContentKey.wrapped_dek under the new key, then promote
BOOKS_CONTENT_KEK_NEW to BOOKS_CONTENT_KEK (see that command's docstring
for the full procedure). Chapter ciphertext itself is never touched by
rotation — only the much smaller wrapped-DEK row is.
"""
from __future__ import annotations

import json
import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import transaction

logger = logging.getLogger("security.books")


class ContentEncryptionError(Exception):
    pass


def _kek_fernet(key_b64: str | None = None) -> Fernet:
    key_b64 = key_b64 or settings.BOOKS_CONTENT_KEK
    if not key_b64:
        raise ContentEncryptionError("BOOKS_CONTENT_KEK is not configured.")
    return Fernet(key_b64.encode() if isinstance(key_b64, str) else key_b64)


def _generate_dek() -> bytes:
    return Fernet.generate_key()


def get_or_create_book_dek(book) -> bytes:
    """Returns the book's unwrapped DEK, generating and storing a wrapped
    one on first use. Race-safe: a unique constraint on book (OneToOne)
    means a concurrent double-create raises IntegrityError, caught and
    retried as a plain read."""
    from .models import BookContentKey

    try:
        key_row = BookContentKey.objects.get(book=book)
        return _kek_fernet().decrypt(key_row.wrapped_dek.encode())
    except BookContentKey.DoesNotExist:
        pass

    dek = _generate_dek()
    wrapped = _kek_fernet().encrypt(dek).decode()
    try:
        with transaction.atomic():
            BookContentKey.objects.create(book=book, wrapped_dek=wrapped, key_version="1")
    except Exception:
        # Lost the create race — someone else's key is now authoritative.
        key_row = BookContentKey.objects.get(book=book)
        return _kek_fernet().decrypt(key_row.wrapped_dek.encode())
    return dek


def encrypt_content_blocks(book, content_blocks: list) -> str:
    """Serializes + encrypts a chapter's content_blocks for storage.
    Returns the ciphertext as a str (Fernet tokens are URL-safe base64)."""
    dek = get_or_create_book_dek(book)
    plaintext = json.dumps(content_blocks or []).encode()
    token = Fernet(dek).encrypt(plaintext)
    return token.decode()


def decrypt_content_blocks(book, ciphertext: str) -> list:
    """Inverse of encrypt_content_blocks. Raises ContentEncryptionError
    (never silently returns empty content) if the book has no key row yet
    (ciphertext without a key is a data-integrity bug, not a normal
    empty-chapter state) or decryption/integrity verification fails."""
    if not ciphertext:
        return []
    from .models import BookContentKey

    try:
        key_row = BookContentKey.objects.get(book=book)
    except BookContentKey.DoesNotExist:
        raise ContentEncryptionError(f"No content key found for book {book.id} — cannot decrypt stored content.")
    dek = _kek_fernet().decrypt(key_row.wrapped_dek.encode())
    try:
        plaintext = Fernet(dek).decrypt(ciphertext.encode())
    except InvalidToken as exc:
        logger.error("book_content_decrypt_failed", extra={"book_id": str(book.id)})
        raise ContentEncryptionError(f"Content for book {book.id} failed integrity verification.") from exc
    return json.loads(plaintext.decode())


def generate_kek() -> str:
    """Convenience for ops: `python manage.py shell -c
    "from apps.books.encryption import generate_kek; print(generate_kek())"`
    to produce a real BOOKS_CONTENT_KEK value for a secrets manager."""
    return Fernet.generate_key().decode()
