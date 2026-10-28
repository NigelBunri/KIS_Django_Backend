import base64
import os
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient

from apps.books import services
from apps.books.conversion import ManuscriptValidationError, validate_upload
from apps.books.models import (
    Book,
    BookEntitlement,
    BookHighlight,
    BookImageAsset,
    BookPublishingRestriction,
    BookStatus,
    ChristianPublishingPolicyVersion,
)
from apps.moderation.models import Flag

User = get_user_model()


def make_user(suffix="1", **kwargs):
    kwargs.setdefault("country", "US")
    return User.objects.create_user(
        phone=f"+1555000{suffix.zfill(4)}", password="Test1234!", username=f"user{suffix}", **kwargs
    )


def make_scanned_pdf_bytes(lines: list[str]) -> bytes:
    """Builds a genuinely scanned-looking PDF (an image with no text
    operators at all — pypdf.extract_text() returns '' on it) by
    rendering text to a pixmap and re-embedding it as a plain image. Used
    to prove OCR actually runs and recognizes real text, not mocked."""
    import fitz  # PyMuPDF — already a hard dependency of apps.books.conversion

    render_doc = fitz.open()
    page = render_doc.new_page()
    y = 72
    for line in lines:
        page.insert_text((72, y), line, fontsize=16)
        y += 28
    pix = page.get_pixmap(dpi=200)
    png_bytes = pix.tobytes("png")
    render_doc.close()

    scanned_doc = fitz.open()
    img_doc = fitz.open(stream=png_bytes, filetype="png")
    rect = img_doc[0].rect
    img_doc.close()
    scanned_page = scanned_doc.new_page(width=rect.width, height=rect.height)
    scanned_page.insert_image(rect, stream=png_bytes)
    out = scanned_doc.tobytes()
    scanned_doc.close()
    return out


def make_policy():
    # get_or_create, not create() — mirrors apps.books.services.current_policy_version's
    # own lookup (is_current=True, any version string) and keeps this helper
    # correct even if a prior run (or --keepdb reuse) already left a "1.0"
    # row in the test database uncommitted-rolled-back for some other reason;
    # a hardcoded create() here previously caused a cross-run
    # IntegrityError on the unique `version` constraint.
    policy, _ = ChristianPublishingPolicyVersion.objects.get_or_create(
        version="1.0", defaults={"body_markdown": "Christian books only.", "is_current": True},
    )
    if not policy.is_current:
        policy.is_current = True
        policy.save(update_fields=["is_current"])
    return policy


def make_verified_shop(owner):
    from apps.commerce.models import Shop

    return Shop.objects.create(
        owner=owner, name=f"Shop {owner.id}", slug=f"shop-{owner.id}",
        verification_status="VERIFIED", stripe_charges_enabled=True,
    )


def make_book(owner, *, is_free=True, price_cents=0, with_chapter=True, status=BookStatus.DRAFT):
    book = Book.objects.create(
        owner=owner, title="Grace Abounding", author_name="Test Author",
        is_free=is_free, price_cents=price_cents, status=status,
    )
    if with_chapter:
        from apps.books.models import BookChapter

        blocks = services.assign_block_ids([{"type": "paragraph", "text": "Hello there, reader of this chapter."}])
        BookChapter.objects.create(book=book, order=1, title="Chapter 1", content_blocks=blocks, word_count=6)
    return book


class UploadValidationTests(TestCase):
    def test_accepts_valid_txt(self):
        fmt = validate_upload(filename="book.txt", content_type="text/plain", data=b"Hello world " * 20)
        self.assertEqual(fmt, "txt")

    def test_rejects_unsupported_extension(self):
        with self.assertRaises(ManuscriptValidationError) as ctx:
            validate_upload(filename="book.epub", content_type="application/epub+zip", data=b"x")
        self.assertEqual(ctx.exception.code, "unsupported_format")

    def test_rejects_spoofed_pdf_signature(self):
        # .pdf extension, but the bytes are not a real PDF — must be rejected
        # even though filename/content_type both claim PDF.
        with self.assertRaises(ManuscriptValidationError) as ctx:
            validate_upload(filename="book.pdf", content_type="application/pdf", data=b"NOT_A_REAL_PDF" * 10)
        self.assertEqual(ctx.exception.code, "signature_mismatch")

    def test_rejects_oversized_file(self):
        from apps.books import conversion

        data = b"a" * (conversion.MAX_FILE_SIZE_BYTES + 1)
        with self.assertRaises(ManuscriptValidationError) as ctx:
            validate_upload(filename="book.txt", content_type="text/plain", data=data)
        self.assertEqual(ctx.exception.code, "file_too_large")

    def test_rejects_mime_extension_mismatch(self):
        with self.assertRaises(ManuscriptValidationError) as ctx:
            validate_upload(filename="book.pdf", content_type="text/plain", data=b"%PDF-1.4 fake")
        self.assertEqual(ctx.exception.code, "mime_mismatch")


class ConversionPipelineTests(TestCase):
    def test_txt_manuscript_converts_to_chapters(self):
        user = make_user("2")
        book = Book.objects.create(owner=user, title="T", author_name="A", status=BookStatus.DRAFT)
        text = "CHAPTER ONE\n\nIn the beginning, grace was abundant and good.\n\nCHAPTER TWO\n\nThen the story continued onward truly.\n"
        manuscript = services.create_manuscript(book, uploaded_by=user, filename="book.txt", content_type="text/plain", data=text.encode())
        job = services.process_manuscript(manuscript)
        self.assertEqual(job.status, job.STATUS_SUCCEEDED)
        book.refresh_from_db()
        self.assertEqual(book.status, BookStatus.DRAFT)
        self.assertGreaterEqual(book.chapters.count(), 1)
        self.assertGreater(book.word_count, 0)

    def test_conversion_failure_sets_book_status(self):
        user = make_user("3")
        book = Book.objects.create(owner=user, title="T", author_name="A", status=BookStatus.DRAFT)
        # A PDF with a valid signature but garbage body — passes validate_upload's
        # magic-byte check but fails real parsing.
        manuscript = services.create_manuscript(book, uploaded_by=user, filename="book.pdf", content_type="application/pdf", data=b"%PDF-1.4\ngarbage not a real pdf structure")
        job = services.process_manuscript(manuscript)
        self.assertEqual(job.status, job.STATUS_FAILED)
        book.refresh_from_db()
        self.assertEqual(book.status, BookStatus.CONVERSION_FAILED)

    def test_reconversion_without_force_refuses_to_clobber_edited_chapters(self):
        user = make_user("4")
        book = make_book(user, with_chapter=True)
        manuscript = services.create_manuscript(book, uploaded_by=user, filename="x.txt", content_type="text/plain", data=b"Some words here indeed.")
        with self.assertRaises(ValidationError):
            services.process_manuscript(manuscript, force=False)


class PaidPublishingEligibilityTests(TestCase):
    def test_no_shop_means_ineligible(self):
        user = make_user("5")
        self.assertIsNone(services.eligible_shop_for_paid_publishing(user))

    def test_unverified_shop_means_ineligible(self):
        from apps.commerce.models import Shop

        user = make_user("6")
        Shop.objects.create(owner=user, name="S", slug="s6", verification_status="PENDING", stripe_charges_enabled=True)
        self.assertIsNone(services.eligible_shop_for_paid_publishing(user))

    def test_verified_shop_without_payout_rail_means_ineligible(self):
        from apps.commerce.models import Shop

        user = make_user("7")
        Shop.objects.create(owner=user, name="S", slug="s7", verification_status="VERIFIED")
        self.assertIsNone(services.eligible_shop_for_paid_publishing(user))

    def test_verified_and_payout_ready_shop_is_eligible(self):
        user = make_user("8")
        shop = make_verified_shop(user)
        self.assertEqual(services.eligible_shop_for_paid_publishing(user).id, shop.id)


class SubmissionAndModerationTests(TestCase):
    def setUp(self):
        make_policy()

    def test_submit_requires_policy_acknowledgment(self):
        user = make_user("9")
        book = make_book(user)
        book.rights_declared = True
        book.save()
        with self.assertRaises(ValidationError):
            services.submit_for_review(book, actor=user)

    def test_submit_requires_rights_declaration(self):
        user = make_user("10")
        book = make_book(user)
        services.acknowledge_policy(user=user, book=book)
        with self.assertRaises(ValidationError):
            services.submit_for_review(book, actor=user)

    def test_submit_requires_chapters(self):
        user = make_user("11")
        book = make_book(user, with_chapter=False)
        book.rights_declared = True
        book.save()
        services.acknowledge_policy(user=user, book=book)
        with self.assertRaises(ValidationError):
            services.submit_for_review(book, actor=user)

    def test_paid_submission_requires_eligible_shop(self):
        user = make_user("12")
        book = make_book(user, is_free=False, price_cents=999)
        book.rights_declared = True
        book.save()
        services.acknowledge_policy(user=user, book=book)
        with self.assertRaises(ValidationError):
            services.submit_for_review(book, actor=user)

    def test_free_submission_creates_pending_flag(self):
        user = make_user("13")
        book = make_book(user)
        book.rights_declared = True
        book.save()
        services.acknowledge_policy(user=user, book=book)
        book = services.submit_for_review(book, actor=user)
        self.assertEqual(book.status, BookStatus.PENDING_REVIEW)
        flag = Flag.objects.get(target_type="book", target_id=book.id)
        self.assertEqual(flag.status, "PENDING")

    def test_approve_publishes_free_book(self):
        user = make_user("14")
        reviewer = make_user("15", is_staff=True)
        book = make_book(user)
        book.rights_declared = True
        book.save()
        services.acknowledge_policy(user=user, book=book)
        book = services.submit_for_review(book, actor=user)
        book = services.apply_moderation_decision(book, action="approve", actor=reviewer)
        self.assertEqual(book.status, BookStatus.PUBLISHED)
        self.assertIsNotNone(book.published_at)

    def test_reject_records_reason_and_blocks_publication(self):
        user = make_user("16")
        reviewer = make_user("17", is_staff=True)
        book = make_book(user)
        book.rights_declared = True
        book.save()
        services.acknowledge_policy(user=user, book=book)
        book = services.submit_for_review(book, actor=user)
        book = services.apply_moderation_decision(book, action="reject", actor=reviewer, notes="Not aligned with policy.")
        self.assertEqual(book.status, BookStatus.REJECTED)
        self.assertEqual(book.rejection_reason, "Not aligned with policy.")

    def test_corrections_requested_preserves_chapters(self):
        user = make_user("18")
        reviewer = make_user("19", is_staff=True)
        book = make_book(user)
        book.rights_declared = True
        book.save()
        services.acknowledge_policy(user=user, book=book)
        book = services.submit_for_review(book, actor=user)
        book = services.apply_moderation_decision(book, action="request_corrections", actor=reviewer, notes="Fix title.")
        self.assertEqual(book.status, BookStatus.CORRECTIONS_REQUESTED)
        self.assertEqual(book.chapters.count(), 1)

    def test_block_suspends_published_book_without_revoking_existing_entitlements(self):
        user = make_user("20")
        reviewer = make_user("21", is_staff=True)
        buyer = make_user("22")
        book = make_book(user, status=BookStatus.PUBLISHED)
        book.published_at = timezone.now()
        book.save()
        services.grant_entitlement(book, buyer, source=BookEntitlement.SOURCE_FREE)
        book = services.apply_moderation_decision(book, action="block", actor=reviewer, notes="Policy concern.")
        self.assertEqual(book.status, BookStatus.SUSPENDED)
        self.assertTrue(BookEntitlement.objects.get(book=book, user=buyer).is_active)


class RestrictionEnforcementTests(TestCase):
    def test_active_publishing_suspension_blocks_publish(self):
        user = make_user("23")
        admin = make_user("24")
        services.apply_restriction(
            user=user, restriction_type=BookPublishingRestriction.TYPE_PUBLISHING_SUSPENDED,
            reason="Repeated policy violations.", imposed_by=admin,
        )
        with self.assertRaises(PermissionDenied):
            services.assert_can_publish(user)

    def test_account_ban_deactivates_user(self):
        user = make_user("25")
        admin = make_user("26")
        self.assertTrue(user.is_active)
        services.apply_restriction(
            user=user, restriction_type=BookPublishingRestriction.TYPE_ACCOUNT_BANNED,
            reason="Severe violation.", imposed_by=admin,
        )
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_temporary_suspension_expires_and_reinstates_account(self):
        user = make_user("27")
        admin = make_user("28")
        restriction = services.apply_restriction(
            user=user, restriction_type=BookPublishingRestriction.TYPE_ACCOUNT_SUSPENDED,
            reason="Cool-down period.", imposed_by=admin,
            ends_at=timezone.now() - timezone.timedelta(seconds=1),  # already in the past
        )
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        count = services.expire_stale_restrictions()
        self.assertEqual(count, 1)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        restriction.refresh_from_db()
        self.assertEqual(restriction.status, BookPublishingRestriction.STATUS_EXPIRED)

    def test_lift_restriction_reinstates_account(self):
        user = make_user("29")
        admin = make_user("30")
        restriction = services.apply_restriction(
            user=user, restriction_type=BookPublishingRestriction.TYPE_ACCOUNT_SUSPENDED,
            reason="Under review.", imposed_by=admin,
        )
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        services.lift_restriction(restriction, lifted_by=admin, notes="Resolved.")
        user.refresh_from_db()
        self.assertTrue(user.is_active)

    def test_paid_publishing_restriction_blocks_price_setting_even_with_verified_shop(self):
        user = make_user("31")
        admin = make_user("32")
        make_verified_shop(user)
        services.apply_restriction(
            user=user, restriction_type=BookPublishingRestriction.TYPE_PAID_PUBLISHING_RESTRICTED,
            reason="Suspicious pricing.", imposed_by=admin,
        )
        with self.assertRaises(PermissionDenied):
            services.assert_eligible_for_price(user=user)


class EntitlementAndAccessTests(TestCase):
    def test_owner_always_has_access(self):
        user = make_user("33")
        book = make_book(user, status=BookStatus.DRAFT)
        self.assertTrue(services.user_has_reading_access(book, user))

    def test_stranger_has_no_access_to_unpublished_book(self):
        owner = make_user("34")
        stranger = make_user("35")
        book = make_book(owner, status=BookStatus.DRAFT)
        self.assertFalse(services.user_has_reading_access(book, stranger))

    def test_published_free_book_is_accessible_to_anyone(self):
        owner = make_user("36")
        stranger = make_user("37")
        book = make_book(owner, status=BookStatus.PUBLISHED)
        self.assertTrue(services.user_has_reading_access(book, stranger))

    def test_published_paid_book_requires_entitlement(self):
        owner = make_user("38")
        stranger = make_user("39")
        book = make_book(owner, is_free=False, price_cents=500, status=BookStatus.PUBLISHED)
        self.assertFalse(services.user_has_reading_access(book, stranger))
        services.grant_entitlement(book, stranger, source=BookEntitlement.SOURCE_PURCHASE)
        self.assertTrue(services.user_has_reading_access(book, stranger))

    def test_grant_entitlement_is_idempotent(self):
        owner = make_user("40")
        buyer = make_user("41")
        book = make_book(owner, is_free=False, price_cents=500, status=BookStatus.PUBLISHED)
        services.grant_entitlement(book, buyer, source=BookEntitlement.SOURCE_PURCHASE)
        services.grant_entitlement(book, buyer, source=BookEntitlement.SOURCE_PURCHASE)
        self.assertEqual(BookEntitlement.objects.filter(book=book, user=buyer).count(), 1)


class BookPurchasePaymentIntegrationTests(TestCase):
    """Exercises the real apps.billing.direct_payments wiring end to end
    (resolve_payout_entity / _target_owner_and_amount / _mark_target_paid),
    not a reimplementation of it."""

    def test_mark_target_paid_grants_entitlement_idempotently(self):
        from apps.billing.direct_payments import _mark_target_paid
        from apps.billing.models import DirectPaymentIntent
        from apps.books.models import BookPurchase

        author = make_user("42")
        shop = make_verified_shop(author)
        buyer = make_user("43")
        book = make_book(author, is_free=False, price_cents=500, status=BookStatus.PUBLISHED)
        book.publisher_shop = shop
        book.save()
        purchase = BookPurchase.objects.create(book=book, buyer=buyer, amount_cents=500, currency="USD")
        intent = DirectPaymentIntent.objects.create(
            user=buyer, provider="flutterwave", target_type=DirectPaymentIntent.TARGET_BOOK_PURCHASE,
            target_id=purchase.id, amount_cents=500, currency="USD",
            status=DirectPaymentIntent.STATUS_PENDING, tx_ref=f"kis_direct_test_{uuid.uuid4().hex}",
        )
        _mark_target_paid(intent, {"id": "provider_txn_1"})
        _mark_target_paid(intent, {"id": "provider_txn_1"})  # simulate a duplicate webhook delivery

        purchase.refresh_from_db()
        self.assertEqual(purchase.status, BookPurchase.STATUS_PAID)
        self.assertEqual(BookEntitlement.objects.filter(book=book, user=buyer).count(), 1)
        self.assertTrue(BookEntitlement.objects.get(book=book, user=buyer).is_active)

    def test_resolve_payout_entity_returns_publisher_shop(self):
        from apps.billing.direct_payments import resolve_payout_entity
        from apps.billing.models import DirectPaymentIntent
        from apps.books.models import BookPurchase

        author = make_user("44")
        shop = make_verified_shop(author)
        buyer = make_user("45")
        book = make_book(author, is_free=False, price_cents=500, status=BookStatus.PUBLISHED)
        book.publisher_shop = shop
        book.save()
        purchase = BookPurchase.objects.create(book=book, buyer=buyer, amount_cents=500, currency="USD")
        entity = resolve_payout_entity(DirectPaymentIntent.TARGET_BOOK_PURCHASE, purchase.id)
        self.assertEqual(entity.id, shop.id)


