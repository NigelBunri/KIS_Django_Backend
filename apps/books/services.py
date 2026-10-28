"""Core Books business logic — eligibility, moderation, restrictions,
entitlements. Views stay thin; every rule enforced here is re-checked
server-side regardless of what the client sends."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from .models import (
    Book,
    BookChapter,
    BookChapterBookmark,
    BookEntitlement,
    BookHighlight,
    BookImageAsset,
    BookLibraryItem,
    BookNote,
    BookPolicyAcknowledgment,
    BookPublishingRestriction,
    BookPurchase,
    BookReadingProgress,
    BookReview,
    BookRoyaltyRecord,
    BookStatus,
    ChristianPublishingPolicyVersion,
)

logger = logging.getLogger("security.books")

# A highlight is a reader's own quote/annotation, not a copy of the book —
# and it's also the one unit of book content allowed to leave the app (see
# the "share to chat" flow, which sends a highlight's own highlighted_text
# verbatim). Capping it here, at the single point every highlight is
# created, controls both: a reader can still mark up a long paragraph for
# themselves, but neither the stored highlight nor anything built from it
# can ever smuggle out more than a short excerpt.
MAX_HIGHLIGHT_CHARS = 500


def assign_block_ids(blocks: list) -> list:
    """Every content block gets a stable `id` that never changes once
    assigned — BookHighlight/BookNote/BookChapterBookmark reference
    (chapter.stable_id, block["id"]) rather than raw character offsets
    into the whole chapter, so a highlight survives edits to OTHER
    blocks in the same chapter. Blocks that already have an id (e.g. an
    author's PATCH that only changed a sibling block) keep it unchanged —
    this is idempotent, not a reset."""
    result = []
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        if not block.get("id"):
            block = {**block, "id": str(uuid.uuid4())}
        result.append(block)
    return result


# ───────────────────────── Christian publishing policy ─────────────────────

def current_policy_version() -> ChristianPublishingPolicyVersion | None:
    return ChristianPublishingPolicyVersion.objects.filter(is_current=True).order_by("-created_at").first()


def acknowledge_policy(*, user, book: Book | None = None, ip_address: str = "") -> BookPolicyAcknowledgment:
    policy = current_policy_version()
    if policy is None:
        raise ValidationError("No Christian publishing policy is currently configured. Contact support.")
    ack, _ = BookPolicyAcknowledgment.objects.get_or_create(
        user=user, policy_version=policy, book=book,
        defaults={"ip_address": ip_address},
    )
    if book is not None:
        book.policy_version_acknowledged = policy
        book.policy_acknowledged_at = ack.acknowledged_at
        book.save(update_fields=["policy_version_acknowledged", "policy_acknowledged_at", "updated_at"])
    return ack


# ───────────────────────── Publishing restrictions ──────────────────────────

ACCOUNT_LEVEL_TYPES = {
    BookPublishingRestriction.TYPE_ACCOUNT_SUSPENDED,
    BookPublishingRestriction.TYPE_ACCOUNT_BANNED,
}


def expire_stale_restrictions(user=None) -> int:
    """Lazily (and via a management command, in bulk) flips ACTIVE
    restrictions whose ends_at has passed to EXPIRED, and reinstates
    user.is_active if no other account-level restriction remains in
    effect. A temporary ban can never silently become permanent just
    because no background sweep ran — every permission check below calls
    this first for the user in question."""
    qs = BookPublishingRestriction.objects.filter(
        status=BookPublishingRestriction.STATUS_ACTIVE,
        ends_at__isnull=False,
        ends_at__lte=timezone.now(),
    )
    if user is not None:
        qs = qs.filter(user=user)
    count = 0
    for restriction in qs.select_related("user"):
        with transaction.atomic():
            restriction.status = BookPublishingRestriction.STATUS_EXPIRED
            restriction.lifted_at = timezone.now()
            restriction.lift_notes = "Auto-expired."
            restriction.save(update_fields=["status", "lifted_at", "lift_notes", "updated_at"])
            if restriction.restriction_type in ACCOUNT_LEVEL_TYPES:
                _reinstate_account_if_clear(restriction.user)
        count += 1
    return count


def _reinstate_account_if_clear(user) -> None:
    """Flips is_active back to True once no Books restriction still
    requires it False — but ONLY if nothing else is also holding this
    account down. is_active=False is a shared flag: admin_control's
    AdminUserBlockView and apps.moderation.services.apply_media_safety_action
    both set it for entirely unrelated reasons (generic account blocks,
    severe AI-flagged content). Lifting or expiring a Books-only
    restriction must never silently undo one of those — fail closed (stay
    restricted) rather than fail open, matching the platform's existing
    moderation_gate convention. User.status (the generic account-status
    field other subsystems set) is the signal checked here; if it isn't
    "active", some other subsystem considers this account restricted and
    reinstatement is skipped even though every Books-specific restriction
    is gone."""
    still_restricted = BookPublishingRestriction.objects.filter(
        user=user, status=BookPublishingRestriction.STATUS_ACTIVE,
        restriction_type__in=ACCOUNT_LEVEL_TYPES,
    ).exists()
    if still_restricted:
        return
    user.refresh_from_db(fields=["is_active", "status"])
    if not user.is_active and user.status in ("", "active"):
        user.is_active = True
        user.save(update_fields=["is_active"])


def active_restrictions(user, *, restriction_types=None):
    expire_stale_restrictions(user)
    qs = BookPublishingRestriction.objects.filter(user=user, status=BookPublishingRestriction.STATUS_ACTIVE)
    if restriction_types:
        qs = qs.filter(restriction_type__in=restriction_types)
    return [r for r in qs if r.is_in_effect()]


def assert_can_publish(user) -> None:
    blocking = active_restrictions(
        user,
        restriction_types=[
            BookPublishingRestriction.TYPE_PUBLISHING_SUSPENDED,
            BookPublishingRestriction.TYPE_ACCOUNT_SUSPENDED,
            BookPublishingRestriction.TYPE_ACCOUNT_BANNED,
        ],
    )
    if blocking:
        raise PermissionDenied(
            f"Publishing is currently restricted on this account: {blocking[0].get_restriction_type_display()}."
        )


def assert_can_publish_paid(user) -> None:
    assert_can_publish(user)
    blocking = active_restrictions(
        user,
        restriction_types=[
            BookPublishingRestriction.TYPE_PAID_PUBLISHING_RESTRICTED,
            BookPublishingRestriction.TYPE_BUSINESS_PUBLISHING_SUSPENDED,
        ],
    )
    if blocking:
        raise PermissionDenied(
            f"Paid publishing is currently restricted on this account: {blocking[0].get_restriction_type_display()}."
        )


def apply_restriction(
    *, user, restriction_type: str, reason: str, imposed_by, policy_reference: str = "",
    related_book=None, ends_at: datetime | None = None,
) -> BookPublishingRestriction:
    restriction = BookPublishingRestriction.objects.create(
        user=user,
        restriction_type=restriction_type,
        reason=reason,
        policy_reference=policy_reference,
        related_book=related_book,
        imposed_by=imposed_by,
        ends_at=ends_at,
    )
    if restriction_type in ACCOUNT_LEVEL_TYPES and user.is_active:
        user.is_active = False
        user.save(update_fields=["is_active"])
    _notify(user_id=user.id, notif_type="book_account_restricted", context={
        "restriction_type": restriction_type, "reason": reason,
        "ends_at": ends_at.isoformat() if ends_at else None,
    })
    return restriction


def lift_restriction(restriction: BookPublishingRestriction, *, lifted_by, notes: str = "") -> BookPublishingRestriction:
    restriction.status = BookPublishingRestriction.STATUS_LIFTED
    restriction.lifted_at = timezone.now()
    restriction.lifted_by = lifted_by
    restriction.lift_notes = notes
    restriction.save(update_fields=["status", "lifted_at", "lifted_by", "lift_notes", "updated_at"])
    if restriction.restriction_type in ACCOUNT_LEVEL_TYPES:
        _reinstate_account_if_clear(restriction.user)
    return restriction


# ───────────────────────── Paid-publishing eligibility ──────────────────────

def eligible_shop_for_paid_publishing(user):
    """Returns the user's verified, payment-ready Shop, or None. This is
    THE server-side gate for paid book publishing — reuses the exact same
    Shop.verification_status + payout-readiness check every other domain
    (Market/Education/Health/Broadcast) already relies on, rather than a
    second "is this author eligible" system."""
    from apps.billing.eligibility import can_receive_payments
    from apps.commerce.models import Shop

    shop = (
        Shop.objects.filter(owner=user, verification_status="VERIFIED")
        .order_by("-created_at")
        .first()
    )
    if shop is None:
        return None
    if not can_receive_payments(shop).eligible:
        return None
    return shop


def assert_eligible_for_price(*, user) -> "Shop":  # noqa: F821 - string annotation, avoids import at module load
    assert_can_publish_paid(user)
    shop = eligible_shop_for_paid_publishing(user)
    if shop is None:
        raise ValidationError(
            "Setting a price requires a verified business with a working payout account. "
            "Register or verify your business, then return to this draft — your manuscript and edits are preserved."
        )
    return shop


# ───────────────────────── Submission / moderation ──────────────────────────

def submit_for_review(book: Book, *, actor) -> Book:
    if book.status not in {BookStatus.DRAFT, BookStatus.CORRECTIONS_REQUESTED, BookStatus.REJECTED}:
        raise ValidationError(f"Book in status '{book.status}' cannot be submitted for review.")
    if not book.chapters.exists():
        raise ValidationError("This book has no converted content yet — upload and convert a manuscript first.")
    unresolved = unresolved_image_placements(book)
    if unresolved:
        raise ValidationError(
            f"{len(unresolved)} image placement(s) in this manuscript still need an image before you can "
            "submit for review — open the image review step and upload or replace them."
        )
    if not book.rights_declared:
        raise ValidationError("You must declare publishing rights before submitting for review.")
    if book.policy_version_acknowledged_id is None:
        raise ValidationError("You must acknowledge the Christian Publishing Policy before submitting for review.")
    if not book.is_free:
        shop = eligible_shop_for_paid_publishing(book.owner)
        if shop is None:
            raise ValidationError(
                "This book is priced, but no verified business with a working payout account is linked yet."
            )
        book.publisher_shop = shop
    assert_can_publish(book.owner)

    from apps.moderation.models import Flag

    Flag.objects.create(
        source="USER",
        target_type="book",
        target_id=book.id,
        reporter_id=actor.id,
        reason="New book submitted for Christian publishing policy review.",
        severity="LOW",
        status="PENDING",
    )
    book.status = BookStatus.PENDING_REVIEW
    book.submitted_at = timezone.now()
    book.save(update_fields=["status", "submitted_at", "publisher_shop", "updated_at"])
    return book


def _book_flag(book: Book):
    from apps.moderation.models import Flag

    return Flag.objects.filter(target_type="book", target_id=book.id).order_by("-created_at").first()


def apply_chapter_edit(book: Book, chapter: "BookChapter", *, title: str | None, content_blocks: list | None) -> "BookChapter":
    """The one place chapter title/content edits are applied —
    AuthorChapterUpdateView's whole body. Deliberately checks whether
    anything about the chapter's REAL content changed before touching
    Book.content_version/status: a no-op PATCH (the editor's "Save"
    button tapped with nothing actually edited) must never force an
    already-published book back into review or mark existing offline
    packages stale — both of those are driven by content_version, which
    must track genuine content changes only (see
    apps.books.services.issue_or_renew_offline_license's reuse_existing_dek
    check and verify_offline_license's update_available flag).

    Existing reading-progress/highlight/bookmark anchors are NEVER
    invalidated by content_version itself — those resolve against the
    chapter's live text directly (see resolve_highlight_anchor), which is
    strictly more precise than a whole-chapter version counter: editing
    one paragraph doesn't retroactively "stale" a highlight anchored to
    an untouched paragraph elsewhere in the same chapter."""
    from .conversion import estimate_reading_minutes

    changed = False
    chapter_update_fields = []
    if title is not None and title != chapter.title:
        chapter.title = title
        changed = True
        chapter_update_fields.append("title")

    if content_blocks is not None:
        if not isinstance(content_blocks, list):
            raise ValidationError("content_blocks must be a list.")
        # Existing blocks keep their stable id (so highlights/notes/
        # bookmarks on unrelated blocks in this chapter survive this
        # edit); only genuinely new blocks get a freshly assigned one.
        content_blocks = assign_block_ids(content_blocks)
        if content_blocks != chapter.content_blocks:
            chapter.content_blocks = content_blocks
            chapter.word_count = sum(len(str(b.get("text", "")).split()) for b in content_blocks if isinstance(b, dict))
            changed = True
            chapter_update_fields += ["encrypted_content", "word_count"]

    if not changed:
        return chapter

    chapter.save(update_fields=[*chapter_update_fields, "updated_at"])

    book.word_count = sum(c.word_count for c in book.chapters.all())
    book.estimated_reading_minutes = estimate_reading_minutes(book.word_count)
    book_update_fields = ["word_count", "estimated_reading_minutes", "updated_at"]
    if book.status == BookStatus.PUBLISHED:
        # A real content change to a LIVE book requires re-review before
        # the edit goes out, exactly like AuthorBookDetailView.patch's
        # own book-metadata edit path — and correctly marks every
        # existing offline package for this book stale (content_version
        # bump), which a published-book chapter edit previously did not.
        book.content_version += 1
        book.status = BookStatus.PENDING_REVIEW
        book.submitted_at = timezone.now()
        book_update_fields += ["content_version", "status", "submitted_at"]
    book.save(update_fields=book_update_fields)
    return chapter


VALID_MODERATION_ACTIONS = {"approve", "reject", "request_corrections", "block", "restore", "escalate"}


def apply_moderation_decision(book: Book, *, action: str, actor, notes: str = "") -> Book:
    if action not in VALID_MODERATION_ACTIONS:
        raise ValidationError(f"Unknown moderation action: {action!r}")

    from apps.moderation.models import ModerationAction

    flag = _book_flag(book)

    if action == "approve":
        held_on_eligibility = not book.is_free and eligible_shop_for_paid_publishing(book.owner) is None
        if held_on_eligibility:
            book.status = BookStatus.APPROVED
            book.moderation_notes = "Approved, but publishing is held: the author's business payout setup is no longer eligible."
        elif book.scheduled_publish_at and book.scheduled_publish_at > timezone.now():
            book.status = BookStatus.APPROVED
        else:
            book.status = BookStatus.PUBLISHED
            book.published_at = timezone.now()
        book.reviewed_by = actor
        book.reviewed_at = timezone.now()
        book.save(update_fields=["status", "moderation_notes", "reviewed_by", "reviewed_at", "published_at", "updated_at"])
        if flag:
            flag.status = "ACTIONED"
            flag.reviewed_at = timezone.now()
            flag.save(update_fields=["status", "reviewed_at"])
        # Distinct notification type when publishing is held on an
        # eligibility problem — "book_approved" alone would wrongly tell
        # the author their book is live when apply_moderation_decision
        # deliberately kept it at APPROVED, not PUBLISHED, above.
        _notify(
            user_id=book.owner_id,
            notif_type="book_approved_held" if held_on_eligibility else "book_approved",
            context={"book_id": str(book.id), "title": book.title},
        )

    elif action == "reject":
        book.status = BookStatus.REJECTED
        book.rejection_reason = notes
        book.reviewed_by = actor
        book.reviewed_at = timezone.now()
        book.save(update_fields=["status", "rejection_reason", "reviewed_by", "reviewed_at", "updated_at"])
        if flag:
            flag.status = "ACTIONED"
            flag.reviewed_at = timezone.now()
            flag.save(update_fields=["status", "reviewed_at"])
        _notify(user_id=book.owner_id, notif_type="book_rejected", context={"book_id": str(book.id), "title": book.title, "reason": notes})

    elif action == "request_corrections":
        book.status = BookStatus.CORRECTIONS_REQUESTED
        book.moderation_notes = notes
        book.reviewed_by = actor
        book.reviewed_at = timezone.now()
        book.save(update_fields=["status", "moderation_notes", "reviewed_by", "reviewed_at", "updated_at"])
        if flag:
            flag.status = "REVIEWED"
            flag.reviewed_at = timezone.now()
            flag.save(update_fields=["status", "reviewed_at"])
        _notify(user_id=book.owner_id, notif_type="book_corrections_requested", context={"book_id": str(book.id), "title": book.title, "notes": notes})

    elif action == "block":
        was_published = book.status == BookStatus.PUBLISHED
        book.status = BookStatus.SUSPENDED
        book.moderation_notes = notes
        book.reviewed_by = actor
        book.reviewed_at = timezone.now()
        book.save(update_fields=["status", "moderation_notes", "reviewed_by", "reviewed_at", "updated_at"])
        if flag:
            flag.status = "ACTIONED"
            flag.reviewed_at = timezone.now()
            flag.save(update_fields=["status", "reviewed_at"])
        # A suspended book's every offline copy's NEXT renewal must fail
        # too, regardless of who holds it — the book itself is no longer
        # authorized to be read, offline or not.
        revoke_all_offline_licenses_for_book(book, reason=f"Book suspended: {notes}".strip(": "))
        _notify(user_id=book.owner_id, notif_type="book_suspended", context={"book_id": str(book.id), "title": book.title, "reason": notes, "was_published": was_published})

    elif action == "restore":
        book.status = BookStatus.PUBLISHED if book.published_at else BookStatus.APPROVED
        book.moderation_notes = notes
        book.reviewed_by = actor
        book.reviewed_at = timezone.now()
        book.save(update_fields=["status", "moderation_notes", "reviewed_by", "reviewed_at", "updated_at"])
        _notify(user_id=book.owner_id, notif_type="book_restored", context={"book_id": str(book.id), "title": book.title})

    elif action == "escalate":
        if flag:
            flag.severity = "HIGH"
            flag.escalation_level = "ADMIN"
            flag.save(update_fields=["severity", "escalation_level"])

    if flag:
        ModerationAction.objects.create(
            flag=flag,
            action={"approve": "REINSTATE" if action == "restore" else "WARN",
                    "reject": "DELETE", "request_corrections": "WARN",
                    "block": "SUSPEND", "restore": "REINSTATE", "escalate": "ESCALATE"}.get(action, "WARN"),
            notes=notes,
            performed_by_id=actor.id,
        )
    return book


def publish_scheduled_books() -> int:
    """Flips APPROVED books whose scheduled_publish_at has arrived to
    PUBLISHED. Called lazily from the public marketplace views (same
    pattern as expire_stale_restrictions) AND available as a management
    command / Celery task for a batch sweep — a scheduled book must not
    stay stuck at APPROVED forever just because nobody happens to hit the
    marketplace list endpoint. Re-checks paid-eligibility exactly like
    apply_moderation_decision's approve branch does, so a business that
    lost payout eligibility between approval and the scheduled time still
    doesn't get silently published."""
    due = Book.objects.filter(status=BookStatus.APPROVED, scheduled_publish_at__lte=timezone.now())
    count = 0
    for book in due:
        if not book.is_free and eligible_shop_for_paid_publishing(book.owner) is None:
            continue
        book.status = BookStatus.PUBLISHED
        book.published_at = timezone.now()
        book.save(update_fields=["status", "published_at", "updated_at"])
        _notify(user_id=book.owner_id, notif_type="book_approved", context={"book_id": str(book.id), "title": book.title})
        count += 1
    return count


# ───────────────────────── Entitlements / library ───────────────────────────

def grant_entitlement(book: Book, user, *, source: str = BookEntitlement.SOURCE_FREE) -> BookEntitlement:
    entitlement, created = BookEntitlement.objects.get_or_create(
        book=book, user=user, defaults={"source": source},
    )
    if not created and entitlement.revoked_at:
        entitlement.revoked_at = None
        entitlement.revoked_reason = ""
        entitlement.source = source
        entitlement.save(update_fields=["revoked_at", "revoked_reason", "source", "updated_at"])
    return entitlement


def revoke_entitlement(book: Book, user, *, reason: str = "") -> None:
    BookEntitlement.objects.filter(book=book, user=user, revoked_at__isnull=True).update(
        revoked_at=timezone.now(), revoked_reason=reason,
    )
    # Every path that revokes reading access (refund, future admin
    # revoke) must also stop offline renewal — a single call site rather
    # than scattering this at each caller.
    revoke_offline_license(book, user, reason=reason or "Entitlement revoked.")


# ─────────────────────────── Content-protection: scraping ───────────────────

# A real reader on the mobile app fetches a new chapter roughly every few
# minutes at most; a script harvesting a book's (or the whole catalog's)
# content hits this endpoint dozens of times a minute. This is a
# best-effort SIGNAL, not an access control - it only raises a moderation
# Flag for GO/staff to review, and deliberately never blocks a read, so a
# false positive (an unusually fast genuine reader) can never lock a
# paying customer out of a book they already bought. The hard access gate
# remains user_has_reading_access; THROTTLE_BOOK_CONTENT_READ (see
# settings) is the layer that actually rejects requests.
CHAPTER_READ_SCRAPE_WINDOW_SECONDS = 120
CHAPTER_READ_SCRAPE_THRESHOLD = 25


def record_chapter_read_and_detect_scraping(user, request) -> None:
    from django.core.cache import cache

    from apps.accounts.security_events import client_ip, safe_identifier
    from apps.moderation.models import Flag

    window = CHAPTER_READ_SCRAPE_WINDOW_SECONDS
    is_authenticated = bool(user is not None and getattr(user, "is_authenticated", False))

    if is_authenticated:
        cache_key = f"books:chapter_read:user:{user.id}"
        count = cache.get(cache_key, 0) + 1
        cache.set(cache_key, count, window)
        if count == CHAPTER_READ_SCRAPE_THRESHOLD:
            Flag.objects.create(
                source="SYSTEM",
                target_type="book_scraping_suspected",
                target_id=user.id,
                reason=f"{count} book chapter reads in {window}s from this account — possible automated scraping.",
                severity="MEDIUM",
                status="PENDING",
            )
        return

    # Anonymous (free-book) reads have no UUID to attach a Flag row to
    # (Flag.target_id is a strict UUIDField) — logged for visibility
    # instead of silently dropped, rather than forcing a fake target.
    ip = client_ip(request)
    if not ip:
        return
    cache_key = f"books:chapter_read:ip:{safe_identifier(ip)}"
    count = cache.get(cache_key, 0) + 1
    cache.set(cache_key, count, window)
    if count == CHAPTER_READ_SCRAPE_THRESHOLD:
        logger.warning(
            "book_chapter_scraping_suspected_anonymous",
            extra={"ip_hash": safe_identifier(ip), "count": count, "window_seconds": window},
        )


# ────────────────────────────── Royalties & refunds ─────────────────────────

def record_royalty(purchase: "BookPurchase") -> "BookRoyaltyRecord":
    """Called once a BookPurchase is marked PAID (see
    apps.billing.direct_payments' TARGET_BOOK_PURCHASE webhook handler) —
    snapshots the SAME commission rate the payment processor itself just
    applied to the real money split (the one existing rate resolver,
    apps.accounts.tiers.get_platform_commission_pct), so this ledger row
    always agrees with what the author was actually paid. Idempotent:
    get_or_create on the purchase's own OneToOne — a replayed webhook
    calling this twice for the same purchase is a no-op, not a second row
    or a double-counted commission rate resolved at a possibly different
    moment."""
    from apps.accounts.tiers import get_platform_commission_pct
    from apps.billing.direct_payments import resolve_payout_entity
    from apps.billing.models import DirectPaymentIntent

    existing = BookRoyaltyRecord.objects.filter(purchase=purchase).first()
    if existing:
        return existing

    entity = resolve_payout_entity(DirectPaymentIntent.TARGET_BOOK_PURCHASE, purchase.id)
    pct = get_platform_commission_pct(entity.owner) if entity is not None else 0.0
    gross = int(purchase.amount_cents)
    commission_cents = int(round(gross * max(0.0, min(100.0, pct)) / 100))
    return BookRoyaltyRecord.objects.create(
        purchase=purchase, book=purchase.book, author=purchase.book.owner,
        gross_amount_cents=gross, commission_pct=pct,
        commission_cents=commission_cents, net_royalty_cents=gross - commission_cents,
    )


def _execute_book_refund(purchase: "BookPurchase") -> tuple[bool, str, str, str]:
    """Returns (success, provider, provider_ref, failure_reason) — mirrors
    apps.commerce.returns_services._execute_provider_refund's shape and
    honesty: a provider this doesn't know how to refund through yet
    returns a clear failure_reason, never a fabricated success."""
    if purchase.payment_method == "flutterwave" and purchase.transaction_reference:
        from apps.billing.direct_payments import refund_flutterwave_transaction

        try:
            result = refund_flutterwave_transaction(purchase.transaction_reference, amount_cents=purchase.amount_cents)
            return True, "flutterwave", str(result.get("id") or purchase.transaction_reference), ""
        except ValueError as exc:
            return False, "flutterwave", "", str(exc)
    if purchase.payment_method == "stripe":
        return False, "stripe", "", "Stripe refund integration is not yet implemented in apps.billing."
    return False, purchase.payment_method or "unknown", "", "No refundable payment record was found for this purchase."


def refund_purchase(purchase: "BookPurchase", *, actor, reason: str) -> "BookPurchase":
    if purchase.status != BookPurchase.STATUS_PAID:
        raise ValidationError("Only a paid purchase can be refunded.")

    success, provider, provider_ref, failure_reason = _execute_book_refund(purchase)
    if not success:
        raise ValidationError(failure_reason or "Unable to refund this purchase with the payment provider.")

    purchase.status = BookPurchase.STATUS_REFUNDED
    purchase.refunded_at = timezone.now()
    purchase.refund_reason = reason
    purchase.refund_provider = provider
    purchase.refund_provider_ref = provider_ref
    purchase.metadata = {**(purchase.metadata or {}), "refunded_by_user_id": str(getattr(actor, "id", ""))}
    purchase.save(update_fields=[
        "status", "refunded_at", "refund_reason", "refund_provider", "refund_provider_ref", "metadata", "updated_at",
    ])

    revoke_entitlement(purchase.book, purchase.buyer, reason=f"Purchase refunded: {reason}".strip(": "))

    royalty = BookRoyaltyRecord.objects.filter(purchase=purchase, status=BookRoyaltyRecord.STATUS_EARNED).first()
    if royalty:
        royalty.status = BookRoyaltyRecord.STATUS_REVERSED
        royalty.reversed_at = timezone.now()
        royalty.save(update_fields=["status", "reversed_at", "updated_at"])

    return purchase


def user_has_reading_access(book: Book, user) -> bool:
    if user is not None and book.owner_id == getattr(user, "id", None):
        return True
    if book.is_free and book.is_publicly_visible():
        return True
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return BookEntitlement.objects.filter(book=book, user=user, revoked_at__isnull=True).exists()


# ───────────────────────── Library, bookmarks, highlights, notes ───────────

def _chapter_for_block(chapter, block_id: str) -> dict | None:
    for block in chapter.content_blocks:
        if isinstance(block, dict) and block.get("id") == block_id:
            return block
    return None


def save_book(book: Book, user) -> "BookLibraryItem":
    """Adds a book to the user's Saved library. Idempotent — a retried
    request just returns the existing row, never a duplicate."""
    if not user_has_reading_access(book, user) and not book.is_publicly_visible():
        # A book the user has no access to and isn't even public yet
        # (e.g. someone else's unpublished draft) can't be saved — no
        # information leak about its existence via a 201 vs 403 either,
        # so this mirrors the same 404 the detail endpoint would give.
        from django.http import Http404

        raise Http404
    item, _ = BookLibraryItem.objects.get_or_create(book=book, user=user)
    return item


def unsave_book(book: Book, user) -> None:
    BookLibraryItem.objects.filter(book=book, user=user).delete()


def toggle_chapter_bookmark(chapter: "BookChapter", user, *, label: str = "") -> tuple["BookChapterBookmark | None", bool]:
    """Returns (bookmark_or_None, created). Toggling an existing bookmark
    removes it (returns (None, False)); toggling a non-existent one
    creates it (returns (bookmark, True)) — matches the mobile "tap to
    bookmark / tap again to remove" UX in one idempotent call."""
    existing = BookChapterBookmark.objects.filter(chapter=chapter, user=user).first()
    if existing:
        existing.delete()
        return None, False
    bookmark = BookChapterBookmark.objects.create(book=chapter.book, chapter=chapter, user=user, label=label[:120])
    return bookmark, True


def create_highlight(
    chapter: "BookChapter", user, *, block_id: str, start_offset: int, end_offset: int, color: str = "yellow", note: str = "",
) -> "BookHighlight":
    if not user_has_reading_access(chapter.book, user):
        raise PermissionDenied("You do not have access to this book.")
    block = _chapter_for_block(chapter, block_id)
    if block is None:
        raise ValidationError("That passage no longer exists in this chapter — it may have been edited since you read it.")
    block_text = str(block.get("text", ""))
    start_offset = max(0, min(start_offset, len(block_text)))
    end_offset = max(start_offset, min(end_offset, len(block_text)))
    snapshot = block_text[start_offset:end_offset] or block_text
    if len(snapshot) > MAX_HIGHLIGHT_CHARS:
        snapshot = snapshot[:MAX_HIGHLIGHT_CHARS].rstrip() + "…"
    # get_or_create on the model's own unique_together — a retried
    # identical highlight request is a no-op, not a duplicate row.
    highlight, _ = BookHighlight.objects.get_or_create(
        user=user, chapter=chapter, block_id=block_id, start_offset=start_offset, end_offset=end_offset,
        defaults={"book": chapter.book, "color": color, "highlighted_text": snapshot, "note": note},
    )
    return highlight


def delete_highlight(highlight_id, user) -> None:
    BookHighlight.objects.filter(id=highlight_id, user=user).delete()


def update_highlight(highlight: "BookHighlight", user, *, color: str | None = None, note: str | None = None) -> "BookHighlight":
    """Lets a reader change their own highlight's color and/or edit its
    attached comment — the only two fields a reader can revise after the
    fact; the anchor itself (block_id/offsets/highlighted_text snapshot)
    is immutable, matching create_highlight's own idempotent-by-exact-span
    design (changing the anchor is "create a new highlight", not "edit
    this one")."""
    if highlight.user_id != user.id:
        raise PermissionDenied("You do not own this highlight.")
    update_fields = []
    if color is not None:
        if color not in dict(BookHighlight.COLOR_CHOICES):
            raise ValidationError(f"'{color}' is not a supported highlight color.")
        highlight.color = color
        update_fields.append("color")
    if note is not None:
        highlight.note = note.strip()
        update_fields.append("note")
    if update_fields:
        highlight.save(update_fields=[*update_fields, "updated_at"])
    return highlight


def resolve_highlight_anchor(highlight: "BookHighlight") -> dict:
    """Checks whether a saved highlight's anchor still points at the
    passage it was created against — used by the Highlights & Comments
    Center's "jump to this passage" navigation (never silently jump to
    the wrong sentence after the author has edited the chapter).
    Returns {"resolved": bool, "current_text": str | None}."""
    block = _chapter_for_block(highlight.chapter, highlight.block_id)
    if block is None:
        return {"resolved": False, "current_text": None}
    block_text = str(block.get("text", ""))
    current_slice = block_text[highlight.start_offset:highlight.end_offset]
    # The snapshot is truncated at MAX_HIGHLIGHT_CHARS with an ellipsis
    # for long spans (see create_highlight) — compare only the
    # unambiguous, un-truncated prefix in that case.
    snapshot = highlight.highlighted_text
    comparable_snapshot = snapshot[:-1] if snapshot.endswith("…") else snapshot
    resolved = bool(comparable_snapshot) and current_slice.startswith(comparable_snapshot)
    return {"resolved": resolved, "current_text": current_slice or None}


# ───────────────────────────────── Reviews ──────────────────────────────────

def recompute_book_rating(book: Book) -> Book:
    """Recomputes (never increments) average_rating/review_count straight
    from the live BookReview rows — called after every create/update/
    delete, including a moderation takedown deleting someone else's review
    out from under this book, so the aggregate can never silently drift
    from what's actually in the table."""
    from django.db.models import Avg, Count

    agg = BookReview.objects.filter(book=book).aggregate(avg=Avg("rating"), count=Count("id"))
    book.average_rating = round(agg["avg"] or 0, 2)
    book.review_count = agg["count"] or 0
    book.save(update_fields=["average_rating", "review_count", "updated_at"])
    return book


def create_or_update_review(book: Book, user, *, rating: int, review_text: str = "") -> "BookReview":
    if not user_has_reading_access(book, user):
        raise PermissionDenied("Only readers with access to this book can review it.")
    if rating not in (1, 2, 3, 4, 5):
        raise ValidationError("Rating must be between 1 and 5.")
    review, _ = BookReview.objects.update_or_create(
        book=book, user=user, defaults={"rating": rating, "review_text": (review_text or "").strip()},
    )
    recompute_book_rating(book)
    return review


def delete_review(review_id, user) -> None:
    review = BookReview.objects.filter(id=review_id, user=user).first()
    if not review:
        return
    book = review.book
    review.delete()
    recompute_book_rating(book)


def report_review(review: "BookReview", actor, *, reason: str) -> None:
    from apps.moderation.models import Flag

    Flag.objects.create(
        source="USER",
        target_type="book_review",
        target_id=review.id,
        reporter_id=actor.id,
        reason=reason.strip() or "Reported as inappropriate.",
        severity="LOW",
        status="PENDING",
    )


def create_note(
    chapter: "BookChapter | None", book: Book, user, *, text: str, block_id: str = "", client_request_id: str = "",
) -> "BookNote":
    if not user_has_reading_access(book, user):
        raise PermissionDenied("You do not have access to this book.")
    if not text or not text.strip():
        raise ValidationError("Note text cannot be empty.")
    if client_request_id:
        # Idempotent replay of the exact same create action — returns the
        # already-created note instead of a duplicate.
        existing = BookNote.objects.filter(user=user, client_request_id=client_request_id).first()
        if existing:
            return existing
    return BookNote.objects.create(
        book=book, chapter=chapter, user=user, block_id=block_id, text=text.strip(), client_request_id=client_request_id,
    )


def update_note(note: "BookNote", user, *, text: str) -> "BookNote":
    if note.user_id != user.id:
        raise PermissionDenied("You do not own this note.")
    if not text or not text.strip():
        raise ValidationError("Note text cannot be empty.")
    note.text = text.strip()
    note.save(update_fields=["text", "updated_at"])
    return note


def delete_note(note_id, user) -> None:
    BookNote.objects.filter(id=note_id, user=user).delete()


def get_personal_library(user) -> dict:
    """Aggregates the four personal-library buckets in one call. Books
    that have since been suspended/removed/deleted are handled
    gracefully: a deleted Book cascades its BookLibraryItem/bookmark/
    progress rows away automatically (FK on_delete=CASCADE), and a
    suspended-but-not-deleted book still appears with its current
    (non-published) status visible, rather than silently disappearing or
    erroring — the mobile/website client decides how to render that."""
    saved_qs = (
        BookLibraryItem.objects.filter(user=user).select_related("book").order_by("-created_at")
    )
    progress_qs = BookReadingProgress.objects.filter(user=user).select_related("book").order_by("-last_read_at")
    bookmarked_book_ids = (
        BookChapterBookmark.objects.filter(user=user).values_list("book_id", flat=True).distinct()
    )
    bookmarked_qs = Book.objects.filter(id__in=list(bookmarked_book_ids))

    currently_reading = [p.book for p in progress_qs if not p.is_completed]
    completed = [p.book for p in progress_qs if p.is_completed]
    return {
        "saved": [item.book for item in saved_qs],
        "currently_reading": currently_reading,
        "completed": completed,
        "bookmarked": list(bookmarked_qs),
    }


# ───────────────────────── Manuscript upload / conversion ──────────────────

def create_manuscript(book: Book, *, uploaded_by, filename: str, content_type: str, data: bytes) -> "BookManuscript":
    from django.core.files.base import ContentFile

    from .conversion import ManuscriptValidationError, sha256_hex, validate_upload
    from .models import BookManuscript

    if book.owner_id != uploaded_by.id:
        raise PermissionDenied("You do not own this book.")
    if book.status not in {BookStatus.DRAFT, BookStatus.CONVERSION_FAILED, BookStatus.CORRECTIONS_REQUESTED}:
        raise ValidationError(f"Cannot upload a new manuscript while the book is in status '{book.status}'.")

    try:
        fmt = validate_upload(filename=filename, content_type=content_type, data=data)
    except ManuscriptValidationError as exc:
        raise ValidationError({"detail": str(exc), "code": exc.code})

    manuscript = BookManuscript.objects.create(
        book=book,
        uploaded_by=uploaded_by,
        original_filename=filename[:255],
        declared_format=fmt,
        detected_mime_type=content_type or "",
        file_size_bytes=len(data),
        sha256=sha256_hex(data),
        status=BookManuscript.STATUS_UPLOADED,
    )
    manuscript.original_file.save(f"{manuscript.id}.{fmt}", ContentFile(data), save=True)
    book.status = BookStatus.PROCESSING
    book.save(update_fields=["status", "updated_at"])
    return manuscript


def process_manuscript(manuscript, *, force: bool = False) -> "BookConversionJob":
    """Runs conversion synchronously (dispatched via apps.books.tasks under
    Celery in production; CELERY_TASK_ALWAYS_EAGER in dev/test runs this
    inline). Safe to call more than once for the same manuscript — a prior
    successful job with author-edited chapters (word_count diverges from
    the job's own chapters_detected baseline is NOT tracked precisely here,
    so the conservative rule is: without force=True, re-running conversion
    on a book that already has chapters is refused outright, preserving
    whatever the author has already corrected."""
    from .conversion import ManuscriptValidationError, convert_manuscript, estimate_reading_minutes
    from .models import BookChapter, BookConversionJob

    book = manuscript.book
    if book.chapters.exists() and not force:
        raise ValidationError(
            "This book already has converted/edited chapters. Re-running conversion would discard author "
            "corrections — pass force=true to explicitly replace them."
        )

    attempt_number = manuscript.conversion_jobs.count() + 1
    job = BookConversionJob.objects.create(manuscript=manuscript, attempt_number=attempt_number, status=BookConversionJob.STATUS_RUNNING, started_at=timezone.now())
    manuscript.status = manuscript.STATUS_PROCESSING
    manuscript.save(update_fields=["status", "updated_at"])

    try:
        manuscript.original_file.open("rb")
        data = manuscript.original_file.read()
        manuscript.original_file.close()
        result = convert_manuscript(fmt=manuscript.declared_format, data=data)
    except ManuscriptValidationError as exc:
        job.status = BookConversionJob.STATUS_FAILED
        job.error_message = str(exc)
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error_message", "finished_at"])
        manuscript.status = manuscript.STATUS_FAILED
        manuscript.error_message = str(exc)
        manuscript.save(update_fields=["status", "error_message", "updated_at"])
        book.status = BookStatus.CONVERSION_FAILED
        book.save(update_fields=["status", "updated_at"])
        _notify(user_id=book.owner_id, notif_type="book_conversion_failed", context={"book_id": str(book.id), "title": book.title, "error": str(exc)})
        return job
    except Exception as exc:  # pragma: no cover - unexpected parser crash
        logger.exception("book_conversion_crashed", extra={"manuscript_id": str(manuscript.id)})
        job.status = BookConversionJob.STATUS_FAILED
        job.error_message = f"Unexpected conversion error: {exc}"
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error_message", "finished_at"])
        manuscript.status = manuscript.STATUS_FAILED
        manuscript.save(update_fields=["status", "updated_at"])
        book.status = BookStatus.CONVERSION_FAILED
        book.save(update_fields=["status", "updated_at"])
        return job

    with transaction.atomic():
        book.image_assets.all().delete()
        book.chapters.all().delete()
        for idx, chapter in enumerate(result.chapters, start=1):
            blocks = chapter.blocks
            for block in blocks:
                if not isinstance(block, dict) or block.get("type") != "image":
                    continue
                raw = block.pop("_raw_bytes", None)
                content_type = block.pop("_content_type", "")
                asset = create_or_get_image_asset(book, data=raw or b"", content_type=content_type, uploaded_by=manuscript.uploaded_by) if raw else None
                if asset:
                    block["asset_id"] = str(asset.id)
                    block["width"] = asset.width
                    block["height"] = asset.height
                    block["extraction_status"] = "extracted"
                else:
                    block["asset_id"] = None
                    block.setdefault("extraction_status", "missing")
            BookChapter.objects.create(
                book=book, order=idx, title=chapter.title,
                content_blocks=assign_block_ids(blocks), word_count=chapter.word_count,
                source_page_range=chapter.source_page_range,
            )
        book.word_count = result.total_word_count
        book.estimated_reading_minutes = estimate_reading_minutes(result.total_word_count)
        book.status = BookStatus.DRAFT
        book.save(update_fields=["word_count", "estimated_reading_minutes", "status", "updated_at"])

    job.status = BookConversionJob.STATUS_SUCCEEDED
    job.warnings = result.warnings
    job.confidence_score = result.confidence_score
    job.chapters_detected = len(result.chapters)
    job.images_extracted = result.images_extracted
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "warnings", "confidence_score", "chapters_detected", "images_extracted", "finished_at"])

    manuscript.status = manuscript.STATUS_DONE
    manuscript.save(update_fields=["status", "updated_at"])
    _notify(user_id=book.owner_id, notif_type="book_conversion_completed", context={"book_id": str(book.id), "title": book.title, "chapters": len(result.chapters)})
    return job


