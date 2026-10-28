"""
KIS Books Marketplace — data model.

Deliberately reuses existing platform infrastructure instead of duplicating
it:
  - "Verified business" (required for paid publishing) = apps.commerce.Shop
    with verification_status == VERIFIED and a working payout rail (same
    entity apps.billing.eligibility.can_receive_payments already checks for
    Market/Education/Health/Broadcast — see apps.billing.direct_payments'
    TARGET_BOOK_PURCHASE wiring).
  - Moderation queue/decisions = apps.moderation.Flag + ModerationAction
    (target_type="book"), surfaced for free in the existing platform-wide
    admin_control content queue (admin_control/views/content_moderation.py)
    and its website page (~/dev/website/app/control/admin/moderation).
  - GO/staff authorization = admin_control.roles.AdminAccessService
    (AdminRole / AdminRoleAssignment), via new "books.review" /
    "books.restrict" / "books.ban_user" permission strings — no second
    RBAC system.
  - Payments = apps.billing.direct_payments (Stripe + Flutterwave,
    idempotent webhook verification) via TARGET_BOOK_PURCHASE.
  - Notifications = apps.notifications.services.create_notification.
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from apps.core.models import BaseEntity


class ChristianCategory(models.TextChoices):
    BIBLE_STUDY = "bible_study", "Bible studies & biblical commentary"
    THEOLOGY = "theology", "Christian theology & doctrine"
    DISCIPLESHIP = "discipleship", "Discipleship & spiritual growth"
    DEVOTIONAL = "devotional", "Prayer, devotionals & meditation"
    BIOGRAPHY = "biography", "Christian biography, testimony & ministry history"
    FICTION = "fiction", "Christian fiction & storytelling"
    CHILDREN = "children", "Christian education & children's literature"
    FAMILY = "family", "Marriage, family & relationships"
    LEADERSHIP = "leadership", "Christian leadership & ministry"
    APOLOGETICS = "apologetics", "Apologetics & faith-related scholarship"
    CHURCH_HISTORY = "church_history", "Christian & church history"
    COUNSELING = "counseling", "Christian counseling & pastoral care"
    LIVING = "living", "Christian living & practical faith"
    WORSHIP = "worship", "Worship, hymns & related education"
    OTHER = "other", "Other Christian literature"


class BookStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PROCESSING = "processing", "Processing manuscript"
    CONVERSION_FAILED = "conversion_failed", "Conversion failed"
    PENDING_REVIEW = "pending_review", "Awaiting review"
    UNDER_REVIEW = "under_review", "Under review"
    CORRECTIONS_REQUESTED = "corrections_requested", "Corrections requested"
    APPROVED = "approved", "Approved"
    PUBLISHED = "published", "Published"
    REJECTED = "rejected", "Rejected"
    SUSPENDED = "suspended", "Suspended"
    REMOVED = "removed", "Removed"
    ARCHIVED = "archived", "Archived"


# Statuses visible/orderable by the public marketplace. Fail-closed: a
# status not in this set never appears in discovery/search/detail for
# ordinary users, mirroring apps.broadcasts.moderation_gate's fail-closed
# broadcast-eligibility rule.
PUBLICLY_VISIBLE_STATUSES = {BookStatus.PUBLISHED}


class Book(BaseEntity):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="authored_books"
    )
    publisher_shop = models.ForeignKey(
        "commerce.Shop", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="published_books",
        help_text="Required only once the book is priced above zero — the verified business receiving payment.",
    )
    title = models.CharField(max_length=300)
    subtitle = models.CharField(max_length=300, blank=True, default="")
    author_name = models.CharField(max_length=255, help_text="Byline shown to readers; may differ from the KIS account name.")
    description = models.TextField(blank=True, default="")
    cover_url = models.URLField(blank=True, default="")
    language = models.CharField(max_length=16, default="en")
    christian_category = models.CharField(max_length=32, choices=ChristianCategory.choices, default=ChristianCategory.OTHER)
    tags = models.JSONField(default=list, blank=True)

    is_free = models.BooleanField(default=True)
    price_cents = models.PositiveIntegerField(default=0)
    currency = models.CharField(max_length=3, default="USD")
    sale_price_cents = models.PositiveIntegerField(null=True, blank=True)
    sale_starts_at = models.DateTimeField(null=True, blank=True)
    sale_ends_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(max_length=32, choices=BookStatus.choices, default=BookStatus.DRAFT, db_index=True)
    rejection_reason = models.TextField(blank=True, default="")
    moderation_notes = models.TextField(blank=True, default="")
    content_version = models.PositiveIntegerField(default=1)

    rights_declared = models.BooleanField(default=False)
    rights_declaration_text = models.TextField(blank=True, default="")

    policy_version_acknowledged = models.ForeignKey(
        "ChristianPublishingPolicyVersion", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    policy_acknowledged_at = models.DateTimeField(null=True, blank=True)

    word_count = models.PositiveIntegerField(default=0)
    estimated_reading_minutes = models.PositiveIntegerField(default=0)

    scheduled_publish_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    download_count = models.PositiveIntegerField(default=0)
    view_count = models.PositiveIntegerField(default=0)

    # Denormalized from BookReview — recomputed (never incremented) by
    # apps.books.services.recompute_book_rating on every review
    # create/update/delete, so these can never drift from the real rows
    # even after a moderation takedown deletes a review out from under them.
    average_rating = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    review_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["owner", "status"]),
        ]

    def __str__(self):
        return self.title

    @property
    def current_price_cents(self) -> int:
        if self.is_free:
            return 0
        now = timezone.now()
        if (
            self.sale_price_cents is not None
            and (not self.sale_starts_at or self.sale_starts_at <= now)
            and (not self.sale_ends_at or self.sale_ends_at >= now)
        ):
            return self.sale_price_cents
        return self.price_cents

    def is_publicly_visible(self) -> bool:
        return self.status in PUBLICLY_VISIBLE_STATUSES


class BookManuscript(BaseEntity):
    STATUS_UPLOADED = "uploaded"
    STATUS_VALIDATING = "validating"
    STATUS_PROCESSING = "processing"
    STATUS_DONE = "done"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_UPLOADED, "Uploaded"),
        (STATUS_VALIDATING, "Validating"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_DONE, "Done"),
        (STATUS_FAILED, "Failed"),
    ]
    FORMAT_PDF = "pdf"
    FORMAT_DOCX = "docx"
    FORMAT_TXT = "txt"
    FORMAT_CHOICES = [(FORMAT_PDF, "PDF"), (FORMAT_DOCX, "DOCX"), (FORMAT_TXT, "Plain text")]

    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="manuscripts")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    # Private storage only — never served by a public URL. See
    # apps.books.storage.PRIVATE_MANUSCRIPT_LOCATION.
    original_file = models.FileField(upload_to="books/manuscripts/%Y/%m/", max_length=512)
    original_filename = models.CharField(max_length=255, blank=True, default="")
    declared_format = models.CharField(max_length=8, choices=FORMAT_CHOICES)
    detected_mime_type = models.CharField(max_length=128, blank=True, default="")
    file_size_bytes = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_UPLOADED)
    error_message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Manuscript for {self.book.title} ({self.declared_format})"


class BookConversionJob(BaseEntity):
    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_SUCCEEDED, "Succeeded"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    manuscript = models.ForeignKey(BookManuscript, on_delete=models.CASCADE, related_name="conversion_jobs")
    attempt_number = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    confidence_score = models.FloatField(null=True, blank=True)
    chapters_detected = models.PositiveIntegerField(default=0)
    images_extracted = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]


class BookChapter(BaseEntity):
    """
    One chapter's structured content. `content_blocks` is a validated list
    of {"type": "heading"|"paragraph"|"image"|"quote"|"list"|"caption",
    "text": str, ...}, stored ENCRYPTED AT REST — the real column is
    `encrypted_content` (Fernet ciphertext under this book's own DEK; see
    apps.books.encryption). `content_blocks` is a Python property, not a
    database field: reading it decrypts, assigning it encrypts. Every
    existing call site (serializers, services, views) that did
    `chapter.content_blocks` / `BookChapter.objects.create(...,
    content_blocks=[...])` keeps working unchanged — Django's own
    Model.__init__ already special-cases assigning to properties that
    shadow a constructor kwarg.

    `stable_id` never changes across re-conversion or author edits so
    reading progress, highlights (future work), and moderation references
    keep pointing at the same logical position.
    """
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="chapters")
    stable_id = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    order = models.PositiveIntegerField()
    title = models.CharField(max_length=300, blank=True, default="")
    encrypted_content = models.TextField(blank=True, default="")
    word_count = models.PositiveIntegerField(default=0)
    source_page_range = models.CharField(max_length=32, blank=True, default="")

    class Meta:
        ordering = ["book_id", "order"]
        unique_together = [("book", "order")]
        indexes = [models.Index(fields=["book", "order"])]

    def __str__(self):
        return f"{self.book.title} — Ch.{self.order} {self.title}"

    @property
    def content_blocks(self) -> list:
        from .encryption import decrypt_content_blocks

        if not self.encrypted_content:
            return []
        return decrypt_content_blocks(self.book, self.encrypted_content)

    @content_blocks.setter
    def content_blocks(self, value: list) -> None:
        from .encryption import encrypt_content_blocks

        self.encrypted_content = encrypt_content_blocks(self.book, value or [])


class BookContentKey(BaseEntity):
    """
    The wrapped (encrypted) per-book Data-Encryption-Key — see
    apps.books.encryption's module docstring for the full envelope-
    encryption design. `wrapped_dek` is never usable without
    settings.BOOKS_CONTENT_KEK, which is never stored in this table or
    anywhere else in the database.
    """
    book = models.OneToOneField(Book, on_delete=models.CASCADE, related_name="content_key")
    wrapped_dek = models.TextField()
    key_version = models.CharField(max_length=16, default="1")

    def __str__(self):
        return f"Content key for {self.book.title} (v{self.key_version})"


class BookEntitlement(BaseEntity):
    SOURCE_FREE = "free"
    SOURCE_PURCHASE = "purchase"
    SOURCE_GRANT = "grant"
    SOURCE_CHOICES = [(SOURCE_FREE, "Free"), (SOURCE_PURCHASE, "Purchase"), (SOURCE_GRANT, "Admin grant")]

    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="entitlements")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="book_entitlements")
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default=SOURCE_FREE)
    granted_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        unique_together = [("book", "user")]

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


class BookPurchase(BaseEntity):
    STATUS_PENDING = "pending"
    STATUS_PAID = "paid"
    STATUS_FAILED = "failed"
    STATUS_REFUNDED = "refunded"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PAID, "Paid"),
        (STATUS_FAILED, "Failed"),
        (STATUS_REFUNDED, "Refunded"),
    ]

    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="purchases")
    buyer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="book_purchases")
    amount_cents = models.PositiveIntegerField(help_text="Authoritative server-computed amount at checkout time — never trusts a client-supplied price.")
    currency = models.CharField(max_length=3, default="USD")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    payment_method = models.CharField(max_length=32, blank=True, default="")
    transaction_reference = models.CharField(max_length=128, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)
    refund_reason = models.CharField(max_length=255, blank=True, default="")
    # Which rail actually executed the refund and its provider-side
    # reference — same two fields apps.commerce.returns_models.Refund
    # records, kept here rather than a second generic Refund row since a
    # book purchase only ever has at most one (full, non-partial) refund.
    refund_provider = models.CharField(max_length=32, blank=True, default="")
    refund_provider_ref = models.CharField(max_length=128, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["buyer", "status"])]

    def __str__(self):
        return f"{self.buyer} — {self.book.title} ({self.status})"


class BookRoyaltyRecord(BaseEntity):
    """One durable record per successful BookPurchase, capturing exactly
    what the platform commission was and what the author's net share was
    at the moment of sale — a priced book's commission tier can change
    later (apps.accounts.tiers.get_platform_commission_pct), so this is a
    point-in-time snapshot, not something ever recomputed after the fact.
    The actual money split already happens for real at the payment
    processor (Stripe application_fee_amount / Flutterwave subaccount
    split, see apps.billing.direct_payments) — this is the queryable
    ledger row for author earnings reporting and refund clawback
    accounting, not what moves the money itself."""
    STATUS_EARNED = "earned"
    STATUS_REVERSED = "reversed"
    STATUS_CHOICES = [(STATUS_EARNED, "Earned"), (STATUS_REVERSED, "Reversed (refunded)")]

    purchase = models.OneToOneField(BookPurchase, on_delete=models.CASCADE, related_name="royalty_record")
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="royalty_records")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="book_royalties")
    gross_amount_cents = models.PositiveIntegerField()
    commission_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    commission_cents = models.PositiveIntegerField(default=0)
    net_royalty_cents = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_EARNED, db_index=True)
    reversed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["author", "status"])]

    def __str__(self):
        return f"Royalty({self.book.title}) {self.net_royalty_cents}c {self.status}"


class BookReadingProgress(BaseEntity):
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="reading_progress")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="book_reading_progress")
    chapter_stable_id = models.UUIDField(null=True, blank=True)
    block_index = models.PositiveIntegerField(default=0)
    percent_complete = models.FloatField(default=0.0)
    is_completed = models.BooleanField(default=False)
    last_read_at = models.DateTimeField(default=timezone.now)
    device_info = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        unique_together = [("book", "user")]


class BookLibraryItem(BaseEntity):
    """
    The explicit "Saved" / want-to-read bookmark on a whole book — the
    personal-library "Saved" bucket. Deliberately NOT used for "currently
    reading" or "completed" (those are derived live from
    BookReadingProgress, which is the one authoritative record of actual
    reading activity — keeping a second, cached status field here would
    let it drift out of sync with real progress).
    """
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="library_items")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="book_library_items")

    class Meta:
        unique_together = [("book", "user")]
        indexes = [models.Index(fields=["user", "-created_at"])]


class BookChapterBookmark(BaseEntity):
    """A quick-jump marker on a specific chapter — distinct from
    BookLibraryItem (which bookmarks the whole book)."""
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="chapter_bookmarks")
    chapter = models.ForeignKey(BookChapter, on_delete=models.CASCADE, related_name="bookmarks")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="book_chapter_bookmarks")
    label = models.CharField(max_length=120, blank=True, default="")

    class Meta:
        unique_together = [("chapter", "user")]
        indexes = [models.Index(fields=["user", "-created_at"])]


class BookHighlight(BaseEntity):
    """
    A highlighted passage. Anchored to (chapter.stable_id, block_id) —
    never a raw offset into the whole chapter's concatenated text, which
    would silently point at the wrong passage the moment an earlier block
    changes length. `block_id` is one of the stable per-block ids
    apps.books.services.assign_block_ids assigns at conversion/edit time.
    `highlighted_text` is a snapshot of what was actually highlighted at
    creation time — it keeps displaying correctly even if the author
    later edits that same block's wording, rather than silently
    re-deriving (and possibly mismatching) a live substring.
    """
    COLOR_CHOICES = [
        ("yellow", "Yellow"), ("green", "Green"), ("blue", "Blue"), ("pink", "Pink"),
    ]
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="highlights")
    chapter = models.ForeignKey(BookChapter, on_delete=models.CASCADE, related_name="highlights")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="book_highlights")
    block_id = models.CharField(max_length=64)
    start_offset = models.PositiveIntegerField(default=0)
    end_offset = models.PositiveIntegerField(default=0)
    color = models.CharField(max_length=16, choices=COLOR_CHOICES, default="yellow")
    highlighted_text = models.TextField()
    note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        # A retried identical highlight request (same user, same exact
        # span) is idempotent — get_or_create on this constraint, not a
        # new duplicate row every time a flaky connection retries.
        unique_together = [("user", "chapter", "block_id", "start_offset", "end_offset")]
        indexes = [models.Index(fields=["user", "book"])]


class BookReview(BaseEntity):
    """A reader's star rating + optional written review. One per
    (book, user) — editing a review updates this same row rather than
    creating a second one, same idempotent-upsert shape as BookHighlight.
    Restricted to readers with real entitlement (see
    apps.books.services.create_or_update_review's access check) so a
    rating always reflects someone who could actually open the book, not
    just anyone with an account. Abuse reports reuse the platform-wide
    apps.moderation.Flag system with target_type="book_review" — same
    pattern as the Book entity itself (see this module's docstring)."""
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="reviews")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="book_reviews")
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    review_text = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("book", "user")]
        indexes = [models.Index(fields=["book", "-created_at"])]


