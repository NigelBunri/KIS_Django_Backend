"""
Offline book package encryption (KIS Books secure offline downloads).

Distinct from apps.books.encryption's at-rest chapter storage (Fernet,
one opaque token per chapter): an offline package is a multi-part bundle
(a manifest plus one entry per chapter) that must reject a tampered OR
reordered part, not just a corrupted one. That needs explicit per-part
nonces and associated data, so this uses real AES-256-GCM directly via
`cryptography.hazmat.primitives.ciphers.aead.AESGCM` — already available
in the pinned `cryptography` package, no new dependency — rather than
reusing Fernet's single-token API.

Key hierarchy is otherwise IDENTICAL to encryption.py, not a parallel
scheme: an offline package gets its own fresh, random, single-purpose
256-bit DEK (never the book's permanent content-at-rest DEK), wrapped at
rest using the exact same BOOKS_CONTENT_KEK master key via the exact same
Fernet-wrap convention (see encryption.py's _kek_fernet, reused directly
below) — one master key, one wrapping convention, for both content-at-
rest and offline packages. Rotating BOOKS_CONTENT_KEK via
`rewrap_book_content_keys` therefore also needs to re-wrap
BookOfflineLicense.wrapped_package_dek rows — see that command.

Each part (the manifest, or one chapter) is encrypted independently with
its own random nonce and with associated data (AAD) binding book id,
license id, package version, and — for a chapter — its chapter id and
declared order. A ciphertext copied from a different part, a different
chapter's position, a different license, or a different package version
fails AES-GCM's authentication tag check and is rejected outright, not
silently decrypted into the wrong slot. This is what gives the package
real reorder/substitution detection, not just per-blob corruption
detection — scrambling or renaming files would not provide this; the
cryptographic tag failure is what does.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .encryption import ContentEncryptionError, _kek_fernet

NONCE_SIZE_BYTES = 12  # standard/recommended AES-GCM nonce size


def generate_package_dek() -> bytes:
    """A fresh, random, single-use-license 256-bit key — never derived
    from, or equal to, the book's own permanent content-at-rest DEK."""
    return os.urandom(32)


def wrap_package_dek(dek: bytes) -> str:
    """Wraps a package DEK under BOOKS_CONTENT_KEK for at-rest storage on
    BookOfflineLicense.wrapped_package_dek — the raw DEK itself is never
    persisted, mirroring BookContentKey.wrapped_dek exactly."""
    return _kek_fernet().encrypt(dek).decode()


def unwrap_package_dek(wrapped: str) -> bytes:
    from cryptography.fernet import InvalidToken

    try:
        return _kek_fernet().decrypt(wrapped.encode())
    except InvalidToken as exc:
        raise ContentEncryptionError("Offline package key failed integrity verification.") from exc


def _aad(book_id, license_id, package_version: int, part: str) -> bytes:
    return f"{book_id}:{license_id}:{package_version}:{part}".encode()


GCM_TAG_SIZE_BYTES = 16  # standard AES-GCM authentication tag length


def encrypt_part(dek: bytes, plaintext, *, book_id, license_id, package_version: int, part: str) -> dict:
    """Encrypts one logical part of the package. `part` is a stable
    identifier ("manifest", or "chapter:<stable_id>:<order>") baked into
    the AAD — see module docstring for why that matters.

    `ciphertext` and `tag` are returned as SEPARATE base64 fields (the
    `cryptography` package's AESGCM.encrypt() appends the 16-byte tag to
    the ciphertext internally, but is split back out here) because the
    on-device decrypt (KIS/src/services/books/offlineCrypto.ts, via
    react-native-quick-crypto's Node-compatible createDecipheriv) takes
    the tag as a separate setAuthTag() call, not appended ciphertext —
    this makes the wire format match that API exactly rather than
    requiring the client to know Python's internal convention."""
    aesgcm = AESGCM(dek)
    nonce = os.urandom(NONCE_SIZE_BYTES)
    raw = json.dumps(plaintext, sort_keys=True).encode()
    combined = aesgcm.encrypt(nonce, raw, _aad(book_id, license_id, package_version, part))
    ciphertext, tag = combined[:-GCM_TAG_SIZE_BYTES], combined[-GCM_TAG_SIZE_BYTES:]
    return {
        "nonce": base64.b64encode(nonce).decode(),
        "ciphertext": base64.b64encode(ciphertext).decode(),
        "tag": base64.b64encode(tag).decode(),
    }


def decrypt_part(dek: bytes, encrypted: dict, *, book_id, license_id, package_version: int, part: str):
    """Inverse of encrypt_part. Used server-side only for tests that
    prove the AEAD scheme actually rejects tampering/reordering — real
    offline reads decrypt on-device (see KIS/src/services/books/
    offlineCrypto.ts), the server never decrypts a package back down
    after issuing it."""
    aesgcm = AESGCM(dek)
    try:
        nonce = base64.b64decode(encrypted["nonce"])
        ciphertext = base64.b64decode(encrypted["ciphertext"])
        tag = base64.b64decode(encrypted["tag"])
        raw = aesgcm.decrypt(nonce, ciphertext + tag, _aad(book_id, license_id, package_version, part))
    except Exception as exc:
        raise ContentEncryptionError(f"Offline package part {part!r} failed integrity verification.") from exc
    return json.loads(raw.decode())


def content_hash(blocks: list) -> str:
    """SHA-256 of a chapter's canonical plaintext — included (encrypted,
    inside the manifest) as a second, defense-in-depth integrity check
    the client re-verifies after AEAD decryption succeeds."""
    raw = json.dumps(blocks, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()