# ───────────────────────── Manuscript image placements ─────────────────────
#
# An "image" content block is detected during conversion (see
# apps.books.conversion) at its real position in the manuscript's reading
# order — never collected at the start/end of the chapter. Its stable
# block `id` (assigned by assign_block_ids, same as every other block
# type) is the one durable reference an author's later upload/replace
# action targets; the human-visible "Image N" numbering
# (number_image_placements below) is recomputed on every read, never
# stored, so reordering/inserting chapters can never desync it from the
# real blocks.

MAX_IMAGE_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
ACCEPTED_IMAGE_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}


def create_or_get_image_asset(book: Book, *, data: bytes, content_type: str = "", uploaded_by=None, source: str = BookImageAsset.SOURCE_EXTRACTED) -> "BookImageAsset | None":
    """Validates actual image bytes (never trusts a client-supplied
    filename/MIME alone — Pillow must be able to decode it), dedups by
    content hash within this book, and returns the asset row. Returns
    None for genuinely empty/undecodable input rather than raising, so a
    best-effort conversion-time extraction failure degrades to a
    "missing, author must supply" placement instead of aborting the
    whole conversion."""
    if not data:
        return None
    if len(data) > MAX_IMAGE_FILE_SIZE_BYTES:
        raise ValidationError(f"Image exceeds the {MAX_IMAGE_FILE_SIZE_BYTES // (1024 * 1024)} MB limit.")
    try:
        from io import BytesIO

        from PIL import Image as PILImage

        img = PILImage.open(BytesIO(data))
        img.verify()
        # verify() invalidates the file handle for further use — reopen
        # to actually read dimensions.
        img = PILImage.open(BytesIO(data))
        width, height = img.size
        detected_format = (img.format or "").lower()
    except Exception:
        return None
    detected_content_type = {
        "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp",
    }.get(detected_format, content_type)
    if detected_content_type not in ACCEPTED_IMAGE_CONTENT_TYPES:
        return None

    digest = hashlib.sha256(data).hexdigest()
    existing = BookImageAsset.objects.filter(book=book, sha256=digest).first()
    if existing:
        return existing

    from django.core.files.base import ContentFile

    ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[detected_content_type]
    asset = BookImageAsset(
        book=book, sha256=digest, content_type=detected_content_type,
        file_size_bytes=len(data), width=width, height=height,
        source=source, uploaded_by=uploaded_by,
    )
    asset.file.save(f"{digest}.{ext}", ContentFile(data), save=False)
    asset.save()
    return asset


