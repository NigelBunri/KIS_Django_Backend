# KIS Books — Operations Guide

Covers everything an operator needs to deploy, configure, and maintain the
`apps.books` marketplace: environment variables, the content-encryption
key lifecycle, OCR dependencies, and scheduled jobs. Implementation
details live in code docstrings (`apps/books/encryption.py`,
`apps/books/conversion.py`); this file is the operational index.

## 1. Required environment variables

| Variable | Required in prod | Purpose |
|---|---|---|
| `BOOKS_CONTENT_KEK` | **Yes** | Master Key-Encryption-Key wrapping every book's per-book data-encryption-key. See §2. |
| `BOOKS_CONTENT_KEK_NEW` | Only during a rotation | Target key for `rewrap_book_content_keys`. See §2.3. |

If `BOOKS_CONTENT_KEK` is unset, `config/settings/base.py` derives a
fallback key from `SECRET_KEY` and logs an error when `DEBUG=False`. That
fallback provides **no real secrecy** — it exists only so local
dev/test doesn't require extra setup. Never rely on it outside `DEBUG`.

## 2. Content encryption key lifecycle

### 2.1 Generating a real production key

```bash
python manage.py shell -c "from apps.books.encryption import generate_kek; print(generate_kek())"
```

This prints a random, URL-safe base64 Fernet key. Store it in your
secrets manager (not source control, not a `.env` committed to git) and
set it as `BOOKS_CONTENT_KEK` for the Django app's runtime environment
(web + worker processes — both need it, since conversion jobs encrypt and
the reader decrypts).

### 2.2 Backup

`BOOKS_CONTENT_KEK` is as sensitive as the database itself: **losing it
makes every book's stored chapter content permanently and
unrecoverably unreadable.** Back it up wherever your other
production secrets are backed up (secrets manager's own durability
guarantees), independently of any single server's disk. Do not rely on
"it's in the running container's environment" as your only copy.

### 2.3 Rotation

1. Generate a new key (§2.1). Do **not** discard the old one yet.
2. Deploy both: keep the current value as `BOOKS_CONTENT_KEK`, add the new
   value as `BOOKS_CONTENT_KEK_NEW`, on every web + worker process.
3. Run:
   ```bash
   python manage.py rewrap_book_content_keys
   ```
   This re-wraps every `BookContentKey.wrapped_dek` under the new key.
   It does **not** touch chapter ciphertext (`BookChapter.encrypted_content`)
   — only the much smaller wrapped-DEK row per book. Safe to re-run; rows
   already wrapped under the new key are skipped, not double-wrapped.
4. Confirm the command reports `0 failed`. If any failed, do not proceed —
   investigate before touching the old key.
5. Promote: set `BOOKS_CONTENT_KEK` to the new value, remove
   `BOOKS_CONTENT_KEK_NEW`, redeploy.

### 2.4 Loss / recovery scenarios

- **Lost `BOOKS_CONTENT_KEK`, have a recent secrets-manager backup**:
  restore it from backup. No data is lost.
- **Lost `BOOKS_CONTENT_KEK` with no backup**: every `BookChapter.encrypted_content`
  row is permanently unreadable. There is no cryptographic recovery path —
  that's the point of the scheme (see the threat model in
  `apps/books/encryption.py`'s module docstring). Recovery requires authors
  to re-upload and re-convert their manuscripts from their own original
  files (which KIS does not retain once conversion succeeds — see
  `apps/books/storage.py`).
- **Suspected key compromise**: rotate immediately (§2.3) — but rotation
  alone does not retroactively protect content an attacker already
  decrypted with the old key before rotation. Treat as a full incident:
  audit `BookModerationAccess` and relevant request logs for unauthorized
  reads during the exposure window.

### 2.5 What this scheme protects against — and what it doesn't

Real protection: a **database-only** compromise (a stolen DB dump, a
leaked read-only DB credential) yields ciphertext in both
`books_bookchapter` and `books_bookcontentkey`, not plaintext — the
attacker also needs `BOOKS_CONTENT_KEK`, which lives outside the database
entirely (app environment / secrets manager).