class BookNote(BaseEntity):
    """A free-standing private note. May reference a specific block
    (block_id set) or be a general note on the book/chapter (block_id
    blank). `client_request_id`, when supplied by the caller, makes a
    retried create idempotent without preventing a user from legitimately
    adding several distinct notes to the very same block."""
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="notes")
    chapter = models.ForeignKey(BookChapter, null=True, blank=True, on_delete=models.CASCADE, related_name="notes")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="book_notes")
    block_id = models.CharField(max_length=64, blank=True, default="")
    text = models.TextField()
    client_request_id = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "book"])]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "client_request_id"],
                condition=~models.Q(client_request_id=""),
                name="unique_book_note_client_request_id_per_user",
            ),
        ]


class ChristianPublishingPolicyVersion(BaseEntity):
    version = models.CharField(max_length=32, unique=True)
    title = models.CharField(max_length=255, default="KIS Books Christian Publishing Policy")
    body_markdown = models.TextField()
    is_current = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Policy v{self.version}"


class BookPolicyAcknowledgment(BaseEntity):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="book_policy_acknowledgments")
    policy_version = models.ForeignKey(ChristianPublishingPolicyVersion, on_delete=models.CASCADE, related_name="acknowledgments")
    book = models.ForeignKey(Book, null=True, blank=True, on_delete=models.SET_NULL, related_name="policy_acknowledgments")
    acknowledged_at = models.DateTimeField(default=timezone.now)
    ip_address = models.CharField(max_length=45, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]