def _iter_image_blocks(book: Book):
    """Yields (chapter, block_index, block) for every "image" content
    block across the book, in book reading order (chapter order, then
    block position) — the single source of truth both the numbered
    author-review list and the publish-time validation below iterate."""
    for chapter in book.chapters.order_by("order"):
        blocks = chapter.content_blocks
        for idx, block in enumerate(blocks):
            if isinstance(block, dict) and block.get("type") == "image":
                yield chapter, idx, block


def list_image_placements(book: Book) -> list[dict]:
    """Author-review listing: every image placement in reading order,
    numbered 1..N for display (see module note above on why the number
    itself is never persisted)."""
    placements = []
    asset_ids = set()
    rows = list(_iter_image_blocks(book))
    for _, _, block in rows:
        if block.get("asset_id"):
            asset_ids.add(block["asset_id"])
    assets = {str(a.id): a for a in BookImageAsset.objects.filter(id__in=asset_ids)}

    for number, (chapter, idx, block) in enumerate(rows, start=1):
        asset = assets.get(str(block.get("asset_id") or ""))
        placements.append({
            "number": number,
            "chapter_stable_id": str(chapter.stable_id),
            "chapter_title": chapter.title,
            "block_id": block.get("id"),
            "extraction_status": block.get("extraction_status", "missing"),
            "source_page": block.get("source_page"),
            "caption": block.get("caption", ""),
            "alt_text": block.get("alt_text", ""),
            "nearby_text": block.get("nearby_text", ""),
            "width": asset.width if asset else block.get("width", 0),
            "height": asset.height if asset else block.get("height", 0),
            "asset_id": str(asset.id) if asset else None,
        })
    return placements