Not protected against: a compromised **application server** (which holds
`BOOKS_CONTENT_KEK` in its own environment to do its job) or a malicious
**authenticated user with legitimate read access** (they already receive
plaintext via the normal, authorized `/api/v1/books/<id>/chapters/<id>/`
endpoint — encryption-at-rest isn't a substitute for the authorization
checks in `apps.books.services.user_has_reading_access`, which run before
any decryption on every read).

## 2A. Secure offline downloads

### 2A.1 Architecture summary

Every offline package uses **real AES-256-GCM** (not Fernet — see
`apps/books/offline_crypto.py`'s module docstring for why a multi-part
bundle needs explicit per-part nonces/AAD that Fernet's single-token API
doesn't expose) with a **fresh, random, single-purpose 256-bit DEK per
(book, user, device)** — never the book's own permanent content-at-rest
DEK (`BookContentKey`). The package DEK is wrapped at rest using the
exact same `BOOKS_CONTENT_KEK` master key and Fernet-wrap convention as
`BookContentKey.wrapped_dek` (see `apps/books/offline_crypto.py`'s
`wrap_package_dek`/`unwrap_package_dek`) — one master key, one wrapping
convention, for both content-at-rest and offline packages.

Each part (the manifest, or one chapter) is encrypted independently with
its own nonce and AAD binding `book_id:license_id:package_version:part`
together. A tampered byte, a chapter's ciphertext copied into a
different chapter's slot, or a package decrypted against the wrong
license/version all fail the GCM authentication tag check by
construction — this is what gives real reorder/substitution detection,
not file renaming or scrambling.

**Rotating `BOOKS_CONTENT_KEK` (§2.3) also re-wraps every active
`BookOfflineLicense.wrapped_package_dek` row** — `rewrap_book_content_keys`
covers both tables in one run (see §2.3's step 3; its output now reports
two separate rewrapped/already-done/failed counts, one per table). Do
not promote `BOOKS_CONTENT_KEK_NEW` until *both* counts report 0 failed.

### 2A.2 Offline-license policy

- **Validity window: 14 days** from issuance or last renewal
  (`apps.books.services.OFFLINE_LICENSE_VALIDITY_DAYS`).
- The mobile client renews automatically (`GET .../offline/verify/`)
  whenever it has connectivity, well before the locally-cached expiry.
- **A device with no connectivity cannot learn about revocation any
  faster than this 14-day window.** This is a deliberate, documented
  tradeoff, not an oversight — offline reading is impossible without
  some such window, and this codebase makes no claim of instant offline
  revocation anywhere in the client or docs.
- On refund, entitlement revocation, or the book itself being suspended/
  removed by moderation, the server-side license row is revoked
  immediately (`apps.books.services.revoke_offline_license` /
  `revoke_all_offline_licenses_for_book`) — this stops the **next**
  renewal from succeeding and the local app's own verify call will then
  delete the local copy. It does not reach a device that stays offline
  past the current license window.
- Package size is capped at `OFFLINE_PACKAGE_MAX_PLAINTEXT_BYTES` (20MB
  of combined chapter JSON) — a book larger than this cannot be
  downloaded for offline reading; the `/offline/request/` endpoint
  returns 400.
- `THROTTLE_BOOK_OFFLINE_DOWNLOAD` (default 12/min in production) caps
  issue/renew calls — a real but infrequent operation, not general
  browsing traffic.

### 2A.3 Mobile key management

- The package DEK is transmitted **once**, inline in the issue/renew
  response body, over the same TLS-protected authenticated channel a JWT
  already travels over.
- It is moved immediately into `react-native-encrypted-storage`
  (iOS Keychain / Android Keystore-backed) — see
  `KIS/src/security/offlineBookKeys.ts` — and is **never** written to
  disk anywhere else.
- The encrypted package bytes (ciphertext only) are written to
  app-private storage (`RNFS.DocumentDirectoryPath`, excluded from the
  iOS device backup via `NSURLIsExcludedFromBackupKey`; Android has
  `android:allowBackup="false"` set app-wide already) — see
  `KIS/src/storage/offlineBookStorage.ts`.
- Sign-out / account switch clears every downloaded package's key (and
  deletes the on-disk ciphertext) via
  `clearAllOfflineDownloadsForSignOut`, wired into
  `src/security/authStorage.ts`'s `clearAuthSession`.