class AdminModerationAccessControlTests(TestCase):
    """GO/staff-only authorization on the moderation surfaces — a
    non-privileged user must be refused, not merely hidden from in the UI."""

    def setUp(self):
        make_policy()
        self.client = APIClient()

    def test_ordinary_user_cannot_open_moderation_queue(self):
        user = make_user("46")
        self.client.force_authenticate(user=user)
        resp = self.client.get("/control/admin/books/queue/")
        self.assertEqual(resp.status_code, 403)

    def test_superuser_can_open_moderation_queue_and_read_full_manuscript(self):
        author = make_user("47")
        admin = make_user("48", is_superuser=True, is_staff=True, email="admin48@kis.test")
        book = make_book(author, status=BookStatus.PENDING_REVIEW)
        self.client.force_authenticate(user=admin)

        resp = self.client.get("/control/admin/books/queue/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["pagination"]["total_items"], 1)

        detail = self.client.get(f"/control/admin/books/{book.id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(len(detail.json()["chapters"]), 1)

        from apps.books.models import BookModerationAccess

        self.assertTrue(BookModerationAccess.objects.filter(book=book, accessed_by=admin).exists())

    def test_ordinary_user_cannot_ban_an_author(self):
        author = make_user("49")
        user = make_user("50")
        self.client.force_authenticate(user=user)
        resp = self.client.post(
            f"/control/admin/authors/{author.id}/restrictions/",
            {"restriction_type": "account_banned", "reason": "test"},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_superuser_can_approve_submitted_book_via_api(self):
        author = make_user("51")
        admin = make_user("52", is_superuser=True, is_staff=True, email="admin52@kis.test")
        book = make_book(author, status=BookStatus.PENDING_REVIEW)
        self.client.force_authenticate(user=admin)
        resp = self.client.post(f"/control/admin/books/{book.id}/action/", {"action": "approve", "notes": ""}, format="json")
        self.assertEqual(resp.status_code, 200)
        book.refresh_from_db()
        self.assertEqual(book.status, BookStatus.PUBLISHED)


class PublicApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_published_book_is_publicly_listed(self):
        owner = make_user("53")
        make_book(owner, status=BookStatus.PUBLISHED)
        resp = self.client.get("/api/v1/books/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["results"]) if isinstance(resp.json(), dict) and "results" in resp.json() else len(resp.json()), 1)

    def test_list_response_uses_the_projectwide_paginated_shape(self):
        """BookListView is a generics.ListAPIView, so it inherits
        REST_FRAMEWORK['DEFAULT_PAGINATION_CLASS']
        (common.pagination.StandardResultsSetPagination) automatically —
        the real response is {"meta": {...}, "results": [...]}, never a
        bare array. A mobile client that assumed a bare array (as
        BooksMarketplaceScreen.tsx originally did) would silently render
        zero books forever, even with real published books in the
        database — this locks in the actual shape so that regresses
        loudly instead of silently."""
        owner = make_user("53b")
        make_book(owner, status=BookStatus.PUBLISHED)
        resp = self.client.get("/api/v1/books/")
        body = resp.json()
        self.assertIsInstance(body, dict)
        self.assertIn("results", body)
        self.assertIn("meta", body)
        self.assertIn("count", body["meta"])
        self.assertIsInstance(body["results"], list)
        self.assertEqual(len(body["results"]), 1)

    def test_draft_book_is_not_publicly_visible(self):
        owner = make_user("54")
        book = make_book(owner, status=BookStatus.DRAFT)
        resp = self.client.get(f"/api/v1/books/{book.id}/")
        self.assertEqual(resp.status_code, 404)

    def test_owner_can_see_own_draft(self):
        owner = make_user("55")
        book = make_book(owner, status=BookStatus.DRAFT)
        self.client.force_authenticate(user=owner)
        resp = self.client.get(f"/api/v1/books/{book.id}/")
        self.assertEqual(resp.status_code, 200)

    def test_unpublished_chapter_read_is_blocked_for_public(self):
        owner = make_user("56")
        book = make_book(owner, status=BookStatus.DRAFT)
        chapter = book.chapters.first()
        resp = self.client.get(f"/api/v1/books/{book.id}/chapters/{chapter.stable_id}/")
        self.assertEqual(resp.status_code, 403)


class CrossSystemAccountSafetyTests(TestCase):
    """Regression tests for the cross-system is_active bug found during
    verification: lifting/expiring a Books-only restriction must never
    silently reinstate an account another subsystem (generic account
    block, media-safety consequence) independently suspended."""

    def test_lift_reinstates_when_nothing_else_holds_the_account_down(self):
        user = make_user("57")
        admin = make_user("58")
        restriction = services.apply_restriction(
            user=user, restriction_type=BookPublishingRestriction.TYPE_ACCOUNT_SUSPENDED,
            reason="Books-only suspension.", imposed_by=admin,
        )
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertEqual(user.status, "active")  # untouched by Books — only is_active moved
        services.lift_restriction(restriction, lifted_by=admin)
        user.refresh_from_db()
        self.assertTrue(user.is_active)

    def test_lift_does_not_reinstate_an_account_blocked_by_another_subsystem(self):
        user = make_user("59")
        admin = make_user("60")
        restriction = services.apply_restriction(
            user=user, restriction_type=BookPublishingRestriction.TYPE_ACCOUNT_SUSPENDED,
            reason="Books-only suspension.", imposed_by=admin,
        )
        # Simulate admin_control.views.user_management.AdminUserBlockView
        # (or apps.moderation.services.apply_media_safety_action) having
        # independently blocked this same account for an unrelated reason,
        # using the exact same is_active/status fields.
        user.status = "blocked"
        user.save(update_fields=["status"])

        services.lift_restriction(restriction, lifted_by=admin)
        user.refresh_from_db()
        self.assertFalse(user.is_active, "lifting a Books restriction must not undo an unrelated account block")

    def test_expiry_does_not_reinstate_an_account_banned_by_another_subsystem(self):
        user = make_user("61")
        admin = make_user("62")
        services.apply_restriction(
            user=user, restriction_type=BookPublishingRestriction.TYPE_ACCOUNT_SUSPENDED,
            reason="Books-only suspension.", imposed_by=admin,
            ends_at=timezone.now() - timezone.timedelta(seconds=1),
        )
        user.status = "banned"
        user.save(update_fields=["status"])

        services.expire_stale_restrictions()
        user.refresh_from_db()
        self.assertFalse(user.is_active, "an expired Books restriction must not undo an unrelated ban")


class NotificationContentTests(TestCase):
    """Regression test for the blank-notification bug found during
    verification: create_notification only renders title/body from a
    NotificationTemplate keyed by template_key — passing none (as the
    original _notify did) silently produced empty-text notifications."""

    def test_book_approved_notification_has_real_title_and_body(self):
        from apps.notifications.models import Notification

        user = make_user("63")
        reviewer = make_user("64")
        make_policy()
        book = make_book(user)
        book.rights_declared = True
        book.save()
        services.acknowledge_policy(user=user, book=book)
        book = services.submit_for_review(book, actor=user)
        services.apply_moderation_decision(book, action="approve", actor=reviewer)

        notif = Notification.objects.filter(user_id=user.id, type="book_approved").latest("created_at")
        self.assertTrue(notif.title, "notification title must not be blank")
        self.assertTrue(notif.body, "notification body must not be blank")
        self.assertIn(book.title, notif.body)

    def test_held_approval_sends_distinct_notification_type(self):
        from apps.notifications.models import Notification

        author = make_user("65")
        reviewer = make_user("66")
        book = make_book(author, is_free=False, price_cents=999, status=BookStatus.PENDING_REVIEW)
        # No verified shop linked — eligibility has lapsed since submission.
        services.apply_moderation_decision(book, action="approve", actor=reviewer)
        book.refresh_from_db()
        self.assertEqual(book.status, BookStatus.APPROVED)  # not PUBLISHED

        notif = Notification.objects.filter(user_id=author.id, type="book_approved_held").latest("created_at")
        self.assertIn("hold", notif.title.lower())


class ScheduledPublicationTests(TestCase):
    def test_due_scheduled_book_publishes_lazily_on_list_view(self):
        owner = make_user("67")
        book = make_book(owner, status=BookStatus.APPROVED)
        book.scheduled_publish_at = timezone.now() - timezone.timedelta(minutes=1)
        book.save(update_fields=["scheduled_publish_at"])

        count = services.publish_scheduled_books()
        self.assertEqual(count, 1)
        book.refresh_from_db()
        self.assertEqual(book.status, BookStatus.PUBLISHED)
        self.assertIsNotNone(book.published_at)

    def test_not_yet_due_scheduled_book_stays_approved(self):
        owner = make_user("68")
        book = make_book(owner, status=BookStatus.APPROVED)
        book.scheduled_publish_at = timezone.now() + timezone.timedelta(days=1)
        book.save(update_fields=["scheduled_publish_at"])

        services.publish_scheduled_books()
        book.refresh_from_db()
        self.assertEqual(book.status, BookStatus.APPROVED)

    def test_scheduled_paid_book_without_eligible_shop_is_not_published(self):
        owner = make_user("69")
        book = make_book(owner, is_free=False, price_cents=500, status=BookStatus.APPROVED)
        book.scheduled_publish_at = timezone.now() - timezone.timedelta(minutes=1)
        book.save(update_fields=["scheduled_publish_at"])

        count = services.publish_scheduled_books()
        self.assertEqual(count, 0)
        book.refresh_from_db()
        self.assertEqual(book.status, BookStatus.APPROVED)


def make_role_user(suffix, role_names):
    """A user whose ONLY admin_control authority comes from the named
    Books role presets (seeded by apps.books.management.commands.
    seed_books_launch) — not is_superuser/is_staff/GO — so these tests
    exercise the real role-separation boundary, not the admin bypass."""
    from admin_control.roles import AdminRole, AdminRoleAssignment

    user = make_user(suffix)
    for name in role_names:
        role = AdminRole.objects.get(name=name)
        AdminRoleAssignment.objects.get_or_create(user=user, role=role)
    return user


class RoleSeparationTests(TestCase):
    """A reviewer who can read/approve books must not automatically get
    restriction or ban authority — the master spec's explicit requirement
    that these be separate roles, verified end to end through the real
    AdminAccessService lookup (not mocked)."""

    def setUp(self):
        from django.core.management import call_command

        call_command("seed_books_launch")
        self.client = APIClient()

    def test_book_reviewer_can_approve_but_not_block_or_ban(self):
        author = make_user("70")
        reviewer = make_role_user("71", ["Book Reviewer"])
        book = make_book(author, status=BookStatus.PENDING_REVIEW)
        self.client.force_authenticate(user=reviewer)

        approve = self.client.post(f"/control/admin/books/{book.id}/action/", {"action": "approve"}, format="json")
        self.assertEqual(approve.status_code, 200)

        book2 = make_book(author, status=BookStatus.PUBLISHED)
        block = self.client.post(f"/control/admin/books/{book2.id}/action/", {"action": "block"}, format="json")
        self.assertEqual(block.status_code, 403)

        ban = self.client.post(
            f"/control/admin/authors/{author.id}/restrictions/",
            {"restriction_type": "account_banned", "reason": "test"}, format="json",
        )
        self.assertEqual(ban.status_code, 403)

    def test_book_moderator_can_block_but_not_ban(self):
        author = make_user("72")
        moderator = make_role_user("73", ["Book Moderator"])
        book = make_book(author, status=BookStatus.PUBLISHED)
        self.client.force_authenticate(user=moderator)

        block = self.client.post(f"/control/admin/books/{book.id}/action/", {"action": "block"}, format="json")
        self.assertEqual(block.status_code, 200)

        ban = self.client.post(
            f"/control/admin/authors/{author.id}/restrictions/",
            {"restriction_type": "account_banned", "reason": "test"}, format="json",
        )
        self.assertEqual(ban.status_code, 403)

        warn = self.client.post(
            f"/control/admin/authors/{author.id}/restrictions/",
            {"restriction_type": "warning", "reason": "test"}, format="json",
        )
        self.assertEqual(warn.status_code, 201)

    def test_book_administrator_can_ban(self):
        author = make_user("74")
        admin_role_user = make_role_user("75", ["Book Administrator"])
        self.client.force_authenticate(user=admin_role_user)

        ban = self.client.post(
            f"/control/admin/authors/{author.id}/restrictions/",
            {"restriction_type": "account_banned", "reason": "test"}, format="json",
        )
        self.assertEqual(ban.status_code, 201)
        author.refresh_from_db()
        self.assertFalse(author.is_active)


class AuditLogTests(TestCase):
    def test_approve_reject_and_restriction_actions_are_audited(self):
        from admin_control.models import AdminAuditEntry

        author = make_user("76")
        admin = make_user("77", is_superuser=True, is_staff=True, email="admin77@kis.test")
        client = APIClient()
        client.force_authenticate(user=admin)

        book = make_book(author, status=BookStatus.PENDING_REVIEW)
        resp = client.post(f"/control/admin/books/{book.id}/action/", {"action": "approve"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(AdminAuditEntry.objects.filter(action_type="books.approve", target_pk=str(book.id)).exists())

        book2 = make_book(author, status=BookStatus.PENDING_REVIEW)
        resp = client.post(f"/control/admin/books/{book2.id}/action/", {"action": "reject", "notes": "no"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(AdminAuditEntry.objects.filter(action_type="books.reject", target_pk=str(book2.id)).exists())

        resp = client.post(
            f"/control/admin/authors/{author.id}/restrictions/",
            {"restriction_type": "warning", "reason": "test audit"}, format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(AdminAuditEntry.objects.filter(action_type="books.author_restricted", metadata__user_id=str(author.id)).exists())


class EndToEndWorkflowTests(TestCase):
    """Drives the entire author->submission->correction->resubmission->
    approval->publication->suspension->restriction lifecycle purely
    through HTTP (author-facing REST API + admin_control endpoints), the
    same way a real mobile/website client would — not by calling service
    functions directly. This is the literal step-by-step golden path from
    the verification request."""

    def setUp(self):
        make_policy()
        self.author = make_user("80")
        self.reviewer = make_user("81", is_superuser=True, is_staff=True, email="reviewer81@kis.test")
        self.author_client = APIClient()
        self.author_client.force_authenticate(user=self.author)
        self.reviewer_client = APIClient()
        self.reviewer_client.force_authenticate(user=self.reviewer)

    def test_full_free_book_lifecycle(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        # 1. Create a free-book draft via the real author API.
        create = self.author_client.post("/api/v1/books/mine/", {
            "title": "On the Incarnation", "author_name": "Athanasius",
            "description": "A classic.", "christian_category": "theology",
            "is_free": True, "price_cents": 0,
        }, format="json")
        self.assertEqual(create.status_code, 201, create.content)
        book_id = create.json()["id"]

        # Submitting before a manuscript/rights/policy exist must fail closed.
        early_submit = self.author_client.post(f"/api/v1/books/mine/{book_id}/submit/", {}, format="json")
        self.assertEqual(early_submit.status_code, 400)

        # 2. Upload a valid TXT manuscript through the real multipart endpoint.
        manuscript_text = (
            "CHAPTER ONE\n\nIn the beginning the Word was already at work, abundant and good.\n\n"
            "CHAPTER TWO\n\nAnd so the account continues onward, truly and at length, for the reader.\n"
        ).encode()
        upload_file = SimpleUploadedFile("manuscript.txt", manuscript_text, content_type="text/plain")
        upload = self.author_client.post(f"/api/v1/books/mine/{book_id}/manuscript/", {"file": upload_file}, format="multipart")
        self.assertEqual(upload.status_code, 201, upload.content)

        # 3. Conversion ran (CELERY_TASK_ALWAYS_EAGER=True in test settings)
        # and produced a reviewable draft with real chapters.
        detail = self.author_client.get(f"/api/v1/books/mine/{book_id}/").json()
        self.assertEqual(detail["status"], "draft")
        self.assertGreaterEqual(len(detail["chapters"]), 1)

        # 4. Declare rights and acknowledge the Christian publishing policy.
        rights = self.author_client.patch(f"/api/v1/books/mine/{book_id}/", {
            "rights_declared": True, "rights_declaration_text": "I own this content.",
        }, format="json")
        self.assertEqual(rights.status_code, 200, rights.content)
        ack = self.author_client.post(f"/api/v1/books/mine/{book_id}/acknowledge-policy/", {}, format="json")
        self.assertEqual(ack.status_code, 200, ack.content)

        # 5. Submit for review — now eligible.
        submit = self.author_client.post(f"/api/v1/books/mine/{book_id}/submit/", {}, format="json")
        self.assertEqual(submit.status_code, 200, submit.content)
        self.assertEqual(submit.json()["status"], "pending_review")

        # Resubmitting while already under review must be rejected.
        resubmit_blocked = self.author_client.post(f"/api/v1/books/mine/{book_id}/submit/", {}, format="json")
        self.assertEqual(resubmit_blocked.status_code, 400)

        # 6/7. The submission is visible in the GO queue, and the reviewer
        # can read the complete converted manuscript.
        queue = self.reviewer_client.get("/control/admin/books/queue/").json()
        self.assertIn(book_id, [b["id"] for b in queue["books"]])
        full = self.reviewer_client.get(f"/control/admin/books/{book_id}/").json()
        self.assertEqual(len(full["chapters"]), len(detail["chapters"]))
        self.assertIn("beginning", full["chapters"][0]["content_blocks"][-1]["text"])

        # 8. Reviewer requests corrections rather than approving outright.
        corrections = self.reviewer_client.post(
            f"/control/admin/books/{book_id}/action/", {"action": "request_corrections", "notes": "Fix the title."}, format="json",
        )
        self.assertEqual(corrections.status_code, 200)

        # 9. The author sees the correct status and explanation.
        after_corrections = self.author_client.get(f"/api/v1/books/mine/{book_id}/").json()
        self.assertEqual(after_corrections["status"], "corrections_requested")
        self.assertEqual(after_corrections["moderation_notes"], "Fix the title.")

        # 10. An eligible resubmission preserves the manuscript/chapters —
        # the author edits a chapter, then resubmits without re-uploading.
        chapter_stable_id = after_corrections["chapters"][0]["stable_id"]
        edit = self.author_client.patch(
            f"/api/v1/books/mine/{book_id}/chapters/{chapter_stable_id}/",
            {"title": "Chapter One: Fixed"}, format="json",
        )
        self.assertEqual(edit.status_code, 200, edit.content)
        resubmit = self.author_client.post(f"/api/v1/books/mine/{book_id}/submit/", {}, format="json")
        self.assertEqual(resubmit.status_code, 200, resubmit.content)
        self.assertEqual(resubmit.json()["status"], "pending_review")
        preserved = self.author_client.get(f"/api/v1/books/mine/{book_id}/").json()
        self.assertEqual(len(preserved["chapters"]), len(detail["chapters"]))  # manuscript not wiped

        # 11/12. Reviewer approves; publication happens immediately since
        # it's free and unscheduled.
        approve = self.reviewer_client.post(f"/control/admin/books/{book_id}/action/", {"action": "approve"}, format="json")
        self.assertEqual(approve.status_code, 200)
        published = self.author_client.get(f"/api/v1/books/mine/{book_id}/").json()
        self.assertEqual(published["status"], "published")

        # It is now visible on the real public marketplace, readable by a
        # stranger with no entitlement (it's free), including the edited title.
        stranger = APIClient()
        stranger.force_authenticate(user=make_user("82"))
        public_detail = stranger.get(f"/api/v1/books/{book_id}/").json()
        self.assertTrue(public_detail["has_access"])
        chapter_read = stranger.get(f"/api/v1/books/{book_id}/chapters/{chapter_stable_id}/").json()
        self.assertEqual(chapter_read["title"], "Chapter One: Fixed")

        # 13/14. Reviewer suspends the published book — enforced immediately
        # on the exact same public endpoint a mobile/website client would hit.
        suspend = self.reviewer_client.post(f"/control/admin/books/{book_id}/action/", {"action": "block", "notes": "policy review"}, format="json")
        self.assertEqual(suspend.status_code, 200)
        blocked_public = stranger.get(f"/api/v1/books/{book_id}/")
        self.assertEqual(blocked_public.status_code, 404)  # no longer publicly visible
        # A direct attempt to read the chapter content is blocked too — the
        # restriction isn't bypassable by hitting the content endpoint directly.
        blocked_chapter = APIClient()
        blocked_chapter.force_authenticate(user=make_user("83"))
        self.assertEqual(blocked_chapter.get(f"/api/v1/books/{book_id}/chapters/{chapter_stable_id}/").status_code, 403)

        # 15/16. Reviewer applies a temporary publishing restriction to the
        # author directly, and it expires correctly.
        from datetime import timedelta

        restrict = self.reviewer_client.post(
            f"/control/admin/authors/{self.author.id}/restrictions/",
            {"restriction_type": "publishing_suspended", "reason": "repeat corrections", "ends_at": (timezone.now() + timedelta(days=1)).isoformat()},
            format="json",
        )
        self.assertEqual(restrict.status_code, 201)

        # Build a SECOND book that is otherwise fully eligible to submit
        # (has chapters, rights declared, policy acknowledged) via the real
        # API, so the only thing that can block it is the restriction.
        second = make_book(self.author, with_chapter=True)  # chapters via the ORM helper, not re-uploading a manuscript
        rights2 = self.author_client.patch(f"/api/v1/books/mine/{second.id}/", {"rights_declared": True, "rights_declaration_text": "Mine."}, format="json")
        self.assertEqual(rights2.status_code, 200)
        ack2 = self.author_client.post(f"/api/v1/books/mine/{second.id}/acknowledge-policy/", {}, format="json")
        self.assertEqual(ack2.status_code, 200)

        restricted_submit = self.author_client.post(f"/api/v1/books/mine/{second.id}/submit/", {}, format="json")
        self.assertEqual(restricted_submit.status_code, 403, restricted_submit.content)

        # Expire the restriction and confirm the exact same, otherwise-
        # unchanged book can now actually be submitted through the real API.
        from apps.books.models import BookPublishingRestriction

        restriction_row = BookPublishingRestriction.objects.get(user=self.author, restriction_type="publishing_suspended")
        restriction_row.ends_at = timezone.now() - timedelta(seconds=1)
        restriction_row.save(update_fields=["ends_at"])
        services.expire_stale_restrictions()

        now_allowed_submit = self.author_client.post(f"/api/v1/books/mine/{second.id}/submit/", {}, format="json")
        self.assertEqual(now_allowed_submit.status_code, 200, now_allowed_submit.content)


class ScannedPdfOcrTests(TestCase):
    """Proves apps.books.conversion actually runs OCR (real Tesseract,
    real PyMuPDF page rendering) on scanned PDFs — not a stub, and not
    mocked. make_scanned_pdf_bytes builds a PDF containing only an image
    (pypdf.extract_text() returns '' on it), so any text that comes back
    out of convert_pdf() on it genuinely passed through the OCR path."""

    def test_fully_scanned_pdf_is_recognized_via_ocr(self):
        from apps.books.conversion import convert_pdf

        pdf_bytes = make_scanned_pdf_bytes(["CHAPTER ONE", "", "In the beginning grace was abundant and good indeed."])
        result = convert_pdf(pdf_bytes)
        all_text = " ".join(b["text"] for c in result.chapters for b in c.blocks)
        self.assertIn("beginning", all_text.lower())
        self.assertIn("grace", all_text.lower())
        self.assertTrue(any("OCR" in w for w in result.warnings))
        self.assertGreater(result.confidence_score, 0)

    def test_mixed_pdf_only_ocrs_the_pages_without_a_text_layer(self):
        import fitz
        from apps.books.conversion import convert_pdf

        # Page 1: a real text layer. Page 2: image-only (scanned).
        real_doc = fitz.open()
        p1 = real_doc.new_page()
        p1.insert_text((72, 72), "This page has a genuine selectable text layer.")
        mixed_bytes = real_doc.tobytes()
        real_doc.close()

        scanned_bytes = make_scanned_pdf_bytes(["This second page is a scanned image only."])

        combined = fitz.open(stream=mixed_bytes, filetype="pdf")
        scanned_src = fitz.open(stream=scanned_bytes, filetype="pdf")
        combined.insert_pdf(scanned_src)
        scanned_src.close()
        final_bytes = combined.tobytes()
        combined.close()

        result = convert_pdf(final_bytes)
        all_text = " ".join(b["text"] for c in result.chapters for b in c.blocks)
        self.assertIn("genuine selectable", all_text)
        self.assertIn("scanned image only", all_text.lower())
        ocr_warnings = [w for w in result.warnings if "OCR was used to recognize text on 1 scanned page" in w]
        self.assertEqual(len(ocr_warnings), 1, result.warnings)  # exactly 1 page needed OCR, not both

    def test_too_many_scanned_pages_fails_with_clear_error_not_a_hang(self):
        from unittest.mock import patch

        import fitz
        from apps.books.conversion import ManuscriptValidationError, convert_pdf

        page1 = fitz.open(stream=make_scanned_pdf_bytes(["Page one scanned."]), filetype="pdf")
        page2 = fitz.open(stream=make_scanned_pdf_bytes(["Page two scanned."]), filetype="pdf")
        page1.insert_pdf(page2)
        page2.close()
        two_scanned_pages = page1.tobytes()
        page1.close()

        with patch("apps.books.conversion.MAX_OCR_PAGES", 1):
            with self.assertRaises(ManuscriptValidationError) as ctx:
                convert_pdf(two_scanned_pages)
        self.assertEqual(ctx.exception.code, "too_many_ocr_pages")

    def test_ocr_dependency_missing_raises_clear_error_not_a_crash(self):
        from unittest.mock import patch

        from apps.books.conversion import ManuscriptValidationError, convert_pdf

        pdf_bytes = make_scanned_pdf_bytes(["Some scanned text."])
        with patch("pytesseract.get_tesseract_version", side_effect=RuntimeError("tesseract not on PATH")):
            with self.assertRaises(ManuscriptValidationError) as ctx:
                convert_pdf(pdf_bytes)
        self.assertEqual(ctx.exception.code, "dependency_missing")

    def test_full_upload_to_published_cycle_for_a_scanned_manuscript(self):
        """The complete author-facing pipeline (not just the conversion
        function in isolation) correctly handles a scanned PDF end to end."""
        make_policy()
        author = make_user("90")
        reviewer = make_user("91", is_superuser=True, is_staff=True, email="reviewer91@kis.test")
        book = Book.objects.create(owner=author, title="Scanned Classic", author_name="Author", status=BookStatus.DRAFT)

        pdf_bytes = make_scanned_pdf_bytes(["CHAPTER ONE", "", "Real recognizable words appear on this scanned page."])
        manuscript = services.create_manuscript(book, uploaded_by=author, filename="scan.pdf", content_type="application/pdf", data=pdf_bytes)
        job = services.process_manuscript(manuscript)
        self.assertEqual(job.status, job.STATUS_SUCCEEDED)
        self.assertGreater(job.confidence_score, 0)

        book.refresh_from_db()
        self.assertGreaterEqual(book.chapters.count(), 1)
        all_text = " ".join(
            b["text"] for chapter in book.chapters.all() for b in chapter.content_blocks
        ).lower()
        self.assertIn("recognizable", all_text)

        book.rights_declared = True
        book.save()
        services.acknowledge_policy(user=author, book=book)
        book = services.submit_for_review(book, actor=author)
        book = services.apply_moderation_decision(book, action="approve", actor=reviewer)
        self.assertEqual(book.status, BookStatus.PUBLISHED)


class ContentEncryptionAtRestTests(TestCase):
    """Proves apps.books.encryption's envelope scheme is really applied —
    not that the code merely calls Fernet somewhere, but that the actual
    database column never holds the plaintext, two books get different
    DEKs, and a corrupted/foreign ciphertext fails closed instead of
    silently returning garbage or someone else's content."""

    def test_stored_column_is_ciphertext_not_plaintext(self):
        from apps.books.models import BookChapter, BookContentKey

        owner = make_user("95")
        book = Book.objects.create(owner=owner, title="T", author_name="A")
        secret = "TOP SECRET SENTENCE 90210"
        BookChapter.objects.create(book=book, order=1, title="Ch1", content_blocks=[{"type": "paragraph", "text": secret}], word_count=4)

        raw = BookChapter.objects.get(book=book, order=1)
        self.assertNotIn(secret, raw.encrypted_content)
        self.assertTrue(raw.encrypted_content)  # not empty — something was actually written
        self.assertEqual(raw.content_blocks, [{"type": "paragraph", "text": secret}])
        self.assertTrue(BookContentKey.objects.filter(book=book).exists())

    def test_two_books_get_independent_keys(self):
        from apps.books.models import BookChapter, BookContentKey

        owner = make_user("96")
        book_a = Book.objects.create(owner=owner, title="A", author_name="A")
        book_b = Book.objects.create(owner=owner, title="B", author_name="A")
        BookChapter.objects.create(book=book_a, order=1, content_blocks=[{"type": "paragraph", "text": "same text"}])
        BookChapter.objects.create(book=book_b, order=1, content_blocks=[{"type": "paragraph", "text": "same text"}])

        key_a = BookContentKey.objects.get(book=book_a).wrapped_dek
        key_b = BookContentKey.objects.get(book=book_b).wrapped_dek
        self.assertNotEqual(key_a, key_b)

        chapter_a = BookChapter.objects.get(book=book_a)
        chapter_b = BookChapter.objects.get(book=book_b)
        # Identical plaintext, encrypted under different keys -> different
        # ciphertext (also Fernet includes a random IV, so even the same
        # key would differ, but this proves the keys genuinely differ too).
        self.assertNotEqual(chapter_a.encrypted_content, chapter_b.encrypted_content)

    def test_tampered_ciphertext_fails_closed_not_silently(self):
        from apps.books.encryption import ContentEncryptionError
        from apps.books.models import BookChapter

        owner = make_user("97")
        book = Book.objects.create(owner=owner, title="T", author_name="A")
        chapter = BookChapter.objects.create(book=book, order=1, content_blocks=[{"type": "paragraph", "text": "hello"}])

        chapter.encrypted_content = chapter.encrypted_content[:-4] + "abcd"  # corrupt the auth tag
        chapter.save(update_fields=["encrypted_content"])
        reloaded = BookChapter.objects.get(pk=chapter.pk)
        with self.assertRaises(ContentEncryptionError):
            _ = reloaded.content_blocks

    def test_missing_key_row_fails_closed(self):
        from apps.books.encryption import ContentEncryptionError
        from apps.books.models import BookChapter, BookContentKey

        owner = make_user("98")
        book = Book.objects.create(owner=owner, title="T", author_name="A")
        chapter = BookChapter.objects.create(book=book, order=1, content_blocks=[{"type": "paragraph", "text": "hello"}])
        BookContentKey.objects.filter(book=book).delete()

        reloaded = BookChapter.objects.get(pk=chapter.pk)
        with self.assertRaises(ContentEncryptionError):
            _ = reloaded.content_blocks

    def test_wrong_kek_cannot_unwrap_the_dek(self):
        """Simulates the whole-database-dump threat model: without the
        real BOOKS_CONTENT_KEK (which never lives in the DB), the wrapped
        DEK is unusable."""
        from cryptography.fernet import Fernet, InvalidToken

        from apps.books.models import BookChapter, BookContentKey

        owner = make_user("99")
        book = Book.objects.create(owner=owner, title="T", author_name="A")
        BookChapter.objects.create(book=book, order=1, content_blocks=[{"type": "paragraph", "text": "hello"}])

        wrapped_dek = BookContentKey.objects.get(book=book).wrapped_dek
        wrong_kek = Fernet.generate_key()
        with self.assertRaises(InvalidToken):
            Fernet(wrong_kek).decrypt(wrapped_dek.encode())

    def test_key_rotation_rewraps_without_touching_chapter_ciphertext(self):
        import os

        from django.core.management import call_command
        from django.test import override_settings

        from apps.books.encryption import generate_kek
        from apps.books.models import BookChapter, BookContentKey

        owner = make_user("100")
        book = Book.objects.create(owner=owner, title="T", author_name="A")
        chapter = BookChapter.objects.create(book=book, order=1, content_blocks=[{"type": "paragraph", "text": "rotate me"}])
        original_chapter_ciphertext = chapter.encrypted_content
        original_wrapped_dek = BookContentKey.objects.get(book=book).wrapped_dek

        from django.conf import settings

        new_kek = generate_kek()
        old_env_new = os.environ.get("BOOKS_CONTENT_KEK_NEW")
        old_env_cur = os.environ.get("BOOKS_CONTENT_KEK")
        os.environ["BOOKS_CONTENT_KEK_NEW"] = new_kek
        os.environ["BOOKS_CONTENT_KEK"] = settings.BOOKS_CONTENT_KEK
        try:
            call_command("rewrap_book_content_keys")
        finally:
            if old_env_new is None:
                os.environ.pop("BOOKS_CONTENT_KEK_NEW", None)
            else:
                os.environ["BOOKS_CONTENT_KEK_NEW"] = old_env_new
            if old_env_cur is None:
                os.environ.pop("BOOKS_CONTENT_KEK", None)
            else:
                os.environ["BOOKS_CONTENT_KEK"] = old_env_cur

        key_row = BookContentKey.objects.get(book=book)
        self.assertNotEqual(key_row.wrapped_dek, original_wrapped_dek)  # re-wrapped

        reloaded_chapter = BookChapter.objects.get(pk=chapter.pk)
        self.assertEqual(reloaded_chapter.encrypted_content, original_chapter_ciphertext)  # untouched

        # The book's content is still readable under its OLD (unrotated)
        # settings.BOOKS_CONTENT_KEK — proving the rotation command rewrapped
        # the DEK under a key this test never promoted into settings at all.
        with override_settings(BOOKS_CONTENT_KEK=new_kek):
            self.assertEqual(reloaded_chapter.content_blocks, [{"type": "paragraph", "text": "rotate me"}])

    def test_key_rotation_also_rewraps_active_offline_license_keys(self):
        """The same rewrap command must cover BookOfflineLicense.wrapped_package_dek
        too (see offline_crypto.py's docstring — it uses the identical
        BOOKS_CONTENT_KEK wrap convention), not just BookContentKey."""
        import os

        from django.conf import settings
        from django.core.management import call_command
        from django.test import override_settings

        from apps.books import offline_crypto, services
        from apps.books.encryption import generate_kek
        from apps.books.models import BookOfflineLicense

        owner = make_user("101")
        book = make_book(owner, status=BookStatus.PUBLISHED)
        reader = make_user("102")
        _, package = services.issue_or_renew_offline_license(book, reader, device_id="dev-rotation-test")
        license_row = BookOfflineLicense.objects.get(book=book, user=reader, device_id="dev-rotation-test")
        original_wrapped = license_row.wrapped_package_dek

        new_kek = generate_kek()
        old_env_new = os.environ.get("BOOKS_CONTENT_KEK_NEW")
        old_env_cur = os.environ.get("BOOKS_CONTENT_KEK")
        os.environ["BOOKS_CONTENT_KEK_NEW"] = new_kek
        os.environ["BOOKS_CONTENT_KEK"] = settings.BOOKS_CONTENT_KEK
        try:
            call_command("rewrap_book_content_keys")
        finally:
            if old_env_new is None:
                os.environ.pop("BOOKS_CONTENT_KEK_NEW", None)
            else:
                os.environ["BOOKS_CONTENT_KEK_NEW"] = old_env_new
            if old_env_cur is None:
                os.environ.pop("BOOKS_CONTENT_KEK", None)
            else:
                os.environ["BOOKS_CONTENT_KEK"] = old_env_cur

        license_row.refresh_from_db()
        self.assertNotEqual(license_row.wrapped_package_dek, original_wrapped)  # re-wrapped

        # The package DEK unwraps correctly under the NEW key, proving
        # the rewrap preserved the real key (not just changed the bytes),
        # and the package itself is still decryptable with it.
        with override_settings(BOOKS_CONTENT_KEK=new_kek):
            dek = offline_crypto.unwrap_package_dek(license_row.wrapped_package_dek)
            manifest = offline_crypto.decrypt_part(
                dek, package["manifest"], book_id=str(book.id), license_id=str(license_row.id),
                package_version=license_row.package_version, part="manifest",
            )
            self.assertEqual(manifest["book_id"], str(book.id))


class BlockIdStabilityTests(TestCase):
    def test_conversion_pipeline_assigns_a_stable_id_to_every_block(self):
        # assign_block_ids runs in services.process_manuscript (the real
        # manuscript-upload pipeline), not in conversion.convert_txt
        # itself (a pure text-extraction function with no notion of a
        # persisted Book/BookChapter yet) — exercise the actual pipeline.
        owner = make_user("100")
        book = Book.objects.create(owner=owner, title="T", author_name="A", status=BookStatus.DRAFT)
        manuscript = services.create_manuscript(
            book, uploaded_by=owner, filename="x.txt", content_type="text/plain",
            data=b"Some words appear on this single line of text here.",
        )
        services.process_manuscript(manuscript)
        book.refresh_from_db()
        for chapter in book.chapters.all():
            for block in chapter.content_blocks:
                self.assertIn("id", block)
                self.assertTrue(block["id"])

    def test_author_edit_keeps_existing_block_ids_assigns_new_ones(self):
        owner = make_user("101")
        book = make_book(owner, with_chapter=True)
        chapter = book.chapters.first()
        original_block = chapter.content_blocks[0]

        client = APIClient()
        client.force_authenticate(user=owner)
        resp = client.patch(
            f"/api/v1/books/mine/{book.id}/chapters/{chapter.stable_id}/",
            {"content_blocks": [original_block, {"type": "paragraph", "text": "A brand new second paragraph."}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        chapter.refresh_from_db()
        blocks = chapter.content_blocks
        self.assertEqual(blocks[0]["id"], original_block["id"])  # preserved
        self.assertTrue(blocks[1]["id"])
        self.assertNotEqual(blocks[1]["id"], original_block["id"])  # freshly assigned


class ChapterEditVersioningTests(TestCase):
    """Closes the gap: editing a PUBLISHED book's chapter text must bump
    Book.content_version and force it back to PENDING_REVIEW (matching
    AuthorBookDetailView.patch's existing book-metadata-edit behavior) —
    and a genuinely no-op PATCH must NOT do either, since content_version
    drives offline-package staleness (verify_offline_license) and a
    false bump would re-queue a live book for review over nothing."""

    def setUp(self):
        make_policy()
        self.author = make_user("300")
        self.reviewer = make_user("301", is_superuser=True, is_staff=True, email="reviewer301@kis.test")
        self.client = APIClient()
        self.client.force_authenticate(user=self.author)

    def _publish_book(self):
        book = make_book(self.author, status=BookStatus.DRAFT)
        book.rights_declared = True
        book.save(update_fields=["rights_declared"])
        services.acknowledge_policy(user=self.author, book=book)
        book = services.submit_for_review(book, actor=self.author)
        book = services.apply_moderation_decision(book, action="approve", actor=self.reviewer)
        self.assertEqual(book.status, BookStatus.PUBLISHED)
        return book

    def test_real_text_change_on_published_book_bumps_version_and_requeues_review(self):
        book = self._publish_book()
        original_version = book.content_version
        chapter = book.chapters.first()

        resp = self.client.patch(
            f"/api/v1/books/mine/{book.id}/chapters/{chapter.stable_id}/",
            {"content_blocks": [{**chapter.content_blocks[0], "text": "This text has genuinely changed now."}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["book_status"], BookStatus.PENDING_REVIEW)

        book.refresh_from_db()
        self.assertEqual(book.content_version, original_version + 1)
        self.assertEqual(book.status, BookStatus.PENDING_REVIEW)
        self.assertIsNotNone(book.submitted_at)

    def test_noop_patch_on_published_book_does_not_bump_version_or_requeue(self):
        book = self._publish_book()
        original_version = book.content_version
        chapter = book.chapters.first()
        same_blocks = chapter.content_blocks  # round-tripped verbatim, including existing ids

        resp = self.client.patch(
            f"/api/v1/books/mine/{book.id}/chapters/{chapter.stable_id}/",
            {"title": chapter.title, "content_blocks": same_blocks},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["book_status"], BookStatus.PUBLISHED)

        book.refresh_from_db()
        self.assertEqual(book.content_version, original_version)
        self.assertEqual(book.status, BookStatus.PUBLISHED)

    def test_text_change_on_draft_book_does_not_touch_version_or_status(self):
        book = make_book(self.author, status=BookStatus.DRAFT)
        chapter = book.chapters.first()
        original_version = book.content_version

        resp = self.client.patch(
            f"/api/v1/books/mine/{book.id}/chapters/{chapter.stable_id}/",
            {"content_blocks": [{**chapter.content_blocks[0], "text": "Edited while still a draft."}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        book.refresh_from_db()
        self.assertEqual(book.content_version, original_version)
        self.assertEqual(book.status, BookStatus.DRAFT)

    def test_cannot_edit_chapter_while_under_review(self):
        book = make_book(self.author, status=BookStatus.DRAFT)
        book.rights_declared = True
        book.save(update_fields=["rights_declared"])
        services.acknowledge_policy(user=self.author, book=book)
        book = services.submit_for_review(book, actor=self.author)
        chapter = book.chapters.first()

        resp = self.client.patch(
            f"/api/v1/books/mine/{book.id}/chapters/{chapter.stable_id}/",
            {"title": "Should not apply"}, format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_real_chapter_edit_on_published_book_marks_offline_package_stale(self):
        book = self._publish_book()
        reader = make_user("302")
        BookEntitlement.objects.create(book=book, user=reader, source="free")
        reader_client = APIClient()
        reader_client.force_authenticate(user=reader)

        license_row, _package = services.issue_or_renew_offline_license(book, reader, device_id="device-A")
        self.assertEqual(license_row.book_content_version, book.content_version)
        status_before = services.verify_offline_license(book, reader, device_id="device-A")
        self.assertFalse(status_before["update_available"])

        chapter = book.chapters.first()
        self.client.patch(
            f"/api/v1/books/mine/{book.id}/chapters/{chapter.stable_id}/",
            {"content_blocks": [{**chapter.content_blocks[0], "text": "A genuinely different sentence entirely."}]},
            format="json",
        )

        book.refresh_from_db()
        # The edit re-queued the book for review, exactly like a
        # book-metadata edit would — re-approve so the reader's existing
        # entitlement/offline flow can be exercised post-edit.
        book = services.apply_moderation_decision(book, action="approve", actor=self.reviewer)

        status_after = services.verify_offline_license(book, reader, device_id="device-A")
        self.assertTrue(status_after["update_available"])  # correctly flagged stale now

    def test_highlight_anchor_survives_an_unrelated_blocks_edit_in_the_same_chapter(self):
        book = self._publish_book()
        chapter = book.chapters.first()
        block = chapter.content_blocks[0]
        highlight = BookHighlight.objects.create(
            book=book, chapter=chapter, user=self.author, block_id=block["id"],
            start_offset=0, end_offset=5, highlighted_text=block["text"][:5],
        )
        self.assertTrue(services.resolve_highlight_anchor(highlight)["resolved"])

        # Append an unrelated second block — the highlighted block itself
        # is untouched and keeps its id/text.
        self.client.patch(
            f"/api/v1/books/mine/{book.id}/chapters/{chapter.stable_id}/",
            {"content_blocks": [block, {"type": "paragraph", "text": "A brand new unrelated paragraph."}]},
            format="json",
        )
        chapter.refresh_from_db()
        highlight.refresh_from_db()
        highlight.chapter = chapter
        self.assertTrue(services.resolve_highlight_anchor(highlight)["resolved"])  # still resolves correctly


class HighlightShareControlTests(TestCase):
    """A highlight is the one unit of book content allowed to leave the app
    (shared verbatim into KIS chat) — services.MAX_HIGHLIGHT_CHARS must cap
    it at creation time so neither a long personal highlight nor anything
    built from it (e.g. a chat share) can smuggle out more than a short
    excerpt, no matter how long the underlying content block is."""

    def setUp(self):
        self.owner = make_user("105")
        self.book = make_book(self.owner, status=BookStatus.PUBLISHED, with_chapter=False)
        from apps.books.models import BookChapter

        long_text = "Grace abounds in every season. " * 40  # well over the cap
        blocks = services.assign_block_ids([{"type": "paragraph", "text": long_text}])
        self.chapter = BookChapter.objects.create(
            book=self.book, order=1, title="Chapter 1", content_blocks=blocks, word_count=len(long_text.split()),
        )
        self.block_id = self.chapter.content_blocks[0]["id"]
        self.reader = make_user("106")
        self.reader_client = APIClient()
        self.reader_client.force_authenticate(user=self.reader)

    def test_highlight_spanning_a_long_block_is_truncated_server_side(self):
        url = f"/api/v1/books/{self.book.id}/chapters/{self.chapter.stable_id}/highlights/"
        resp = self.reader_client.post(
            url, {"block_id": self.block_id, "start_offset": 0, "end_offset": 2000}, format="json",
        )
        self.assertEqual(resp.status_code, 201)
        stored_text = resp.json()["highlighted_text"]
        self.assertLessEqual(len(stored_text), services.MAX_HIGHLIGHT_CHARS + 1)  # +1 for the trailing "…"
        self.assertTrue(stored_text.endswith("…"))

        from apps.books.models import BookHighlight

        db_text = BookHighlight.objects.get(user=self.reader, chapter=self.chapter).highlighted_text
        self.assertLessEqual(len(db_text), services.MAX_HIGHLIGHT_CHARS + 1)

    def test_short_highlight_is_unaffected(self):
        url = f"/api/v1/books/{self.book.id}/chapters/{self.chapter.stable_id}/highlights/"
        resp = self.reader_client.post(
            url, {"block_id": self.block_id, "start_offset": 0, "end_offset": 20}, format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(len(resp.json()["highlighted_text"]), 20)
        self.assertFalse(resp.json()["highlighted_text"].endswith("…"))


class BookReviewTests(TestCase):
    def setUp(self):
        self.owner = make_user("107")
        self.book = make_book(self.owner, status=BookStatus.PUBLISHED)  # free -> anyone authenticated has access
        self.reader = make_user("108")
        self.reader_client = APIClient()
        self.reader_client.force_authenticate(user=self.reader)
        self.reviews_url = f"/api/v1/books/{self.book.id}/reviews/"

    def test_review_requires_reading_access(self):
        locked_book = make_book(self.owner, is_free=False, price_cents=999, status=BookStatus.PUBLISHED)
        resp = self.reader_client.post(
            f"/api/v1/books/{locked_book.id}/reviews/", {"rating": 5, "review_text": "Great!"}, format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_create_review_updates_book_aggregate(self):
        resp = self.reader_client.post(self.reviews_url, {"rating": 4, "review_text": "Really helped me."}, format="json")
        self.assertEqual(resp.status_code, 201)
        self.book.refresh_from_db()
        self.assertEqual(self.book.review_count, 1)
        self.assertEqual(float(self.book.average_rating), 4.0)

    def test_second_post_from_same_user_edits_not_duplicates(self):
        self.reader_client.post(self.reviews_url, {"rating": 2, "review_text": "meh"}, format="json")
        second = self.reader_client.post(self.reviews_url, {"rating": 5, "review_text": "actually great"}, format="json")
        self.assertEqual(second.status_code, 201)

        from apps.books.models import BookReview

        self.assertEqual(BookReview.objects.filter(book=self.book, user=self.reader).count(), 1)
        self.book.refresh_from_db()
        self.assertEqual(self.book.review_count, 1)
        self.assertEqual(float(self.book.average_rating), 5.0)

    def test_rating_out_of_range_is_rejected(self):
        resp = self.reader_client.post(self.reviews_url, {"rating": 9, "review_text": "x"}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_reviews_list_is_public(self):
        self.reader_client.post(self.reviews_url, {"rating": 3, "review_text": "ok"}, format="json")
        anon_resp = APIClient().get(self.reviews_url)
        self.assertEqual(anon_resp.status_code, 200)
        self.assertEqual(len(anon_resp.json()), 1)

    def test_delete_own_review_recomputes_aggregate(self):
        created = self.reader_client.post(self.reviews_url, {"rating": 5, "review_text": "x"}, format="json")
        review_id = created.json()["id"]
        self.book.refresh_from_db()
        self.assertEqual(self.book.review_count, 1)

        bob = make_user("109")
        bob_client = APIClient()
        bob_client.force_authenticate(user=bob)
        bob_delete = bob_client.delete(f"/api/v1/books/reviews/{review_id}/")
        self.assertEqual(bob_delete.status_code, 204)  # no-op, not an error that reveals existence

        from apps.books.models import BookReview

        self.assertTrue(BookReview.objects.filter(id=review_id).exists())  # still there — Bob didn't own it

        own_delete = self.reader_client.delete(f"/api/v1/books/reviews/{review_id}/")
        self.assertEqual(own_delete.status_code, 204)
        self.book.refresh_from_db()
        self.assertEqual(self.book.review_count, 0)
        self.assertEqual(float(self.book.average_rating), 0.0)

    def test_report_review_creates_moderation_flag(self):
        created = self.reader_client.post(self.reviews_url, {"rating": 1, "review_text": "spam spam spam"}, format="json")
        review_id = created.json()["id"]

        bob = make_user("110")
        bob_client = APIClient()
        bob_client.force_authenticate(user=bob)
        resp = bob_client.post(f"/api/v1/books/reviews/{review_id}/report/", {"reason": "Spam"}, format="json")
        self.assertEqual(resp.status_code, 201)

        from apps.moderation.models import Flag

        self.assertTrue(Flag.objects.filter(target_type="book_review", target_id=review_id, reporter_id=bob.id).exists())


class BookReviewModerationTakedownTests(TestCase):
    """A reported review goes through the SAME generic platform moderation
    queue as a reported book (admin_control.views.content_moderation) —
    this locks in that the 'book_review' target_type is actually wired
    into that dispatcher, not just reachable via the books app's own
    report endpoint."""

    def test_takedown_deletes_review_and_recomputes_rating(self):
        from admin_control.views.content_moderation import _apply_content_takedown
        from apps.books.models import BookReview
        from apps.moderation.models import Flag

        owner = make_user("113")
        book = make_book(owner, status=BookStatus.PUBLISHED)
        reader = make_user("114")
        review = BookReview.objects.create(book=book, user=reader, rating=1, review_text="spam")
        services.recompute_book_rating(book)
        book.refresh_from_db()
        self.assertEqual(book.review_count, 1)

        flag = Flag.objects.create(
            source="USER", target_type="book_review", target_id=review.id,
            reporter_id=make_user("115").id, reason="Spam", severity="LOW", status="PENDING",
        )
        go_user = make_user("116", is_staff=True)
        result = _apply_content_takedown(flag, actor=go_user)

        self.assertTrue(result)
        self.assertFalse(BookReview.objects.filter(id=review.id).exists())
        book.refresh_from_db()
        self.assertEqual(book.review_count, 0)


class ChapterScrapingDetectionTests(TestCase):
    """apps.books.services.record_chapter_read_and_detect_scraping — a
    best-effort bulk-scraping signal, never an access-control gate (see
    its own docstring for why false positives must stay harmless)."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()  # the scrape-detection counters live in the default cache

    def test_crossing_threshold_creates_exactly_one_flag(self):
        from django.test import RequestFactory
        from apps.moderation.models import Flag

        user = make_user("125")
        request = RequestFactory().get("/api/v1/books/x/chapters/y/")
        for _ in range(services.CHAPTER_READ_SCRAPE_THRESHOLD):
            services.record_chapter_read_and_detect_scraping(user, request)
        self.assertEqual(
            Flag.objects.filter(target_type="book_scraping_suspected", target_id=user.id).count(), 1,
        )

        # Continuing past the threshold in the same window must not spam
        # a second Flag for every subsequent request.
        for _ in range(5):
            services.record_chapter_read_and_detect_scraping(user, request)
        self.assertEqual(
            Flag.objects.filter(target_type="book_scraping_suspected", target_id=user.id).count(), 1,
        )

    def test_below_threshold_does_not_flag(self):
        from django.test import RequestFactory
        from apps.moderation.models import Flag

        user = make_user("126")
        request = RequestFactory().get("/x")
        for _ in range(services.CHAPTER_READ_SCRAPE_THRESHOLD - 1):
            services.record_chapter_read_and_detect_scraping(user, request)
        self.assertEqual(Flag.objects.filter(target_type="book_scraping_suspected").count(), 0)

    def test_two_different_users_tracked_independently(self):
        from django.test import RequestFactory
        from apps.moderation.models import Flag

        alice = make_user("127")
        bob = make_user("128")
        request = RequestFactory().get("/x")
        for _ in range(services.CHAPTER_READ_SCRAPE_THRESHOLD):
            services.record_chapter_read_and_detect_scraping(alice, request)
        for _ in range(3):
            services.record_chapter_read_and_detect_scraping(bob, request)
        self.assertTrue(Flag.objects.filter(target_type="book_scraping_suspected", target_id=alice.id).exists())
        self.assertFalse(Flag.objects.filter(target_type="book_scraping_suspected", target_id=bob.id).exists())

    def test_real_chapter_endpoint_wires_through_detection(self):
        from apps.moderation.models import Flag

        owner = make_user("129")
        book = make_book(owner, status=BookStatus.PUBLISHED)
        chapter = book.chapters.first()
        reader = make_user("130")
        client = APIClient()
        client.force_authenticate(user=reader)
        url = f"/api/v1/books/{book.id}/chapters/{chapter.stable_id}/"
        for _ in range(services.CHAPTER_READ_SCRAPE_THRESHOLD):
            resp = client.get(url)
            self.assertEqual(resp.status_code, 200)
        self.assertTrue(
            Flag.objects.filter(target_type="book_scraping_suspected", target_id=reader.id).exists(),
        )


class RoyaltyAndRefundTests(TestCase):
    """Exercises the real webhook wiring (apps.billing.direct_payments'
    _mark_target_paid) for royalty creation, then the refund domain flow
    (apps.books.services.refund_purchase) end to end: provider refund call,
    purchase status, entitlement revocation, and royalty reversal."""

    def setUp(self):
        from django.core.management import call_command

        call_command("seed_books_launch")
        self.author = make_user("119")
        self.shop = make_verified_shop(self.author)
        self.buyer = make_user("120")
        self.book = make_book(self.author, is_free=False, price_cents=500, status=BookStatus.PUBLISHED)
        self.book.publisher_shop = self.shop
        self.book.save()

    def _pay_for_book(self):
        from apps.billing.direct_payments import _mark_target_paid
        from apps.billing.models import DirectPaymentIntent
        from apps.books.models import BookPurchase

        purchase = BookPurchase.objects.create(book=self.book, buyer=self.buyer, amount_cents=500, currency="USD")
        intent = DirectPaymentIntent.objects.create(
            user=self.buyer, provider="flutterwave", target_type=DirectPaymentIntent.TARGET_BOOK_PURCHASE,
            target_id=purchase.id, amount_cents=500, currency="USD",
            status=DirectPaymentIntent.STATUS_PENDING, tx_ref=f"kis_direct_test_{uuid.uuid4().hex}",
        )
        _mark_target_paid(intent, {"id": "provider_txn_royalty"})
        purchase.refresh_from_db()
        return purchase

    def test_paid_webhook_records_royalty_matching_platform_commission(self):
        from apps.books.models import BookRoyaltyRecord

        purchase = self._pay_for_book()
        royalty = BookRoyaltyRecord.objects.get(purchase=purchase)
        self.assertEqual(royalty.status, BookRoyaltyRecord.STATUS_EARNED)
        self.assertEqual(royalty.gross_amount_cents, 500)
        self.assertEqual(float(royalty.commission_pct), 10.0)  # flat PLATFORM_COMMISSION_PCT default
        self.assertEqual(royalty.commission_cents, 50)
        self.assertEqual(royalty.net_royalty_cents, 450)
        self.assertEqual(royalty.author_id, self.author.id)

    def test_replayed_webhook_does_not_duplicate_royalty(self):
        from apps.billing.direct_payments import _mark_target_paid
        from apps.billing.models import DirectPaymentIntent
        from apps.books.models import BookRoyaltyRecord

        purchase = self._pay_for_book()
        intent = DirectPaymentIntent.objects.get(target_id=purchase.id, target_type=DirectPaymentIntent.TARGET_BOOK_PURCHASE)
        _mark_target_paid(intent, {"id": "provider_txn_royalty"})  # duplicate delivery
        self.assertEqual(BookRoyaltyRecord.objects.filter(purchase=purchase).count(), 1)

    def test_refund_requires_books_refund_permission(self):
        purchase = self._pay_for_book()
        plain_staff = make_role_user("121", ["Book Administrator"])  # review/restrict/ban, NOT refund
        client = APIClient()
        client.force_authenticate(user=plain_staff)
        resp = client.post(f"/api/v1/books/purchases/{purchase.id}/refund/", {"reason": "duplicate charge"}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_successful_refund_reverses_royalty_and_revokes_entitlement(self):
        from unittest.mock import patch
        from apps.books.models import BookEntitlement, BookRoyaltyRecord

        purchase = self._pay_for_book()
        finance_officer = make_role_user("122", ["Book Finance Officer"])
        client = APIClient()
        client.force_authenticate(user=finance_officer)

        with patch(
            "apps.billing.direct_payments.refund_flutterwave_transaction",
            return_value={"id": "flw_refund_1"},
        ):
            resp = client.post(
                f"/api/v1/books/purchases/{purchase.id}/refund/", {"reason": "duplicate charge"}, format="json",
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["status"], "refunded")

        purchase.refresh_from_db()
        self.assertEqual(purchase.status, purchase.STATUS_REFUNDED)
        self.assertEqual(purchase.refund_provider, "flutterwave")

        entitlement = BookEntitlement.objects.get(book=self.book, user=self.buyer)
        self.assertFalse(entitlement.is_active)

        royalty = BookRoyaltyRecord.objects.get(purchase=purchase)
        self.assertEqual(royalty.status, BookRoyaltyRecord.STATUS_REVERSED)
        self.assertIsNotNone(royalty.reversed_at)

    def test_failed_provider_refund_leaves_everything_unchanged(self):
        from unittest.mock import patch
        from apps.books.models import BookEntitlement, BookRoyaltyRecord

        purchase = self._pay_for_book()
        finance_officer = make_role_user("123", ["Book Finance Officer"])
        client = APIClient()
        client.force_authenticate(user=finance_officer)

        with patch(
            "apps.billing.direct_payments.refund_flutterwave_transaction",
            side_effect=ValueError("provider down"),
        ):
            resp = client.post(
                f"/api/v1/books/purchases/{purchase.id}/refund/", {"reason": "duplicate charge"}, format="json",
            )
        self.assertEqual(resp.status_code, 400)

        purchase.refresh_from_db()
        self.assertEqual(purchase.status, purchase.STATUS_PAID)  # unchanged
        self.assertTrue(BookEntitlement.objects.get(book=self.book, user=self.buyer).is_active)
        self.assertEqual(BookRoyaltyRecord.objects.get(purchase=purchase).status, BookRoyaltyRecord.STATUS_EARNED)

    def test_stripe_purchase_refund_fails_honestly_not_silently(self):
        from apps.books.models import BookPurchase

        purchase = self._pay_for_book()
        purchase.payment_method = "stripe"
        purchase.save(update_fields=["payment_method"])
        finance_officer = make_role_user("124", ["Book Finance Officer"])
        client = APIClient()
        client.force_authenticate(user=finance_officer)

        resp = client.post(f"/api/v1/books/purchases/{purchase.id}/refund/", {"reason": "x"}, format="json")
        self.assertEqual(resp.status_code, 400)
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, BookPurchase.STATUS_PAID)

    def test_my_royalties_view_shows_lifetime_totals(self):
        self._pay_for_book()
        client = APIClient()
        client.force_authenticate(user=self.author)
        resp = client.get("/api/v1/books/royalties/mine/")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["lifetime_net_earned_cents"], 450)
        self.assertEqual(len(body["records"]), 1)


class OfflineDownloadTests(TestCase):
    """Covers the server-testable half of the offline-downloads feature:
    authorization, encryption/authentication, idempotency, versioning,
    and revocation. Decryption-and-render itself happens on-device (see
    KIS/src/services/books/offlineCrypto.ts) and is verified there."""

    def setUp(self):
        self.author = make_user("140")
        self.reader = make_user("141")
        self.client = APIClient()
        self.client.force_authenticate(user=self.reader)

    def _free_book(self):
        return make_book(self.author, is_free=True, status=BookStatus.PUBLISHED)

    def _paid_book(self):
        shop = make_verified_shop(self.author)
        book = make_book(self.author, is_free=False, price_cents=999, status=BookStatus.PUBLISHED)
        book.publisher_shop = shop
        book.save()
        return book

    def test_free_book_download_authorized(self):
        book = self._free_book()
        resp = self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        self.assertIn("package_dek", body)
        self.assertIn("manifest", body)
        self.assertIn("chapters", body)

    def test_paid_book_download_without_purchase_rejected(self):
        book = self._paid_book()
        resp = self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_paid_book_download_with_entitlement_succeeds(self):
        from apps.books.models import BookEntitlement

        book = self._paid_book()
        services.grant_entitlement(book, self.reader, source=BookEntitlement.SOURCE_PURCHASE)
        resp = self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)

    def test_anonymous_cannot_request_download(self):
        book = self._free_book()
        resp = APIClient().post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json")
        self.assertEqual(resp.status_code, 401)

    def test_package_contains_no_readable_manuscript_text(self):
        book = self._free_book()
        chapter = book.chapters.first()
        plaintext_snippet = chapter.content_blocks[0]["text"]
        resp = self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json")
        raw_body = resp.content.decode()
        self.assertNotIn(plaintext_snippet, raw_body)
        self.assertNotIn(book.title, raw_body)
        self.assertNotIn(book.author_name, raw_body)

    def test_two_different_devices_get_independent_keys(self):
        book = self._free_book()
        r1 = self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-a"}, format="json").json()
        r2 = self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-b"}, format="json").json()
        self.assertNotEqual(r1["package_dek"], r2["package_dek"])
        self.assertNotEqual(r1["license_id"], r2["license_id"])

    def test_sign_out_and_different_account_gets_independent_license(self):
        # Same device_id, different user — licenses must not collide or
        # leak one user's key to another just because the device matches.
        book = self._free_book()
        bob = make_user("142")
        bob_client = APIClient()
        bob_client.force_authenticate(user=bob)
        r1 = self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "shared-dev"}, format="json").json()
        r2 = bob_client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "shared-dev"}, format="json").json()
        self.assertNotEqual(r1["license_id"], r2["license_id"])
        self.assertNotEqual(r1["package_dek"], r2["package_dek"])

    def test_tampered_chapter_ciphertext_fails_decryption(self):
        from apps.books import offline_crypto

        book = self._free_book()
        body = self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json").json()
        dek = base64.b64decode(body["package_dek"])
        chapter_stable_id = list(body["chapters"].keys())[0]
        tampered = dict(body["chapters"][chapter_stable_id])
        raw = bytearray(base64.b64decode(tampered["ciphertext"]))
        raw[0] ^= 0xFF
        tampered["ciphertext"] = base64.b64encode(bytes(raw)).decode()
        with self.assertRaises(Exception):
            offline_crypto.decrypt_part(
                dek, tampered, book_id=str(book.id), license_id=body["license_id"],
                package_version=body["package_version"], part=f"chapter:{chapter_stable_id}:0",
            )

    def test_reordered_chapters_fail_decryption(self):
        from apps.books import offline_crypto
        from apps.books.models import BookChapter

        book = make_book(self.author, status=BookStatus.PUBLISHED, with_chapter=False)
        blocks1 = services.assign_block_ids([{"type": "paragraph", "text": "Chapter one text."}])
        blocks2 = services.assign_block_ids([{"type": "paragraph", "text": "Chapter two text."}])
        c1 = BookChapter.objects.create(book=book, order=1, title="One", content_blocks=blocks1, word_count=3)
        c2 = BookChapter.objects.create(book=book, order=2, title="Two", content_blocks=blocks2, word_count=3)
        body = self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json").json()
        dek = base64.b64decode(body["package_dek"])
        part1 = body["chapters"][str(c1.stable_id)]
        with self.assertRaises(Exception):
            offline_crypto.decrypt_part(
                dek, part1, book_id=str(book.id), license_id=body["license_id"],
                package_version=body["package_version"], part=f"chapter:{c2.stable_id}:{c2.order}",
            )

    def test_corrupted_manifest_rejected(self):
        from apps.books import offline_crypto

        book = self._free_book()
        body = self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json").json()
        dek = base64.b64decode(body["package_dek"])
        manifest = dict(body["manifest"])
        manifest["nonce"] = base64.b64encode(os.urandom(12)).decode()
        with self.assertRaises(Exception):
            offline_crypto.decrypt_part(
                dek, manifest, book_id=str(book.id), license_id=body["license_id"],
                package_version=body["package_version"], part="manifest",
            )

    def test_missing_chapter_is_detectable_against_manifest(self):
        from apps.books import offline_crypto

        book = self._free_book()
        body = self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json").json()
        dek = base64.b64decode(body["package_dek"])
        manifest = offline_crypto.decrypt_part(
            dek, body["manifest"], book_id=str(book.id), license_id=body["license_id"],
            package_version=body["package_version"], part="manifest",
        )
        declared_ids = {c["stable_id"] for c in manifest["chapters"]}
        delivered_ids = set(body["chapters"].keys())
        self.assertEqual(declared_ids, delivered_ids)  # the real check a client performs
        # Simulate a dropped chapter and confirm the mismatch is detectable.
        incomplete = dict(body["chapters"])
        incomplete.pop(next(iter(incomplete)))
        self.assertNotEqual(declared_ids, set(incomplete.keys()))

    def test_idempotent_retry_reuses_same_license_and_key(self):
        from apps.books.models import BookOfflineLicense

        book = self._free_book()
        r1 = self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json").json()
        r2 = self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json").json()
        self.assertEqual(r1["license_id"], r2["license_id"])
        self.assertEqual(r1["package_dek"], r2["package_dek"])
        self.assertEqual(BookOfflineLicense.objects.filter(book=book, user=self.reader, device_id="dev-1").count(), 1)

    def test_content_edit_bumps_package_version_and_issues_fresh_key(self):
        book = self._free_book()
        r1 = self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json").json()
        book.content_version += 1
        book.save(update_fields=["content_version"])
        r2 = self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json").json()
        self.assertEqual(r2["package_version"], r1["package_version"] + 1)
        self.assertNotEqual(r2["package_dek"], r1["package_dek"])

    def test_verify_reports_update_available_after_content_edit(self):
        book = self._free_book()
        self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json")
        book.content_version += 1
        book.save(update_fields=["content_version"])
        resp = self.client.get(f"/api/v1/books/{book.id}/offline/verify/?device_id=dev-1")
        self.assertTrue(resp.json()["valid"])
        self.assertTrue(resp.json()["update_available"])

    def test_verify_extends_expiry(self):
        from apps.books.models import BookOfflineLicense

        book = self._free_book()
        self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json")
        license_row = BookOfflineLicense.objects.get(book=book, user=self.reader, device_id="dev-1")
        license_row.expires_at = timezone.now() + timedelta(days=1)
        license_row.save(update_fields=["expires_at"])
        self.client.get(f"/api/v1/books/{book.id}/offline/verify/?device_id=dev-1")
        license_row.refresh_from_db()
        self.assertGreater(license_row.expires_at, timezone.now() + timedelta(days=10))

    def test_revoked_license_fails_verify(self):
        book = self._free_book()
        self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json")
        services.revoke_offline_license(book, self.reader, device_id="dev-1", reason="test revoke")
        resp = self.client.get(f"/api/v1/books/{book.id}/offline/verify/?device_id=dev-1")
        self.assertFalse(resp.json()["valid"])

    def test_nonexistent_license_fails_verify(self):
        book = self._free_book()
        resp = self.client.get(f"/api/v1/books/{book.id}/offline/verify/?device_id=never-downloaded")
        self.assertFalse(resp.json()["valid"])
        self.assertEqual(resp.json()["reason"], "no_license")

    def test_remove_download_revokes_license(self):
        from apps.books.models import BookOfflineLicense

        book = self._free_book()
        self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json")
        resp = self.client.delete(f"/api/v1/books/{book.id}/offline/dev-1/")
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(
            BookOfflineLicense.objects.get(book=book, user=self.reader, device_id="dev-1").status,
            BookOfflineLicense.STATUS_REVOKED,
        )

    def test_refund_revokes_offline_license(self):
        from unittest.mock import patch

        from apps.books.models import BookEntitlement, BookOfflineLicense, BookPurchase

        book = self._paid_book()
        services.grant_entitlement(book, self.reader, source=BookEntitlement.SOURCE_PURCHASE)
        self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json")

        purchase = BookPurchase.objects.create(
            book=book, buyer=self.reader, amount_cents=999, currency="USD",
            status=BookPurchase.STATUS_PAID, payment_method="flutterwave", transaction_reference="tx_1",
        )
        with patch("apps.billing.direct_payments.refund_flutterwave_transaction", return_value={"id": "r1"}):
            services.refund_purchase(purchase, actor=self.author, reason="dup charge")

        self.assertEqual(
            BookOfflineLicense.objects.get(book=book, user=self.reader, device_id="dev-1").status,
            BookOfflineLicense.STATUS_REVOKED,
        )

    def test_book_suspension_revokes_every_readers_offline_licenses(self):
        from apps.books.models import BookOfflineLicense

        book = self._free_book()
        other_reader = make_user("143")
        other_client = APIClient()
        other_client.force_authenticate(user=other_reader)
        self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json")
        other_client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-2"}, format="json")

        services.apply_moderation_decision(book, action="block", actor=self.author, notes="policy violation")

        self.assertEqual(
            BookOfflineLicense.objects.filter(book=book, status=BookOfflineLicense.STATUS_ACTIVE).count(), 0,
        )

    def test_my_offline_downloads_view_excludes_other_users(self):
        book = self._free_book()
        other_reader = make_user("144")
        other_client = APIClient()
        other_client.force_authenticate(user=other_reader)
        self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json")
        resp = other_client.get("/api/v1/books/offline/mine/")
        self.assertEqual(resp.json(), [])

    def test_offline_license_listing_never_exposes_wrapped_dek(self):
        book = self._free_book()
        self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json")
        resp = self.client.get("/api/v1/books/offline/mine/")
        self.assertNotIn("wrapped_package_dek", resp.json()[0])

    def test_package_size_guard_rejects_oversized_book(self):
        from apps.books.models import BookChapter

        book = make_book(self.author, status=BookStatus.PUBLISHED, with_chapter=False)
        huge_text = "x" * (services.OFFLINE_PACKAGE_MAX_PLAINTEXT_BYTES + 1000)
        blocks = services.assign_block_ids([{"type": "paragraph", "text": huge_text}])
        BookChapter.objects.create(book=book, order=1, title="One", content_blocks=blocks, word_count=1)
        resp = self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json")
        self.assertEqual(resp.status_code, 400)


class OfflineImageBundlingTests(TestCase):
    """Closes the gap: a book's manuscript images must be bundled into
    the encrypted offline package (not just rendered online), with the
    exact same per-part AEAD scheme chapters already use."""

    def setUp(self):
        self.author = make_user("150")
        self.reader = make_user("151")
        self.client = APIClient()
        self.client.force_authenticate(user=self.reader)

    def _book_with_images(self, *, published=True):
        from apps.books.models import BookChapter

        book = Book.objects.create(
            owner=self.author, title="Illustrated", author_name="A",
            is_free=True, status=BookStatus.PUBLISHED if published else BookStatus.DRAFT,
        )
        png = make_png_bytes((50, 100, 150))
        asset = services.create_or_get_image_asset(book, data=png, content_type="image/png")
        blocks = services.assign_block_ids([
            {"type": "paragraph", "text": "Before the picture."},
            {"type": "image", "asset_id": str(asset.id), "extraction_status": "extracted", "width": asset.width, "height": asset.height, "caption": "A figure"},
            {"type": "paragraph", "text": "After the picture."},
            # Same asset referenced twice — proves dedup-on-bundle, not duplication.
            {"type": "image", "asset_id": str(asset.id), "extraction_status": "extracted", "width": asset.width, "height": asset.height},
        ])
        BookChapter.objects.create(book=book, order=1, title="One", content_blocks=blocks, word_count=4)
        return book, asset, png

    def test_offline_package_includes_and_decrypts_the_image(self):
        from apps.books import offline_crypto

        book, asset, original_png = self._book_with_images()
        resp = self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        dek = base64.b64decode(body["package_dek"])

        self.assertIn(str(asset.id), body["images"])
        decoded_b64 = offline_crypto.decrypt_part(
            dek, body["images"][str(asset.id)], book_id=str(book.id), license_id=body["license_id"],
            package_version=body["package_version"], part=f"image:{asset.id}",
        )
        decrypted_bytes = base64.b64decode(decoded_b64)
        self.assertEqual(decrypted_bytes, original_png)

    def test_repeated_image_at_two_placements_is_bundled_exactly_once(self):
        book, asset, _png = self._book_with_images()
        resp = self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json").json()
        self.assertEqual(len(resp["images"]), 1)  # one asset, even though 2 blocks reference it

    def test_manifest_lists_image_metadata(self):
        from apps.books import offline_crypto

        book, asset, _png = self._book_with_images()
        body = self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json").json()
        dek = base64.b64decode(body["package_dek"])
        manifest = offline_crypto.decrypt_part(
            dek, body["manifest"], book_id=str(book.id), license_id=body["license_id"],
            package_version=body["package_version"], part="manifest",
        )
        self.assertEqual(len(manifest["images"]), 1)
        self.assertEqual(manifest["images"][0]["asset_id"], str(asset.id))
        self.assertEqual(manifest["images"][0]["content_type"], "image/png")

    def test_image_free_book_has_an_empty_images_section_backward_compatible(self):
        book = make_book(self.author, status=BookStatus.PUBLISHED)  # no images
        body = self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json").json()
        self.assertEqual(body["images"], {})

    def test_tampered_image_ciphertext_fails_decryption(self):
        from apps.books import offline_crypto

        book, asset, _png = self._book_with_images()
        body = self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json").json()
        dek = base64.b64decode(body["package_dek"])
        tampered = dict(body["images"][str(asset.id)])
        raw = bytearray(base64.b64decode(tampered["ciphertext"]))
        raw[0] ^= 0xFF
        tampered["ciphertext"] = base64.b64encode(bytes(raw)).decode()
        with self.assertRaises(Exception):
            offline_crypto.decrypt_part(
                dek, tampered, book_id=str(book.id), license_id=body["license_id"],
                package_version=body["package_version"], part=f"image:{asset.id}",
            )

    def test_missing_image_part_is_detectable_against_manifest(self):
        from apps.books import offline_crypto

        book, asset, _png = self._book_with_images()
        body = self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json").json()
        dek = base64.b64decode(body["package_dek"])
        manifest = offline_crypto.decrypt_part(
            dek, body["manifest"], book_id=str(book.id), license_id=body["license_id"],
            package_version=body["package_version"], part="manifest",
        )
        declared_asset_ids = {i["asset_id"] for i in manifest["images"]}
        delivered_asset_ids = set(body["images"].keys())
        self.assertEqual(declared_asset_ids, delivered_asset_ids)
        incomplete = dict(body["images"])
        incomplete.pop(next(iter(incomplete)))
        self.assertNotEqual(declared_asset_ids, set(incomplete.keys()))  # a dropped image part is detectable

    def test_oversized_image_bundle_rejected_by_existing_package_size_guard(self):
        from unittest.mock import patch

        book, asset, _png = self._book_with_images()
        with patch("apps.books.services.OFFLINE_PACKAGE_MAX_PLAINTEXT_BYTES", 10):
            resp = self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_image_bundling_respects_entitlement_and_revocation(self):
        book, asset, _png = self._book_with_images(published=True)
        shop = make_verified_shop(self.author)
        book.is_free = False
        book.price_cents = 500
        book.publisher_shop = shop
        book.save()

        denied = self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json")
        self.assertEqual(denied.status_code, 403)  # no entitlement — image bundling never bypasses this gate

        BookEntitlement.objects.create(book=book, user=self.reader, source="grant")
        allowed = self.client.post(f"/api/v1/books/{book.id}/offline/request/", {"device_id": "dev-1"}, format="json")
        self.assertEqual(allowed.status_code, 201)
        self.assertIn(str(asset.id), allowed.json()["images"])

        services.revoke_offline_license(book, self.reader, device_id="dev-1")
        status_after_revoke = services.verify_offline_license(book, self.reader, device_id="dev-1")
        self.assertFalse(status_after_revoke["valid"])


class AuthorStudioStatsTests(TestCase):
    """average_rating/review_count/view_count are author-tool additions to
    AuthorBookSerializer (so AuthorStudioScreen can show an author their
    own book's real reception) — read-only, never settable via PATCH."""

    def test_stats_are_visible_but_not_writable_by_author(self):
        owner = make_user("117")
        book = make_book(owner, status=BookStatus.PUBLISHED)
        reader = make_user("118")
        reader_client = APIClient()
        reader_client.force_authenticate(user=reader)
        reader_client.post(f"/api/v1/books/{book.id}/reviews/", {"rating": 4, "review_text": "x"}, format="json")

        owner_client = APIClient()
        owner_client.force_authenticate(user=owner)
        detail = owner_client.get(f"/api/v1/books/mine/{book.id}/").json()
        self.assertEqual(detail["review_count"], 1)
        self.assertEqual(float(detail["average_rating"]), 4.0)

        # Attempting to forge these via PATCH is silently ignored (DRF
        # drops read_only_fields from validated_data) — the real values
        # stay server-computed.
        owner_client.patch(f"/api/v1/books/mine/{book.id}/", {"average_rating": 1, "review_count": 999}, format="json")
        book.refresh_from_db()
        self.assertEqual(book.review_count, 1)
        self.assertEqual(float(book.average_rating), 4.0)


class DiscoveryAndSortTests(TestCase):
    def setUp(self):
        self.owner = make_user("111")

    def test_discover_only_shows_published_books(self):
        make_book(self.owner, status=BookStatus.PUBLISHED)
        make_book(self.owner, status=BookStatus.DRAFT)
        resp = APIClient().get("/api/v1/books/discover/")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("new_releases", body)
        self.assertIn("top_rated", body)
        self.assertIn("trending", body)
        self.assertIn("free_books", body)
        self.assertEqual(len(body["new_releases"]), 1)
        self.assertEqual(len(body["free_books"]), 1)
        self.assertEqual(len(body["top_rated"]), 0)  # no reviews yet

    def test_sort_rating_orders_by_average_rating(self):
        low = make_book(self.owner, status=BookStatus.PUBLISHED)
        high = make_book(self.owner, status=BookStatus.PUBLISHED)
        reader = make_user("112")
        reader_client = APIClient()
        reader_client.force_authenticate(user=reader)
        reader_client.post(f"/api/v1/books/{low.id}/reviews/", {"rating": 2, "review_text": "x"}, format="json")
        reader_client.post(f"/api/v1/books/{high.id}/reviews/", {"rating": 5, "review_text": "x"}, format="json")

        resp = APIClient().get("/api/v1/books/?sort=rating")
        ids = [b["id"] for b in resp.json()["results"]]
        self.assertEqual(ids[0], str(high.id))

    def test_sort_popular_orders_by_view_count(self):
        quiet = make_book(self.owner, status=BookStatus.PUBLISHED)
        popular = make_book(self.owner, status=BookStatus.PUBLISHED)
        Book.objects.filter(id=popular.id).update(view_count=50)

        resp = APIClient().get("/api/v1/books/?sort=popular")
        ids = [b["id"] for b in resp.json()["results"]]
        self.assertEqual(ids[0], str(popular.id))


class BookmarkHighlightNoteOwnershipTests(TestCase):
    """Validates the explicit security requirement: a user may only ever
    see/mutate their OWN private notes, highlights, and bookmarks — never
    another user's, even for the exact same book/chapter."""

    def setUp(self):
        self.owner = make_user("102")
        self.book = make_book(self.owner, status=BookStatus.PUBLISHED)
        self.chapter = self.book.chapters.first()
        self.block_id = self.chapter.content_blocks[0]["id"]
        self.alice = make_user("103")
        self.bob = make_user("104")
        self.alice_client = APIClient()
        self.alice_client.force_authenticate(user=self.alice)
        self.bob_client = APIClient()
        self.bob_client.force_authenticate(user=self.bob)

    def test_chapter_bookmark_toggle_is_idempotent_and_per_user(self):
        url = f"/api/v1/books/{self.book.id}/chapters/{self.chapter.stable_id}/bookmark/"
        first = self.alice_client.post(url, {}, format="json")
        self.assertEqual(first.status_code, 201)
        self.assertTrue(first.json()["bookmarked"])

        # Toggling again removes it.
        second = self.alice_client.post(url, {}, format="json")
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.json()["bookmarked"])

        # Re-add, then confirm Bob's own bookmark list is independent.
        self.alice_client.post(url, {}, format="json")
        self.bob_client.post(url, {}, format="json")
        from apps.books.models import BookChapterBookmark

        self.assertEqual(BookChapterBookmark.objects.filter(book=self.book).count(), 2)
        alice_bookmarks = self.alice_client.get(f"/api/v1/books/{self.book.id}/bookmarks/").json()
        self.assertEqual(len(alice_bookmarks), 1)

    def test_highlight_creation_is_idempotent_on_retry(self):
        url = f"/api/v1/books/{self.book.id}/chapters/{self.chapter.stable_id}/highlights/"
        payload = {"block_id": self.block_id, "start_offset": 0, "end_offset": 5, "color": "green"}
        first = self.alice_client.post(url, payload, format="json")
        self.assertEqual(first.status_code, 201)
        retry = self.alice_client.post(url, payload, format="json")  # simulated network-retry resend
        self.assertEqual(retry.status_code, 201)
        self.assertEqual(first.json()["id"], retry.json()["id"])  # same row, not a duplicate

        from apps.books.models import BookHighlight

        self.assertEqual(BookHighlight.objects.filter(user=self.alice, chapter=self.chapter).count(), 1)

    def test_highlight_on_nonexistent_block_is_rejected(self):
        url = f"/api/v1/books/{self.book.id}/chapters/{self.chapter.stable_id}/highlights/"
        resp = self.alice_client.post(url, {"block_id": "not-a-real-block-id", "start_offset": 0, "end_offset": 5}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_bob_cannot_see_or_delete_alices_highlight(self):
        url = f"/api/v1/books/{self.book.id}/chapters/{self.chapter.stable_id}/highlights/"
        created = self.alice_client.post(url, {"block_id": self.block_id, "start_offset": 0, "end_offset": 5}, format="json")
        highlight_id = created.json()["id"]

        bob_view = self.bob_client.get(url)
        self.assertEqual(bob_view.json(), [])  # Bob's own (empty) list, not Alice's

        bob_delete = self.bob_client.delete(f"/api/v1/books/highlights/{highlight_id}/")
        self.assertEqual(bob_delete.status_code, 204)  # no-op, not an error that reveals existence
        from apps.books.models import BookHighlight

        self.assertTrue(BookHighlight.objects.filter(id=highlight_id).exists())  # Alice's highlight is untouched

    def test_bob_cannot_read_update_or_delete_alices_note(self):
        url = f"/api/v1/books/{self.book.id}/chapters/{self.chapter.stable_id}/notes/"
        created = self.alice_client.post(url, {"text": "This is my private reflection."}, format="json")
        note_id = created.json()["id"]

        bob_list = self.bob_client.get(url)
        self.assertEqual(bob_list.json(), [])

        bob_patch = self.bob_client.patch(f"/api/v1/books/notes/{note_id}/", {"text": "tampered"}, format="json")
        self.assertEqual(bob_patch.status_code, 404)  # Bob can't even address Alice's note

        from apps.books.models import BookNote

        self.assertEqual(BookNote.objects.get(id=note_id).text, "This is my private reflection.")

    def test_note_creation_with_client_request_id_is_idempotent(self):
        url = f"/api/v1/books/{self.book.id}/chapters/{self.chapter.stable_id}/notes/"
        payload = {"text": "Retry-safe note.", "client_request_id": "retry-token-abc"}
        first = self.alice_client.post(url, payload, format="json")
        retry = self.alice_client.post(url, payload, format="json")
        self.assertEqual(first.json()["id"], retry.json()["id"])

        from apps.books.models import BookNote

        self.assertEqual(BookNote.objects.filter(user=self.alice, client_request_id="retry-token-abc").count(), 1)

    def test_distinct_notes_without_client_request_id_are_not_deduped(self):
        url = f"/api/v1/books/{self.book.id}/chapters/{self.chapter.stable_id}/notes/"
        self.alice_client.post(url, {"text": "First note."}, format="json")
        self.alice_client.post(url, {"text": "Second, genuinely different note."}, format="json")
        from apps.books.models import BookNote

        self.assertEqual(BookNote.objects.filter(user=self.alice, chapter=self.chapter).count(), 2)

    def test_cannot_highlight_or_note_a_book_without_access(self):
        private_book = make_book(self.owner, status=BookStatus.DRAFT)  # unpublished, Alice has no access
        chapter = private_book.chapters.first()
        block_id = chapter.content_blocks[0]["id"]
        url = f"/api/v1/books/{private_book.id}/chapters/{chapter.stable_id}/highlights/"
        resp = self.alice_client.post(url, {"block_id": block_id, "start_offset": 0, "end_offset": 3}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_save_and_unsave_book_is_idempotent_and_per_user(self):
        save_url = f"/api/v1/books/{self.book.id}/save/"
        self.alice_client.post(save_url, {}, format="json")
        self.alice_client.post(save_url, {}, format="json")  # retried save — no duplicate
        from apps.books.models import BookLibraryItem

        self.assertEqual(BookLibraryItem.objects.filter(user=self.alice, book=self.book).count(), 1)
        self.assertEqual(BookLibraryItem.objects.filter(user=self.bob, book=self.book).count(), 0)

        unsave = self.alice_client.delete(save_url)
        self.assertEqual(unsave.status_code, 204)
        self.assertFalse(BookLibraryItem.objects.filter(user=self.alice, book=self.book).exists())

    def test_cannot_save_an_unpublished_book_with_no_access(self):
        private_book = make_book(self.owner, status=BookStatus.DRAFT)
        resp = self.alice_client.post(f"/api/v1/books/{private_book.id}/save/", {}, format="json")
        self.assertEqual(resp.status_code, 404)


class PersonalLibraryOverviewTests(TestCase):
    def setUp(self):
        self.user = make_user("105")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_overview_returns_four_distinct_buckets(self):
        saved_book = make_book(self.user, status=BookStatus.PUBLISHED)
        reading_book = make_book(self.user, status=BookStatus.PUBLISHED)
        completed_book = make_book(self.user, status=BookStatus.PUBLISHED)
        bookmarked_book = make_book(self.user, status=BookStatus.PUBLISHED)

        self.client.post(f"/api/v1/books/{saved_book.id}/save/", {}, format="json")
        self.client.put(f"/api/v1/books/{reading_book.id}/progress/", {"percent_complete": 0.3, "is_completed": False}, format="json")
        self.client.put(f"/api/v1/books/{completed_book.id}/progress/", {"percent_complete": 1.0, "is_completed": True}, format="json")
        bm_chapter = bookmarked_book.chapters.first()
        self.client.post(f"/api/v1/books/{bookmarked_book.id}/chapters/{bm_chapter.stable_id}/bookmark/", {}, format="json")

        overview = self.client.get("/api/v1/books/library/overview/").json()
        saved_ids = {b["id"] for b in overview["saved"]}
        reading_ids = {b["id"] for b in overview["currently_reading"]}
        completed_ids = {b["id"] for b in overview["completed"]}
        bookmarked_ids = {b["id"] for b in overview["bookmarked"]}

        self.assertIn(str(saved_book.id), saved_ids)
        self.assertIn(str(reading_book.id), reading_ids)
        self.assertIn(str(completed_book.id), completed_ids)
        self.assertIn(str(bookmarked_book.id), bookmarked_ids)
        # Completed book must not also show up as still "currently reading".
        self.assertNotIn(str(completed_book.id), reading_ids)

    def test_overview_gracefully_handles_a_since_suspended_saved_book(self):
        book = make_book(self.user, status=BookStatus.PUBLISHED)
        self.client.post(f"/api/v1/books/{book.id}/save/", {}, format="json")
        book.status = BookStatus.SUSPENDED
        book.save(update_fields=["status"])

        resp = self.client.get("/api/v1/books/library/overview/")
        self.assertEqual(resp.status_code, 200)  # does not error out
        saved = resp.json()["saved"]
        self.assertEqual(saved[0]["status"], "suspended")  # current status surfaced, not hidden or stale

    def test_overview_gracefully_handles_a_deleted_saved_book(self):
        book = make_book(self.user, status=BookStatus.PUBLISHED)
        self.client.post(f"/api/v1/books/{book.id}/save/", {}, format="json")
        book_id = book.id
        book.delete()

        from apps.books.models import BookLibraryItem

        self.assertFalse(BookLibraryItem.objects.filter(book_id=book_id).exists())  # cascaded away
        resp = self.client.get("/api/v1/books/library/overview/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["saved"], [])


def make_png_bytes(color=(200, 30, 30), size=(40, 30)) -> bytes:
    """A tiny, genuinely-decodable PNG — used wherever a test needs real
    image bytes (not a placeholder string) to prove extraction/validation
    actually decodes pixels, not just checks a file extension."""
    import io as _io

    from PIL import Image as PILImage

    buf = _io.BytesIO()
    PILImage.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def make_docx_bytes(parts: list) -> bytes:
    """Builds a real .docx in memory. `parts` is a reading-order mix of
    ("heading"|"paragraph", text) tuples and ("image", png_bytes) tuples
    — exercises the exact same interleaving convert_docx must detect."""
    import io as _io

    import docx

    document = docx.Document()
    for part in parts:
        kind = part[0]
        if kind == "image":
            document.add_picture(_io.BytesIO(part[1]))
        elif kind == "heading":
            document.add_heading(part[1], level=1)
        else:
            document.add_paragraph(part[1])
    buf = _io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def make_pdf_with_image_bytes(text: str, png_bytes: bytes) -> bytes:
    """A real PDF with a genuine text layer AND a genuine embedded
    image — pypdf.extract_text() finds the text, and reader.pages[0].images
    finds the image, independently of each other."""
    import fitz  # PyMuPDF

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=16)
    img_doc = fitz.open(stream=png_bytes, filetype="png")
    rect = img_doc[0].rect
    img_doc.close()
    page.insert_image(fitz.Rect(72, 150, 72 + rect.width, 150 + rect.height), stream=png_bytes)
    out = doc.tobytes()
    doc.close()
    return out


def _as_upload_file(data: bytes, *, name: str = "image.png", content_type: str = "image/png"):
    """A real multipart file upload for APIClient.post(..., format='multipart')
    — exercises ImagePlacementImageView.post's actual request.FILES path,
    not a raw dict a real HTTP client could never send this way."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, data, content_type=content_type)


class HighlightCommentAnnotationCenterTests(TestCase):
    """Feature A: sentence-level highlight/comment anchors, color
    filtering, editing, and the cross-book Highlights & Comments Center
    feed (GET /books/annotations/mine/)."""

    def setUp(self):
        self.owner = make_user("201")
        self.book = make_book(self.owner, status=BookStatus.PUBLISHED)
        self.chapter = self.book.chapters.first()
        self.block = self.chapter.content_blocks[0]
        self.block_id = self.block["id"]
        self.alice = make_user("202")
        self.bob = make_user("203")
        self.alice_client = APIClient()
        self.alice_client.force_authenticate(user=self.alice)
        self.bob_client = APIClient()
        self.bob_client.force_authenticate(user=self.bob)

    def _highlights_url(self):
        return f"/api/v1/books/{self.book.id}/chapters/{self.chapter.stable_id}/highlights/"

    def test_single_sentence_highlight_persists_exact_offsets(self):
        # block text is "Hello there, reader of this chapter." — "Hello there," is chars [0:12]
        resp = self.alice_client.post(self._highlights_url(), {
            "block_id": self.block_id, "start_offset": 0, "end_offset": 12, "color": "yellow",
        }, format="json")
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["start_offset"], 0)
        self.assertEqual(body["end_offset"], 12)
        self.assertEqual(body["highlighted_text"], "Hello there,")

    def test_multi_sentence_span_persists_as_one_highlight(self):
        text = self.block["text"]
        resp = self.alice_client.post(self._highlights_url(), {
            "block_id": self.block_id, "start_offset": 0, "end_offset": len(text), "color": "blue",
        }, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["highlighted_text"], text)

    def test_comment_is_a_highlight_with_a_note_distinct_from_a_plain_highlight(self):
        plain = self.alice_client.post(self._highlights_url(), {
            "block_id": self.block_id, "start_offset": 0, "end_offset": 5, "color": "yellow",
        }, format="json").json()
        commented = self.alice_client.post(self._highlights_url(), {
            "block_id": self.block_id, "start_offset": 6, "end_offset": 11, "color": "green",
            "note": "This part matters to me.",
        }, format="json").json()

        annotations = self.alice_client.get("/api/v1/books/annotations/mine/").json()["results"]
        by_id = {a["id"]: a for a in annotations}
        self.assertFalse(by_id[plain["id"]]["has_comment"])
        self.assertTrue(by_id[commented["id"]]["has_comment"])
        self.assertEqual(by_id[commented["id"]]["note"], "This part matters to me.")

    def test_owner_can_edit_color_and_note_but_not_the_anchor(self):
        created = self.alice_client.post(self._highlights_url(), {
            "block_id": self.block_id, "start_offset": 0, "end_offset": 5, "color": "yellow",
        }, format="json").json()
        patched = self.alice_client.patch(f"/api/v1/books/highlights/{created['id']}/", {
            "color": "pink", "note": "Added after the fact.",
        }, format="json")
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.json()["color"], "pink")
        self.assertEqual(patched.json()["note"], "Added after the fact.")
        self.assertEqual(patched.json()["start_offset"], 0)  # anchor itself is immutable

    def test_other_user_cannot_edit_or_delete_someones_highlight(self):
        created = self.alice_client.post(self._highlights_url(), {
            "block_id": self.block_id, "start_offset": 0, "end_offset": 5,
        }, format="json").json()
        bob_patch = self.bob_client.patch(f"/api/v1/books/highlights/{created['id']}/", {"color": "pink"}, format="json")
        self.assertEqual(bob_patch.status_code, 404)
        bob_delete = self.bob_client.delete(f"/api/v1/books/highlights/{created['id']}/")
        self.assertEqual(bob_delete.status_code, 204)  # no-op, doesn't reveal existence
        self.assertTrue(BookHighlight.objects.filter(id=created["id"]).exists())

    def test_rejects_invalid_color(self):
        created = self.alice_client.post(self._highlights_url(), {
            "block_id": self.block_id, "start_offset": 0, "end_offset": 5,
        }, format="json").json()
        resp = self.alice_client.patch(f"/api/v1/books/highlights/{created['id']}/", {"color": "ultraviolet"}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_color_filter_on_annotation_center(self):
        self.alice_client.post(self._highlights_url(), {"block_id": self.block_id, "start_offset": 0, "end_offset": 5, "color": "yellow"}, format="json")
        self.alice_client.post(self._highlights_url(), {"block_id": self.block_id, "start_offset": 6, "end_offset": 11, "color": "green"}, format="json")

        all_rows = self.alice_client.get("/api/v1/books/annotations/mine/").json()["results"]
        self.assertEqual(len(all_rows), 2)
        green_only = self.alice_client.get("/api/v1/books/annotations/mine/?color=green").json()["results"]
        self.assertEqual(len(green_only), 1)
        self.assertEqual(green_only[0]["color"], "green")

    def test_has_comment_filter_on_annotation_center(self):
        self.alice_client.post(self._highlights_url(), {"block_id": self.block_id, "start_offset": 0, "end_offset": 5}, format="json")
        self.alice_client.post(self._highlights_url(), {"block_id": self.block_id, "start_offset": 6, "end_offset": 11, "note": "a comment"}, format="json")

        commented = self.alice_client.get("/api/v1/books/annotations/mine/?has_comment=true").json()["results"]
        self.assertEqual(len(commented), 1)
        self.assertEqual(commented[0]["note"], "a comment")
        uncommented = self.alice_client.get("/api/v1/books/annotations/mine/?has_comment=false").json()["results"]
        self.assertEqual(len(uncommented), 1)
        self.assertEqual(uncommented[0]["note"], "")

    def test_annotation_center_is_scoped_to_the_caller_only(self):
        self.alice_client.post(self._highlights_url(), {"block_id": self.block_id, "start_offset": 0, "end_offset": 5}, format="json")
        self.bob_client.post(self._highlights_url(), {"block_id": self.block_id, "start_offset": 6, "end_offset": 11}, format="json")

        alice_rows = self.alice_client.get("/api/v1/books/annotations/mine/").json()["results"]
        self.assertEqual(len(alice_rows), 1)
        bob_rows = self.bob_client.get("/api/v1/books/annotations/mine/").json()["results"]
        self.assertEqual(len(bob_rows), 1)
        self.assertNotEqual(alice_rows[0]["id"], bob_rows[0]["id"])

    def test_resolve_anchor_true_when_unchanged_false_after_block_edited(self):
        highlight = BookHighlight.objects.create(
            book=self.book, chapter=self.chapter, user=self.alice, block_id=self.block_id,
            start_offset=0, end_offset=5, highlighted_text=self.block["text"][:5],
        )
        self.assertTrue(services.resolve_highlight_anchor(highlight)["resolved"])

        # Author edits the chapter — the same block_id now holds different text.
        blocks = self.chapter.content_blocks
        blocks[0]["text"] = "Completely different wording now."
        self.chapter.content_blocks = blocks
        self.chapter.save(update_fields=["encrypted_content"])
        highlight.refresh_from_db()
        self.chapter.refresh_from_db()
        highlight.chapter = self.chapter
        self.assertFalse(services.resolve_highlight_anchor(highlight)["resolved"])

    def test_resolved_field_reflected_on_annotation_center_rows(self):
        created = self.alice_client.post(self._highlights_url(), {
            "block_id": self.block_id, "start_offset": 0, "end_offset": 5,
        }, format="json").json()
        rows = self.alice_client.get("/api/v1/books/annotations/mine/").json()["results"]
        self.assertTrue(rows[0]["resolved"])

        blocks = self.chapter.content_blocks
        blocks[0]["text"] = "Something else entirely, no overlap."
        self.chapter.content_blocks = blocks
        self.chapter.save(update_fields=["encrypted_content"])

        rows = self.alice_client.get("/api/v1/books/annotations/mine/").json()["results"]
        self.assertFalse(rows[0]["resolved"])


class ImagePlacementConversionTests(TestCase):
    """Feature B: image position detection/extraction during manuscript
    conversion, author review/assignment, publish-time validation, and
    offline/package-unrelated access control for the image asset itself."""

    def test_docx_images_interspersed_among_paragraphs_in_correct_order(self):
        from apps.books.conversion import convert_docx

        png1 = make_png_bytes((200, 30, 30))
        png2 = make_png_bytes((30, 200, 30))
        data = make_docx_bytes([
            ("heading", "Chapter One"),
            ("paragraph", "First paragraph of real prose."),
            ("image", png1),
            ("paragraph", "Second paragraph, after the first image."),
            ("paragraph", "Figure caption for the first image."),
            ("image", png2),
            ("paragraph", "Fourth paragraph, after the second image."),
        ])
        result = convert_docx(data)
        self.assertEqual(result.images_extracted, 2)
        blocks = result.chapters[0].blocks
        types = [b["type"] for b in blocks]
        self.assertEqual(types, ["heading", "paragraph", "image", "paragraph", "paragraph", "image", "paragraph"])
        self.assertTrue(all(b.get("_raw_bytes") for b in blocks if b["type"] == "image"))

    def test_docx_with_no_images_produces_no_image_blocks(self):
        from apps.books.conversion import convert_docx

        data = make_docx_bytes([("heading", "Chapter One"), ("paragraph", "Just plain prose, nothing else.")])
        result = convert_docx(data)
        self.assertEqual(result.images_extracted, 0)
        self.assertFalse(any(b["type"] == "image" for b in result.chapters[0].blocks))

    def test_pdf_with_text_and_embedded_image_places_image_after_page_text(self):
        from apps.books.conversion import convert_pdf

        png = make_png_bytes()
        data = make_pdf_with_image_bytes("Real selectable text on this page.", png)
        result = convert_pdf(data)
        self.assertEqual(result.images_extracted, 1)
        blocks = result.chapters[0].blocks
        image_blocks = [b for b in blocks if b["type"] == "image"]
        self.assertEqual(len(image_blocks), 1)
        self.assertEqual(image_blocks[0]["position_confidence"], "page_level")
        self.assertEqual(image_blocks[0]["source_page"], 1)
        # The text block(s) come before the image block in this page's stream.
        image_idx = blocks.index(image_blocks[0])
        self.assertTrue(any(b["type"] == "paragraph" for b in blocks[:image_idx]))

    def test_image_free_pdf_has_no_image_blocks(self):
        from apps.books.conversion import convert_pdf
        import fitz

        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Only text, no pictures on this page at all.")
        data = doc.tobytes()
        doc.close()
        result = convert_pdf(data)
        self.assertEqual(result.images_extracted, 0)
        self.assertFalse(any(b["type"] == "image" for b in result.chapters[0].blocks))

    def test_create_or_get_image_asset_rejects_non_image_bytes(self):
        user = make_user("210")
        book = make_book(user)
        self.assertIsNone(services.create_or_get_image_asset(book, data=b"not a real image at all", content_type="image/png"))

    def test_create_or_get_image_asset_dedupes_identical_bytes_within_a_book(self):
        user = make_user("211")
        book = make_book(user)
        png = make_png_bytes()
        asset1 = services.create_or_get_image_asset(book, data=png, content_type="image/png")
        asset2 = services.create_or_get_image_asset(book, data=png, content_type="image/png")
        self.assertEqual(asset1.id, asset2.id)
        self.assertEqual(BookImageAsset.objects.filter(book=book).count(), 1)

    def test_same_image_at_two_placements_shares_one_asset(self):
        user = make_user("212")
        book = Book.objects.create(owner=user, title="T", author_name="A", status=BookStatus.DRAFT)
        png = make_png_bytes()
        data = make_docx_bytes([
            ("paragraph", "Intro paragraph."),
            ("image", png),
            ("paragraph", "Middle paragraph."),
            ("image", png),  # same bytes, second placement
            ("paragraph", "Closing paragraph."),
        ])
        manuscript = services.create_manuscript(book, uploaded_by=user, filename="b.docx", content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", data=data)
        job = services.process_manuscript(manuscript)
        self.assertEqual(job.status, job.STATUS_SUCCEEDED)
        self.assertEqual(BookImageAsset.objects.filter(book=book).count(), 1)  # deduped
        placements = services.list_image_placements(book)
        self.assertEqual(len(placements), 2)  # but two distinct placement references
        self.assertEqual(placements[0]["asset_id"], placements[1]["asset_id"])
        self.assertEqual(placements[0]["number"], 1)
        self.assertEqual(placements[1]["number"], 2)

    def test_full_pipeline_numbers_placements_and_resolves_missing_image_on_replace(self):
        make_policy()
        author = make_user("213")
        book = Book.objects.create(owner=author, title="T", author_name="A", status=BookStatus.DRAFT)
        png1 = make_png_bytes((200, 30, 30))
        data = make_docx_bytes([
            ("paragraph", "Some words."),
            ("image", png1),
            ("paragraph", "More words."),
        ])
        manuscript = services.create_manuscript(book, uploaded_by=author, filename="b.docx", content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", data=data)
        services.process_manuscript(manuscript)
        book.refresh_from_db()

        placements = services.list_image_placements(book)
        self.assertEqual(len(placements), 1)
        self.assertEqual(placements[0]["extraction_status"], "extracted")

        # Replace with a new image — same placement, new asset.
        png2 = make_png_bytes((10, 10, 250))
        new_asset = services.create_or_get_image_asset(book, data=png2, content_type="image/png")
        result = services.assign_image_to_placement(book, placements[0]["block_id"], new_asset, user=author)
        self.assertEqual(result["asset_id"], str(new_asset.id))

        placements_after = services.list_image_placements(book)
        self.assertEqual(len(placements_after), 1)
        self.assertEqual(placements_after[0]["asset_id"], str(new_asset.id))

    def test_submission_blocked_while_an_image_placement_is_unresolved(self):
        make_policy()
        author = make_user("214")
        book = Book.objects.create(owner=author, title="T", author_name="A", status=BookStatus.DRAFT, rights_declared=True)
        data = make_docx_bytes([("paragraph", "Some text."), ("image", make_png_bytes())])
        manuscript = services.create_manuscript(book, uploaded_by=author, filename="b.docx", content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", data=data)
        services.process_manuscript(manuscript)
        book.refresh_from_db()
        services.acknowledge_policy(user=author, book=book)

        # Simulate the "detected but could not be extracted" case (e.g. a
        # linked-not-embedded image) directly, same technique the
        # resolve-anchor test uses to simulate content drift — mark the
        # placement that was just successfully extracted as missing.
        chapter = book.chapters.first()
        blocks = chapter.content_blocks
        for block in blocks:
            if block.get("type") == "image":
                block["asset_id"] = None
                block["extraction_status"] = "missing"
        chapter.content_blocks = blocks
        chapter.save(update_fields=["encrypted_content"])

        self.assertGreater(len(services.unresolved_image_placements(book)), 0)
        with self.assertRaises(ValidationError):
            services.submit_for_review(book, actor=author)

        # Resolve it, then submission succeeds.
        placements = services.list_image_placements(book)
        asset = services.create_or_get_image_asset(book, data=make_png_bytes(), content_type="image/png")
        services.assign_image_to_placement(book, placements[0]["block_id"], asset, user=author)
        book.refresh_from_db()
        book = services.submit_for_review(book, actor=author)
        self.assertEqual(book.status, BookStatus.PENDING_REVIEW)

    def test_image_placement_endpoints_are_author_only(self):
        author = make_user("215")
        other = make_user("216")
        book = Book.objects.create(owner=author, title="T", author_name="A", status=BookStatus.DRAFT)
        other_client = APIClient()
        other_client.force_authenticate(user=other)
        resp = other_client.get(f"/api/v1/books/mine/{book.id}/image-placements/")
        self.assertEqual(resp.status_code, 403)

    def test_image_download_requires_real_access(self):
        owner = make_user("217")
        reader = make_user("218")
        book = make_book(owner, status=BookStatus.PUBLISHED)
        asset = services.create_or_get_image_asset(book, data=make_png_bytes(), content_type="image/png")

        stranger_client = APIClient()
        unrelated = make_user("219")
        stranger_client.force_authenticate(user=unrelated)
        # A free, published book means user_has_reading_access is True for
        # anyone authenticated — so instead prove the DENY path on a
        # private (draft) book's image.
        private_book = Book.objects.create(owner=owner, title="Private", author_name="A", status=BookStatus.DRAFT)
        private_asset = services.create_or_get_image_asset(private_book, data=make_png_bytes(), content_type="image/png")
        denied = stranger_client.get(f"/api/v1/books/images/{private_asset.id}/")
        self.assertEqual(denied.status_code, 403)

        owner_client = APIClient()
        owner_client.force_authenticate(user=owner)
        allowed = owner_client.get(f"/api/v1/books/images/{asset.id}/")
        # No presigned-URL backend configured in the test environment —
        # the honest degrade is 501, never a silent 200 with a bad URL,
        # and never conflated with a 403 (this IS authorized).
        self.assertIn(allowed.status_code, (200, 501))


class AnnotationAndImageSecurityBoundaryTests(TestCase):
    """Section 5 of the acceptance pass: explicit, independent coverage
    of every authorization boundary the annotation and image-placement
    features introduce — never trust a client-supplied id/MIME/filename,
    never let ownership be satisfied by anything other than a real
    server-side relationship."""

    def setUp(self):
        self.author = make_user("500")
        self.other_author = make_user("501")
        self.reviewer = make_user("502", is_superuser=True, is_staff=True, email="reviewer502@kis.test")
        self.reader = make_user("503")
        self.stranger = make_user("504")

    def test_unauthenticated_request_cannot_read_image_download_endpoint(self):
        book = make_book(self.author, status=BookStatus.PUBLISHED)
        asset = services.create_or_get_image_asset(book, data=make_png_bytes(), content_type="image/png")
        anon_client = APIClient()
        resp = anon_client.get(f"/api/v1/books/images/{asset.id}/")
        self.assertEqual(resp.status_code, 401)

    def test_paid_unpublished_book_image_is_denied_to_a_reader_with_no_entitlement(self):
        shop = make_verified_shop(self.author)
        book = Book.objects.create(
            owner=self.author, title="Paid", author_name="A", status=BookStatus.PUBLISHED,
            is_free=False, price_cents=999, publisher_shop=shop,
        )
        asset = services.create_or_get_image_asset(book, data=make_png_bytes(), content_type="image/png")
        reader_client = APIClient()
        reader_client.force_authenticate(user=self.reader)
        resp = reader_client.get(f"/api/v1/books/images/{asset.id}/")
        self.assertEqual(resp.status_code, 403)

    def test_cannot_assign_an_image_to_another_authors_manuscript_by_guessing_block_id(self):
        victim_book = Book.objects.create(owner=self.other_author, title="Victim", author_name="A", status=BookStatus.DRAFT)
        data = make_docx_bytes([("paragraph", "Intro."), ("image", make_png_bytes())])
        manuscript = services.create_manuscript(
            victim_book, uploaded_by=self.other_author, filename="b.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", data=data,
        )
        services.process_manuscript(manuscript)
        victim_book.refresh_from_db()
        original_placement = services.list_image_placements(victim_book)[0]
        real_block_id = original_placement["block_id"]
        original_asset_id = original_placement["asset_id"]  # conversion already auto-extracted this one

        attacker_asset = services.create_or_get_image_asset(
            Book.objects.create(owner=self.author, title="Attacker's book", author_name="A", status=BookStatus.DRAFT),
            data=make_png_bytes((9, 9, 9)), content_type="image/png",
        )
        with self.assertRaises(PermissionDenied):
            services.assign_image_to_placement(victim_book, real_block_id, attacker_asset, user=self.author)

        attacker_client = APIClient()
        attacker_client.force_authenticate(user=self.author)
        resp = attacker_client.post(
            f"/api/v1/books/mine/{victim_book.id}/image-placements/{real_block_id}/image/",
            {"file": _as_upload_file(make_png_bytes((9, 9, 9)))}, format="multipart",
        )
        self.assertEqual(resp.status_code, 404)  # scoped by owner=request.user — the book doesn't even resolve

        # The victim's placement is provably untouched — still the
        # original auto-extracted asset, never the attacker's.
        victim_book.refresh_from_db()
        self.assertEqual(services.list_image_placements(victim_book)[0]["asset_id"], original_asset_id)
        self.assertNotEqual(original_asset_id, str(attacker_asset.id))

    def test_reviewer_image_access_is_logged_like_the_existing_manuscript_review_workflow(self):
        from apps.books.models import BookModerationAccess

        book = Book.objects.create(owner=self.author, title="Under review", author_name="A", status=BookStatus.PENDING_REVIEW)
        asset = services.create_or_get_image_asset(book, data=make_png_bytes(), content_type="image/png")
        reviewer_client = APIClient()
        reviewer_client.force_authenticate(user=self.reviewer)

        self.assertEqual(BookModerationAccess.objects.filter(book=book).count(), 0)
        resp = reviewer_client.get(f"/api/v1/books/images/{asset.id}/")
        self.assertIn(resp.status_code, (200, 501))  # authorized either way
        self.assertEqual(BookModerationAccess.objects.filter(book=book, accessed_by=self.reviewer).count(), 1)

    def test_owner_viewing_their_own_image_does_not_create_a_spurious_reviewer_access_log(self):
        from apps.books.models import BookModerationAccess

        book = make_book(self.author, status=BookStatus.PUBLISHED)
        asset = services.create_or_get_image_asset(book, data=make_png_bytes(), content_type="image/png")
        owner_client = APIClient()
        owner_client.force_authenticate(user=self.author)
        owner_client.get(f"/api/v1/books/images/{asset.id}/")
        self.assertEqual(BookModerationAccess.objects.filter(book=book).count(), 0)

    def test_annotation_list_endpoints_do_not_leak_unauthorized_book_text(self):
        from apps.books.models import BookChapter

        private_book = Book.objects.create(owner=self.author, title="Private", author_name="A", status=BookStatus.DRAFT)
        blocks = services.assign_block_ids([{"type": "paragraph", "text": "Secret draft text."}])
        chapter = BookChapter.objects.create(book=private_book, order=1, title="One", content_blocks=blocks, word_count=3)
        stranger_client = APIClient()
        stranger_client.force_authenticate(user=self.stranger)
        resp = stranger_client.get(f"/api/v1/books/{private_book.id}/chapters/{chapter.stable_id}/highlights/")
        self.assertEqual(resp.status_code, 403)

    def test_my_annotations_feed_never_returns_another_users_rows(self):
        book = make_book(self.author, status=BookStatus.PUBLISHED)
        chapter = book.chapters.first()
        block_id = chapter.content_blocks[0]["id"]
        reader_client = APIClient()
        reader_client.force_authenticate(user=self.reader)
        stranger_client = APIClient()
        stranger_client.force_authenticate(user=self.stranger)

        reader_client.post(
            f"/api/v1/books/{book.id}/chapters/{chapter.stable_id}/highlights/",
            {"block_id": block_id, "start_offset": 0, "end_offset": 5}, format="json",
        )
        stranger_rows = stranger_client.get("/api/v1/books/annotations/mine/").json()["results"]
        self.assertEqual(stranger_rows, [])

    def test_upload_rejects_a_renamed_non_image_file_disguised_as_png(self):
        author = make_user("505")
        book = Book.objects.create(owner=author, title="T", author_name="A", status=BookStatus.DRAFT)
        data = make_docx_bytes([("paragraph", "Intro."), ("image", make_png_bytes())])
        manuscript = services.create_manuscript(
            book, uploaded_by=author, filename="b.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", data=data,
        )
        services.process_manuscript(manuscript)
        book.refresh_from_db()
        block_id = services.list_image_placements(book)[0]["block_id"]

        author_client = APIClient()
        author_client.force_authenticate(user=author)
        fake_image = _as_upload_file(b"This is plain text, not image bytes at all.", name="totally.png", content_type="image/png")
        resp = author_client.post(
            f"/api/v1/books/mine/{book.id}/image-placements/{block_id}/image/", {"file": fake_image}, format="multipart",
        )
        self.assertEqual(resp.status_code, 400)  # declared MIME/extension both lie — rejected on real content