def assign_image_to_placement(book: Book, block_id: str, asset: "BookImageAsset", *, user) -> dict:
    """Attaches `asset` to the stable block `block_id` — not appended to
    a gallery, not attached to the end of the chapter; the placement's
    position in content_blocks is untouched, only its asset_id/status
    change. Refuses outright while the book is mid-review, mirroring
    AuthorChapterUpdateView's own guard, and correctly re-queues an
    already-published book for review rather than silently mutating live
    content (unlike AuthorChapterUpdateView, which has a known gap here —
    see docs/KIS_BOOKS_OPERATIONS.md)."""
    if book.owner_id != user.id:
        raise PermissionDenied("You do not own this book.")
    if book.status in {BookStatus.PENDING_REVIEW, BookStatus.UNDER_REVIEW}:
        raise ValidationError("This book is currently under review and cannot be edited.")

    for chapter, idx, block in _iter_image_blocks(book):
        if block.get("id") == block_id:
            blocks = chapter.content_blocks
            blocks[idx] = {
                **block,
                "asset_id": str(asset.id),
                "width": asset.width,
                "height": asset.height,
                "extraction_status": "extracted",
            }
            chapter.content_blocks = blocks
            chapter.save(update_fields=["encrypted_content", "updated_at"])
            was_published = book.status == BookStatus.PUBLISHED
            if was_published:
                book.content_version += 1
                book.status = BookStatus.PENDING_REVIEW
                book.submitted_at = timezone.now()
                book.save(update_fields=["content_version", "status", "submitted_at", "updated_at"])
            return {
                "block_id": block_id, "asset_id": str(asset.id), "extraction_status": "extracted",
                "width": asset.width, "height": asset.height,
            }
    raise ValidationError("That image placement no longer exists in this book.")