## 3. OCR (scanned manuscript recognition)

`apps.books.conversion` runs real OCR (PyMuPDF page rendering + Tesseract)
on PDF pages with no extractable text layer.

**Host dependency** (not a Python package): the `tesseract` binary must be
on `PATH`.
- Docker: already added to `Dockerfile`'s `apt-get install` line
  (`tesseract-ocr`).
- Local dev (macOS): `brew install tesseract`.
- Local dev (Debian/Ubuntu): `apt-get install tesseract-ocr`.

**Python dependencies** (in `requirements/base.txt` and `requirements.txt`):
`PyMuPDF`, `pytesseract`.

**Failure mode if missing**: `apps.books.conversion.ManuscriptValidationError`
with `code="dependency_missing"` — the conversion job fails with a clear
message, surfaced to the author as a real error on `BookConversionJob`.
It never silently skips OCR and reports success.

**Limits** (tune via `apps/books/conversion.py` constants if needed):
- `MAX_PDF_PAGES` (2000) — total pages accepted.
- `MAX_OCR_PAGES` (60) — scanned pages a single manuscript may need OCR
  on, before the job fails with `code="too_many_ocr_pages"` rather than
  tying up a worker indefinitely. A document exceeding this must be split
  into smaller files and re-uploaded.

## 4. Scheduled jobs (cron / celery-beat)

Both are also triggered **lazily** on real request paths (so nothing
breaks if the schedule is misconfigured), but should additionally run on
a schedule so state updates even without matching traffic:

| Command / task | Purpose | Suggested cadence |
|---|---|---|
| `manage.py expire_book_restrictions` (`apps.books.tasks.expire_book_publishing_restrictions`) | Flips expired `BookPublishingRestriction` rows to `EXPIRED` and reinstates `user.is_active` if nothing else is holding the account down. | Every 15–30 min |
| `manage.py publish_scheduled_books` (`apps.books.tasks.publish_scheduled_books_task`) | Flips `APPROVED` books whose `scheduled_publish_at` has arrived to `PUBLISHED`. | Every 5–15 min |

Register these via `django_celery_beat` (already a project dependency) in
whichever environment owns the production beat schedule.

## 5. Launch seed

```bash
python manage.py seed_books_launch
```

Idempotent — safe to run on every deploy. Creates/updates the current
Christian Publishing Policy version and four `AdminRole` presets
(`Book Reviewer`, `Book Moderator`, `Book Administrator`, `Book Finance
Officer`) scoped to `app_label="books"`. **Does not assign any role to
any user** — a GO must
explicitly grant these via the existing `admin_control` roles UI
(`/control/admin/roles`). See `apps/books/permissions.py` for exactly
which permission string gates which action.