class BookPublishingRestriction(BaseEntity):
    """
    GO/moderator-imposed restriction on a user's publishing (or wider KIS
    account) privileges. ACCOUNT_SUSPENDED / ACCOUNT_BANNED are enforced
    through the existing Django auth gate (User.is_active) — not a second,
    frontend-only flag — see apps.books.services.apply_restriction.
    Expiry is evaluated lazily on every permission check AND by the
    `expire_restrictions` management command, so a missed cron run can
    never turn a temporary restriction into a de-facto permanent one.
    """
    TYPE_WARNING = "warning"
    TYPE_PUBLISHING_SUSPENDED = "publishing_suspended"
    TYPE_PAID_PUBLISHING_RESTRICTED = "paid_publishing_restricted"
    TYPE_BUSINESS_PUBLISHING_SUSPENDED = "business_publishing_suspended"
    TYPE_ACCOUNT_SUSPENDED = "account_suspended"
    TYPE_ACCOUNT_BANNED = "account_banned"
    TYPE_CHOICES = [
        (TYPE_WARNING, "Warning"),
        (TYPE_PUBLISHING_SUSPENDED, "Publishing suspended"),
        (TYPE_PAID_PUBLISHING_RESTRICTED, "Paid publishing restricted"),
        (TYPE_BUSINESS_PUBLISHING_SUSPENDED, "Business publishing suspended"),
        (TYPE_ACCOUNT_SUSPENDED, "Account suspended"),
        (TYPE_ACCOUNT_BANNED, "Account banned"),
    ]
    STATUS_ACTIVE = "active"
    STATUS_EXPIRED = "expired"
    STATUS_LIFTED = "lifted"
    STATUS_CHOICES = [(STATUS_ACTIVE, "Active"), (STATUS_EXPIRED, "Expired"), (STATUS_LIFTED, "Lifted")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="book_publishing_restrictions")
    restriction_type = models.CharField(max_length=32, choices=TYPE_CHOICES)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE, db_index=True)
    reason = models.TextField()
    policy_reference = models.CharField(max_length=255, blank=True, default="")
    related_book = models.ForeignKey(Book, null=True, blank=True, on_delete=models.SET_NULL, related_name="restrictions")
    imposed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField(null=True, blank=True, help_text="Null = indefinite/permanent.")
    lifted_at = models.DateTimeField(null=True, blank=True)
    lifted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    lift_notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "status"])]

    def is_in_effect(self) -> bool:
        if self.status != self.STATUS_ACTIVE:
            return False
        if self.ends_at and self.ends_at <= timezone.now():
            return False
        return True