def unresolved_image_placements(book: Book) -> list[str]:
    """Block ids of every image placement still missing a real image —
    the publish-time gate (see submit_for_review). "missing"/
    "author_required" both count; only "extracted" is resolved."""
    return [
        block.get("id") for _, _, block in _iter_image_blocks(book)
        if block.get("extraction_status") != "extracted"
    ]


# create_notification(type, ...) stores whatever title/body it's given —
# it does NOT render one from `type` alone without a pre-registered
# NotificationTemplate (apps.notifications.services.create_notification),
# and none of these book notification types have one. Previously this
# called create_notification with only `context`, which silently produced
# notification rows with empty title/body — nothing wrong in the Response,
# but the author's actual in-app notification was blank text. One line of
# human-readable copy per type here fixes that for all current call sites.
_NOTIFICATION_COPY = {
    "book_approved": lambda c: ("Book approved", f"\"{c.get('title', 'Your book')}\" was approved and is now published."),
    "book_approved_held": lambda c: (
        "Book approved — publishing on hold",
        f"\"{c.get('title', 'Your book')}\" was approved, but publishing is on hold: your business payout setup "
        "is no longer eligible. Resolve this, then contact support to publish.",
    ),
    "book_rejected": lambda c: ("Book rejected", f"\"{c.get('title', 'Your book')}\" was not approved. Reason: {c.get('reason') or 'See moderation notes.'}"),
    "book_corrections_requested": lambda c: (
        "Corrections requested", f"\"{c.get('title', 'Your book')}\" needs changes before it can be published: {c.get('notes') or 'See moderation notes.'}"
    ),
    "book_suspended": lambda c: ("Book suspended", f"\"{c.get('title', 'Your book')}\" has been suspended from the marketplace."),
    "book_restored": lambda c: ("Book restored", f"\"{c.get('title', 'Your book')}\" has been restored."),
    "book_conversion_failed": lambda c: ("Manuscript conversion failed", f"We couldn't convert the manuscript for \"{c.get('title', 'your book')}\": {c.get('error') or 'See conversion details.'}"),
    "book_conversion_completed": lambda c: ("Manuscript converted", f"\"{c.get('title', 'Your book')}\" was converted into {c.get('chapters', 0)} chapter(s). Review it before submitting."),
    "book_account_restricted": lambda c: (
        "Publishing restriction applied",
        f"A {str(c.get('restriction_type', 'restriction')).replace('_', ' ')} has been applied to your account."
        + (f" Ends {c.get('ends_at')}." if c.get("ends_at") else " This restriction has no set end date."),
    ),
}