## 6. Verifying a deployment

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py showmigrations books
python manage.py shell -c "from django.conf import settings; from cryptography.fernet import Fernet; Fernet(settings.BOOKS_CONTENT_KEK.encode()); print('KEK valid')"
python manage.py test apps.books
```

## 7. Production deployment checklist (offline downloads)

A separate, actionable pre-flight checklist for shipping the offline-
downloads feature specifically — not a general Books deploy guide (see
§§1-6 for that). **This section does not deploy anything itself; it is
what to verify before someone with deploy authorization does.**

### 7.1 Migrations

- [ ] `python manage.py showmigrations books` shows `0006_bookofflinelicense`
  unapplied on the production DB (confirms it hasn't already been
  partially applied by a stray earlier attempt).
- [ ] `python manage.py makemigrations --check --dry-run` reports no
  pending model changes (catches a forgotten migration for any other
  in-flight change).
- [ ] Migration `0006` only *adds* a new table (`BookOfflineLicense`) and
  two new columns on the existing `BookPurchase` table
  (`refund_provider`, `refund_provider_ref` — both nullable/blank-default)
  — purely additive, no column drops/renames, no data backfill required,
  safe to run while the app is live (no maintenance window needed).

### 7.2 Environment variables

- [ ] `BOOKS_CONTENT_KEK` is set to a real, randomly-generated Fernet key
  from the production secrets manager (§2.1) — **not** the DEBUG-only
  SECRET_KEY-derived fallback (verify `settings.DEBUG=False` is enforced
  in the production settings module, which already errors loudly if
  this is misconfigured — see §1).
- [ ] `THROTTLE_BOOK_OFFLINE_DOWNLOAD` and `THROTTLE_BOOK_CONTENT_READ`
  are left unset (safe defaults: 12/min and 60/min in production) or
  deliberately overridden — not accidentally left at the dev defaults
  (6000/min), which would disable the intended abuse protection.
- [ ] No new environment variable is required beyond what §1 already
  documents — offline downloads reuse `BOOKS_CONTENT_KEK` entirely.

### 7.3 Encryption-key backup and recovery

- [ ] `BOOKS_CONTENT_KEK`'s backup in the secrets manager is current
  (§2.2) — offline-license package keys (`BookOfflineLicense.
  wrapped_package_dek`) depend on this exact key, same as book content
  keys. A lost `BOOKS_CONTENT_KEK` with no backup makes every existing
  offline license's package permanently unwrappable, in addition to the
  already-documented chapter-content loss (§2.4) — there is no separate
  backup needed for offline downloads specifically, confirming the
  shared-key design (§2A.1) doesn't introduce a second thing to back up.
- [ ] If a `BOOKS_CONTENT_KEK` rotation is planned around this release,
  `rewrap_book_content_keys` has been dry-run against a production-data
  copy first and both its "Content keys" and "Offline license keys"
  output lines report `0 failed` (§2.3 — the rewrap command now covers
  both tables; confirm this is the currently-deployed version of the
  command before relying on that coverage).

### 7.4 Production dependency installation

- [ ] Backend: `cryptography==49.0.0` (or newer, pinned) is present in
  `requirements.txt`/`requirements/base.txt` — already a pre-existing
  dependency (used by `apps.books.encryption`), offline downloads add no
  new Python package. Confirm the production image actually installs
  from the pinned `requirements.txt`, not an unpinned/cached layer.
- [ ] Mobile: no new JS dependency was added either — `react-native-fs`,
  `react-native-encrypted-storage`, `react-native-quick-crypto`, and
  `@react-native-community/netinfo` were all already present in
  `package.json` before this feature and are already linked into both
  the iOS Podfile.lock and Android's autolinking registry (confirmed via
  `android/build/generated/autolinking/autolinking.json`). A clean
  `pod install` (iOS) / Gradle sync (Android) from the existing lockfile
  is sufficient — no new linking step is required.

### 7.5 Image build readiness

- [ ] Backend Docker image: no Dockerfile change was required for this
  feature (no new system packages) — confirm the image build still
  succeeds from a clean cache before release, as a general regression
  check, not because this feature specifically needs it.
- [ ] Mobile release build: since this feature exercises `react-native-
  quick-crypto` and `react-native-fs` at runtime (not just at JS-bundle
  load time), a release-mode build (not just a Debug/dev build) should
  be smoke-tested at least once before shipping — release builds strip
  dev-only code paths and can occasionally surface a linking issue a
  Debug build wouldn't.

### 7.6 Rollback steps

- [ ] **Backend rollback** (revert the deploy, migration already
  applied): `BookOfflineLicense` is a new, additive table — leaving
  migration `0006` applied while rolling back the application code to a
  pre-feature version is safe; the old code simply never references the
  new table or columns. No down-migration is required for a same-day
  rollback.
- [ ] **If a down-migration is ever needed**: it only needs to drop the
  `BookOfflineLicense` table and the two new `BookPurchase` columns —
  no data in any other table is touched by this feature, so a
  reverse migration is low-risk to write if the need arises.
- [ ] **Mobile rollback**: the offline-download UI (download button,
  states, MyLibrary "Available Offline" section) is additive to
  existing screens — reverting the mobile app to a pre-feature build
  leaves any already-downloaded on-device packages as inert, orphaned
  files (not read by the old app version, not a crash risk); no mobile-
  side cleanup action is required for a rollback.

### 7.7 Health checks

- [ ] Standard `apps.books` health/smoke check (§6) passes.
- [ ] One real, end-to-end offline-download request against the
  production API immediately post-deploy, using a real authorized test
  account (not synthetic/seeded production data): `POST /api/v1/books/
  <a real free published book id>/offline/request/` returns `201` with
  a well-formed package (`package_dek`, `manifest`, `chapters` all
  present).
- [ ] `GET /api/v1/books/<same id>/offline/verify/?device_id=<same
  device>` immediately after returns `{"valid": true, ...}`.
- [ ] Confirm `common.middleware.RequestLoggingMiddleware`'s access-log
  output for the above requests contains no request/response body
  content (method/path/status/duration only — already the platform-wide
  behavior, see §4 of the security review in the closure report; this
  check exists to catch a future regression, not a known gap).

### 7.8 Post-deployment tests (safe, non-destructive, using a real test account)

- [ ] Download a real free published book end-to-end against production,
  confirm the package decrypts correctly on a real device.
- [ ] Confirm `python manage.py test apps.books` still passes against
  the production codebase's exact deployed commit (CI should already
  gate this, but worth an explicit post-deploy re-run if CI and deploy
  aren't strictly the same pipeline step).
- [ ] Do **not** test refund/revocation flows against real production
  purchases or real customer accounts — those are already covered by
  the automated test suite (`apps.books.tests.OfflineDownloadTests`,
  `RoyaltyAndRefundTests`) against synthetic data; there is no safe way
  to exercise them against real production money/entitlements as a
  "test."

## 8. Sentence-level annotations and manuscript image placements

### 8.0 Published-chapter content_version fix

`AuthorChapterUpdateView.patch` (plain chapter title/content edits) now
goes through `services.apply_chapter_edit`, which closes a gap found
during this work: editing an already-**published** book's chapter text
previously applied silently — no re-review, no `Book.content_version`
bump — unlike `AuthorBookDetailView.patch` (book-metadata edits), which
always re-queued for review. Now both paths behave the same way for a
real content change. Two things were deliberately kept precise:

- **No-op guard.** A PATCH that resends identical title/`content_blocks`
  (the editor's "Save" tapped with nothing actually edited) does
  **not** bump `content_version` or force a published book back into
  review — the diff is a real content comparison, not "a PATCH request
  arrived." A false bump would needlessly mark every existing offline
  package stale and re-queue a live book for review over nothing.
- **Annotations were never at risk either way.** `resolve_highlight_anchor`
  (§8.1) always re-checks a highlight's live text directly — it never
  consulted `content_version`. Editing one paragraph never silently
  "stales" a highlight anchored to an untouched paragraph elsewhere in
  the same chapter, before or after this fix.

Regression tests: `apps.books.tests.ChapterEditVersioningTests` (real
change bumps version + re-queues; no-op doesn't; draft edits are
unaffected; under-review editing stays blocked; a real edit correctly
flips an existing offline license's `update_available`; an unrelated
block's highlight anchor survives a sibling block being edited).

### 8.1 Highlight/comment anchors

A highlight is anchored to `(chapter.stable_id, block_id, start_offset,
end_offset)` — never a raw screen coordinate or scroll position (see
`BookHighlight` in `apps/books/models.py`). A **comment** is the same
`BookHighlight` row with a non-empty `note` field, not a second entity —
`has_comment` on `MyBookAnnotationSerializer` is just `bool(note)`. This
means every comment also carries a highlight color (the UI defaults to
one if the author doesn't pick one), matching the spec's own example of
a comment rendered with a visible highlight tint beneath it.

`apps.books.services.resolve_highlight_anchor` is the one place
"does this highlight still point at the right passage" is decided: it
re-fetches the live block by `block_id` and checks the stored
`highlighted_text` snapshot is still a prefix of the current text at
`[start_offset:end_offset]`. The mobile reader calls the equivalent
client-side check (`BookReaderScreen`'s `targetHighlightId` effect)
before flashing/scrolling to a passage reached from the Highlights &
Comments Center — an unresolved anchor shows a banner and leaves the
reader at the chapter top instead of silently highlighting the wrong
sentence.

`GET /books/annotations/mine/` (paginated, `?color=` and
`?has_comment=` filters) is the one feed backing the personalized
Highlights & Comments Center (`Profile → My Books Center → Highlights &
Comments`) — scoped to `request.user` only, never another reader's.

### 8.2 Manuscript image placements

An "image" content block is detected at its real reading-order position
during conversion (`apps/books/conversion.py`):

- **DOCX** — exact position, via `document.paragraphs` order and each
  paragraph's `a:blip` XML descendants (`_docx_paragraph_images`).
- **PDF** — `position_confidence: "page_level"`: pypdf exposes no
  sub-page ordering, so an embedded image is placed after its source
  page's text, honestly tagged as approximate rather than exact. A page
  with no text layer (OCR'd as a full-page scan) does NOT also produce
  an image block — the scanned page image IS that page's text content,
  not a separate illustration.
- **TXT** — never produces image blocks.

Image bytes are validated (Pillow-decoded, not just MIME-sniffed),
deduplicated per book by SHA-256 (`BookImageAsset`,
`services.create_or_get_image_asset`), and stored in the same private
`default_storage` convention `apps.books.storage` already uses for
manuscripts — never a public permanent URL. A reader/reviewer/owner gets
a short-lived signed URL via `GET /books/images/<asset_id>/`
(`apps.books.storage.sign_image_for_download`), degrading to an honest
501 (never a silent broken link) on a storage backend with no presigned-
GET support.

`services.submit_for_review` refuses submission while any image
placement's `extraction_status != "extracted"` — the author resolves
this in-app via the "Review images" step
(`ImagePlacementsReviewScreen.tsx` → `POST
/books/mine/<id>/image-placements/<block_id>/image/`), which replaces
only that placement's asset, never shifting any other placement.

### 8.3 Offline image bundling

Every image a chapter's "image" blocks reference is bundled into the
encrypted offline package, using the exact same per-part AEAD scheme a
chapter already uses (`apps.books.offline_crypto.encrypt_part`, part id
`"image:<asset_id>"`) — same nonce/tag/AAD binding to book/license/
package-version/part, so a tampered, reordered, or substituted image
part fails GCM authentication exactly like a tampered chapter does. An
asset referenced by two placements (the same picture used twice) is
bundled exactly once (`BookImageAsset` is already deduped by content
hash — see §8.2) and both placements resolve against the one part.
Image bytes count toward the existing `OFFLINE_PACKAGE_MAX_PLAINTEXT_BYTES`
(20MB) package-size guard alongside chapter text, and are never written
to a plaintext file on either side — base64-encoded in-memory only, on
device decoded straight to a `data:` URI for `<Image>` (see
`src/services/books/offlineCrypto.ts`'s `decryptOfflinePackage`,
`BookReaderScreen.tsx`'s image-URI effect).

Backward/forward compatible by construction: `manifest.images` and
`package.images` are both optional/additive keys. A package built before
this feature existed (or an image-free book) simply has no `images` to
declare — treated as "zero images," never an error, on both the
decrypt path and the on-disk `StoredPackage` schema. No redownload or
package-format version bump was needed.

A placement an author never resolved (still "missing") has no
`asset_id` and is therefore never bundled — the offline reader shows the
same honest "no image yet" fallback it already shows online, not a
broken link.

### 8.4 Known limitations (not fixed in this pass)

- **PDF image positioning is page-level, not exact.** See §8.2 — this is
  a genuine pypdf limitation, not an oversight; surfaced to the author
  in the image review step via `source_page`.