class BookModerationAccess(BaseEntity):
    """Audit trail of a reviewer opening a book's full, unpublished
    manuscript — required by the Christian-publishing-policy spec
    ("log access to sensitive manuscripts where appropriate")."""
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="moderation_accesses")
    accessed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    accessed_at = models.DateTimeField(default=timezone.now)
    ip_address = models.CharField(max_length=45, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]


class BookOfflineLicense(BaseEntity):
    """Authorizes exactly one (book, user, device) to hold an encrypted
    offline package — see apps.books.offline_crypto for the package
    format and apps.books.services' offline_* functions for issuance/
    revocation. The package's own DEK is wrapped at rest here using the
    identical BOOKS_CONTENT_KEK convention BookContentKey uses; the raw
    DEK is only ever transmitted to the client, never stored unwrapped.

    Offline-license policy (see services.OFFLINE_LICENSE_VALIDITY_DAYS):
    a license is valid for 14 days from issuance/last renewal. The
    client MUST call the verify/renew endpoint before expiry while it
    has connectivity, or the locally-stored package becomes unreadable
    (the mobile app enforces this against its own locally-cached
    expires_at — a device with no connectivity cannot learn about
    revocation any faster than this window; see
    docs/KIS_BOOKS_OPERATIONS.md for the full, honest policy writeup)."""
    STATUS_ACTIVE = "active"
    STATUS_REVOKED = "revoked"
    STATUS_CHOICES = [(STATUS_ACTIVE, "Active"), (STATUS_REVOKED, "Revoked")]

    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="offline_licenses")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="book_offline_licenses")
    device_id = models.CharField(max_length=128)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE, db_index=True)
    package_version = models.PositiveIntegerField(default=1)
    # The Book.content_version this package was built from — a later
    # author edit bumping Book.content_version past this means the
    # package is stale ("Updating Download" in the mobile UI), detected
    # by the verify endpoint without needing to compare full content.
    book_content_version = models.PositiveIntegerField(default=1)
    wrapped_package_dek = models.TextField()
    content_hash = models.CharField(max_length=64, blank=True, default="")
    issued_at = models.DateTimeField(default=timezone.now)
    last_renewed_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("book", "user", "device_id")]
        indexes = [models.Index(fields=["user", "status"])]

    @property
    def is_valid(self) -> bool:
        return self.status == self.STATUS_ACTIVE and self.expires_at > timezone.now()