def _notify(*, user_id, notif_type: str, context: dict) -> None:
    try:
        from apps.notifications.services import create_notification

        title, body = "", ""
        copy_fn = _NOTIFICATION_COPY.get(notif_type)
        if copy_fn:
            title, body = copy_fn(context)
        create_notification(user_id, notif_type, context=context, title=title, body=body)
    except Exception:
        logger.warning("book_notification_failed", extra={"notif_type": notif_type, "user_id": str(user_id)})


# ─────────────────────────── Offline downloads ──────────────────────────────

OFFLINE_LICENSE_VALIDITY_DAYS = 14
# Conservative cap so a pathological book can't produce an unbounded
# offline package — same spirit as apps.books.conversion.MAX_FILE_SIZE_BYTES
# for the source manuscript, applied to the combined plaintext this time.
OFFLINE_PACKAGE_MAX_PLAINTEXT_BYTES = 20 * 1024 * 1024  # 20MB


def issue_or_renew_offline_license(book: Book, user, *, device_id: str) -> tuple["BookOfflineLicense", dict]:
    """Authorizes (book, user, device_id) for offline reading and builds
    the encrypted package in the same call. Server-authoritative: re-runs
    the exact same user_has_reading_access() check every other read path
    uses — a client can never obtain a package merely by asserting it
    already paid, and this function never accepts a client-supplied
    is_paid/is_purchased/is_authorized flag of any kind.

    The package's ciphertext is never persisted server-side — only the
    WRAPPED DEK and bookkeeping metadata live on BookOfflineLicense. A
    retried/interrupted download just re-encrypts the same plaintext
    under the same DEK with fresh nonces (safe and standard for AES-GCM)
    rather than needing a separate "resume" protocol."""
    from . import offline_crypto
    from .models import BookOfflineLicense

    if not user_has_reading_access(book, user):
        raise PermissionDenied("You do not have access to this book.")
    if not device_id:
        raise ValidationError("device_id is required.")

    chapters = list(book.chapters.order_by("order"))
    if not chapters:
        raise ValidationError("This book has no chapters to download yet.")

    existing = BookOfflineLicense.objects.filter(book=book, user=user, device_id=device_id).first()
    reuse_existing_dek = (
        existing is not None
        and existing.status == BookOfflineLicense.STATUS_ACTIVE
        and existing.book_content_version == book.content_version
    )

    if reuse_existing_dek:
        dek = offline_crypto.unwrap_package_dek(existing.wrapped_package_dek)
        license_row = existing
        license_row.expires_at = timezone.now() + timedelta(days=OFFLINE_LICENSE_VALIDITY_DAYS)
        license_row.last_renewed_at = timezone.now()
        license_row.save(update_fields=["expires_at", "last_renewed_at", "updated_at"])
    else:
        dek = offline_crypto.generate_package_dek()
        next_version = (existing.package_version + 1) if existing else 1
        license_row, _ = BookOfflineLicense.objects.update_or_create(
            book=book, user=user, device_id=device_id,
            defaults={
                "status": BookOfflineLicense.STATUS_ACTIVE,
                "package_version": next_version,
                "book_content_version": book.content_version,
                "wrapped_package_dek": offline_crypto.wrap_package_dek(dek),
                "issued_at": timezone.now(),
                "last_renewed_at": timezone.now(),
                "expires_at": timezone.now() + timedelta(days=OFFLINE_LICENSE_VALIDITY_DAYS),
                "revoked_at": None,
                "revoked_reason": "",
            },
        )

    total_bytes = 0
    chapter_entries = []
    encrypted_chapters = {}
    referenced_asset_ids: list[str] = []
    for chapter in chapters:
        blocks = chapter.content_blocks
        total_bytes += len(json.dumps(blocks).encode())
        if total_bytes > OFFLINE_PACKAGE_MAX_PLAINTEXT_BYTES:
            raise ValidationError("This book is too large for an offline package.")
        part_id = f"chapter:{chapter.stable_id}:{chapter.order}"
        encrypted_chapters[str(chapter.stable_id)] = offline_crypto.encrypt_part(
            dek, blocks, book_id=str(book.id), license_id=str(license_row.id),
            package_version=license_row.package_version, part=part_id,
        )
        chapter_entries.append({
            "stable_id": str(chapter.stable_id), "order": chapter.order, "title": chapter.title,
            "word_count": chapter.word_count, "content_hash": offline_crypto.content_hash(blocks),
        })
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "image" and block.get("asset_id"):
                if block["asset_id"] not in referenced_asset_ids:
                    referenced_asset_ids.append(block["asset_id"])

    # Every required image is bundled, encrypted as its own AEAD part
    # exactly like a chapter (same per-part nonce/tag/AAD scheme — see
    # offline_crypto.encrypt_part's docstring) — never a plaintext export,
    # never a separate/weaker scheme for images. A repeated image (two
    # placements, one underlying BookImageAsset — see
    # create_or_get_image_asset's dedup) is bundled exactly once here;
    # both placements' blocks already point at the same asset_id, so the
    # client resolves both from this one part.
    image_assets = {str(a.id): a for a in BookImageAsset.objects.filter(id__in=referenced_asset_ids)}
    image_entries = []
    encrypted_images = {}
    for asset_id in referenced_asset_ids:
        asset = image_assets.get(asset_id)
        if asset is None:
            # A placement referenced an asset id that no longer exists
            # (e.g. deleted out from under it) — skip it; the on-device
            # reader already renders a graceful "missing" fallback for
            # any image block it can't resolve, online or offline.
            continue
        try:
            asset.file.open("rb")
            raw = asset.file.read()
        except Exception:
            logger.warning("book_offline_image_read_failed", extra={"book_id": str(book.id), "asset_id": asset_id})
            continue
        finally:
            try:
                asset.file.close()
            except Exception:
                pass
        if not raw or len(raw) > MAX_IMAGE_FILE_SIZE_BYTES:
            # Malformed/empty or (shouldn't happen given upload-time
            # validation, but defense in depth) oversized — fail this one
            # image safely rather than the whole package.
            logger.warning("book_offline_image_rejected", extra={"book_id": str(book.id), "asset_id": asset_id, "size": len(raw or b"")})
            continue
        encoded = base64.b64encode(raw).decode()
        total_bytes += len(encoded)
        if total_bytes > OFFLINE_PACKAGE_MAX_PLAINTEXT_BYTES:
            raise ValidationError("This book is too large for an offline package.")
        part_id = f"image:{asset_id}"
        encrypted_images[asset_id] = offline_crypto.encrypt_part(
            dek, encoded, book_id=str(book.id), license_id=str(license_row.id),
            package_version=license_row.package_version, part=part_id,
        )
        image_entries.append({
            "asset_id": asset_id, "content_type": asset.content_type,
            "width": asset.width, "height": asset.height, "content_hash": hashlib.sha256(encoded.encode()).hexdigest(),
        })

    manifest = {
        "book_id": str(book.id), "title": book.title, "author_name": book.author_name,
        "license_id": str(license_row.id), "package_version": license_row.package_version,
        "generated_at": timezone.now().isoformat(), "chapters": chapter_entries,
        # Additive/optional — a pre-image-feature on-device reader that
        # doesn't look for this key still decrypts/renders every chapter
        # exactly as before; a current reader treats a missing/empty
        # "images" key (an older cached package, or simply an image-free
        # book) as "no images to resolve", never an error.
        "images": image_entries,
    }
    encrypted_manifest = offline_crypto.encrypt_part(
        dek, manifest, book_id=str(book.id), license_id=str(license_row.id),
        package_version=license_row.package_version, part="manifest",
    )
    overall_hash = hashlib.sha256(json.dumps([c["content_hash"] for c in chapter_entries]).encode()).hexdigest()
    if license_row.content_hash != overall_hash:
        license_row.content_hash = overall_hash
        license_row.save(update_fields=["content_hash", "updated_at"])

    package = {
        "license_id": str(license_row.id),
        "book_id": str(book.id),
        "package_version": license_row.package_version,
        # Transmitted once, over the same TLS-protected authenticated
        # channel a JWT already travels over — never persisted unwrapped
        # server-side (see BookOfflineLicense.wrapped_package_dek) and
        # must be moved by the client into platform secure storage
        # (Keychain/Keystore), never written to disk beside the package.
        "package_dek": base64.b64encode(dek).decode(),
        "expires_at": license_row.expires_at.isoformat(),
        "manifest": encrypted_manifest,
        "chapters": encrypted_chapters,
        "images": encrypted_images,
    }
    logger.info("book_offline_package_issued", extra={
        "book_id": str(book.id), "user_id": str(user.id), "device_id": device_id,
        "package_version": license_row.package_version, "chapter_count": len(chapters),
        "image_count": len(encrypted_images),
    })
    return license_row, package