class BookImageAsset(BaseEntity):
    """
    One extracted-or-author-uploaded image file backing one or more
    "image" content blocks (see apps.books.conversion / services for how
    those blocks are detected/assigned). Private storage only — never a
    public permanent URL; see apps.books.storage's signed-URL pattern,
    reused here via sign_image_for_download in services.py.

    Deduplicated per book by `sha256`: the SAME image occurring at two
    manuscript positions produces two distinct "image" content blocks
    (two placement references) but reuses this ONE asset row rather than
    storing the bytes twice — see services.create_or_get_image_asset.
    """
    SOURCE_EXTRACTED = "extracted"
    SOURCE_AUTHOR_UPLOADED = "author_uploaded"
    SOURCE_CHOICES = [(SOURCE_EXTRACTED, "Extracted from manuscript"), (SOURCE_AUTHOR_UPLOADED, "Uploaded by author")]

    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="image_assets")
    file = models.FileField(upload_to="books/images/%Y/%m/", max_length=512)
    sha256 = models.CharField(max_length=64, db_index=True)
    content_type = models.CharField(max_length=64, blank=True, default="")
    file_size_bytes = models.PositiveIntegerField(default=0)
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default=SOURCE_EXTRACTED)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("book", "sha256")]

    def __str__(self):
        return f"Image asset for {self.book.title} ({self.sha256[:8]})"