def verify_offline_license(book: Book, user, *, device_id: str) -> dict:
    """Lightweight renew/check — does NOT re-expose the DEK (the device
    already holds it in secure storage from issuance); just confirms the
    license is still valid and whether the on-device package is stale."""
    from .models import BookOfflineLicense

    license_row = BookOfflineLicense.objects.filter(book=book, user=user, device_id=device_id).first()
    if not license_row:
        return {"valid": False, "reason": "no_license"}
    if license_row.status != BookOfflineLicense.STATUS_ACTIVE:
        return {"valid": False, "reason": license_row.revoked_reason or "revoked"}
    if not user_has_reading_access(book, user):
        # Access can be lost without this row having been explicitly
        # revoked yet — re-check the real gate, not just the row's own
        # status flag, so a lapsed/refunded entitlement is caught here too.
        return {"valid": False, "reason": "access_no_longer_authorized"}
    license_row.expires_at = timezone.now() + timedelta(days=OFFLINE_LICENSE_VALIDITY_DAYS)
    license_row.last_renewed_at = timezone.now()
    license_row.save(update_fields=["expires_at", "last_renewed_at", "updated_at"])
    return {
        "valid": True,
        "expires_at": license_row.expires_at.isoformat(),
        "package_version": license_row.package_version,
        "update_available": license_row.book_content_version != book.content_version,
    }


def revoke_offline_license(book: Book, user, *, device_id: str | None = None, reason: str = "") -> int:
    """Revokes one device's license, or every device's license for this
    (book, user) when device_id is omitted (used by refund/entitlement
    revocation — every copy this user holds must stop being renewable).
    Returns the number of rows revoked. This can only ever stop the NEXT
    renewal/verify call from succeeding — it cannot reach an already-
    offline device (see BookOfflineLicense's docstring)."""
    from .models import BookOfflineLicense

    qs = BookOfflineLicense.objects.filter(book=book, user=user, status=BookOfflineLicense.STATUS_ACTIVE)
    if device_id:
        qs = qs.filter(device_id=device_id)
    count = qs.update(status=BookOfflineLicense.STATUS_REVOKED, revoked_at=timezone.now(), revoked_reason=reason)
    if count:
        logger.info("book_offline_license_revoked", extra={
            "book_id": str(book.id), "user_id": str(user.id), "device_id": device_id or "all", "reason": reason, "count": count,
        })
    return count


def revoke_all_offline_licenses_for_book(book: Book, *, reason: str = "") -> int:
    """Used when a book itself is taken down (moderation block/remove) —
    every reader's offline copy's NEXT renewal fails, regardless of who
    they are."""
    from .models import BookOfflineLicense

    count = BookOfflineLicense.objects.filter(book=book, status=BookOfflineLicense.STATUS_ACTIVE).update(
        status=BookOfflineLicense.STATUS_REVOKED, revoked_at=timezone.now(), revoked_reason=reason,
    )
    if count:
        logger.info("book_offline_licenses_revoked_bulk", extra={"book_id": str(book.id), "reason": reason, "count": count})
    return count
