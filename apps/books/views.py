from __future__ import annotations

from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .models import (
    Book,
    BookChapter,
    BookChapterBookmark,
    BookEntitlement,
    BookHighlight,
    BookImageAsset,
    BookNote,
    BookPublishingRestriction,
    BookOfflineLicense,
    BookPurchase,
    BookReadingProgress,
    BookReview,
    BookRoyaltyRecord,
    BookStatus,
)
from .permissions import CanRefundBookPurchases, IsBookOwner, can_review_books
from .serializers import (
    AuthorBookSerializer,
    BookChapterBookmarkSerializer,
    BookConversionJobSerializer,
    BookDetailSerializer,
    BookEntitlementSerializer,
    BookHighlightSerializer,
    BookImageAssetSerializer,
    BookLibraryItemSerializer,
    BookListSerializer,
    BookManuscriptSerializer,
    BookNoteSerializer,
    BookOfflineLicenseSerializer,
    BookPurchaseSerializer,
    BookReadingProgressSerializer,
    BookReviewSerializer,
    BookRoyaltyRecordSerializer,
    ChristianPublishingPolicySerializer,
    MyBookAnnotationSerializer,
)


# ─────────────────────────────── Public marketplace ─────────────────────────

class BookListView(generics.ListAPIView):
    serializer_class = BookListSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        services.publish_scheduled_books()
        qs = Book.objects.filter(status=BookStatus.PUBLISHED)
        params = self.request.query_params
        category = params.get("category")
        if category:
            qs = qs.filter(christian_category=category)
        language = params.get("language")
        if language:
            qs = qs.filter(language=language)
        is_free = params.get("is_free")
        if is_free is not None:
            qs = qs.filter(is_free=is_free.lower() in {"1", "true", "yes"})
        search = params.get("search")
        if search:
            qs = qs.filter(Q(title__icontains=search) | Q(author_name__icontains=search) | Q(description__icontains=search))
        sort = params.get("sort", "newest")
        if sort == "price_low":
            qs = qs.order_by("price_cents", "-created_at")
        elif sort == "price_high":
            qs = qs.order_by("-price_cents", "-created_at")
        elif sort == "rating":
            qs = qs.order_by("-average_rating", "-review_count")
        elif sort == "popular":
            qs = qs.order_by("-view_count", "-published_at")
        else:
            qs = qs.order_by("-published_at")
        return qs


class DiscoveryView(APIView):
    """GET /books/discover/ — curated homepage sections for the
    marketplace, each capped to a small count (this is a discovery
    surface, not another paginated list). Every section reuses the exact
    same PUBLISHED-only, scheduled-publish-aware queryset BookListView
    uses, so nothing can show up here before it's genuinely live."""
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        services.publish_scheduled_books()
        published = Book.objects.filter(status=BookStatus.PUBLISHED)
        new_releases = published.order_by("-published_at")[:10]
        top_rated = published.filter(review_count__gte=1).order_by("-average_rating", "-review_count")[:10]
        trending = published.order_by("-view_count", "-published_at")[:10]
        free_books = published.filter(is_free=True).order_by("-published_at")[:10]
        return Response({
            "new_releases": BookListSerializer(new_releases, many=True).data,
            "top_rated": BookListSerializer(top_rated, many=True).data,
            "trending": BookListSerializer(trending, many=True).data,
            "free_books": BookListSerializer(free_books, many=True).data,
        })


class BookDetailView(generics.RetrieveAPIView):
    serializer_class = BookDetailSerializer
    permission_classes = [permissions.AllowAny]
    lookup_field = "id"

    def get_queryset(self):
        return Book.objects.all()

    def get_object(self):
        services.publish_scheduled_books()
        book = get_object_or_404(Book, id=self.kwargs["id"])
        user = self.request.user
        is_owner_or_staff = bool(
            getattr(user, "is_authenticated", False)
            and (book.owner_id == user.id or can_review_books(user))
        )
        if not book.is_publicly_visible() and not is_owner_or_staff:
            from django.http import Http404

            raise Http404
        Book.objects.filter(id=book.id).update(view_count=book.view_count + 1)
        return book


class ChristianPolicyCurrentView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        policy = services.current_policy_version()
        if not policy:
            return Response({"detail": "No policy configured."}, status=status.HTTP_404_NOT_FOUND)
        return Response(ChristianPublishingPolicySerializer(policy).data)


class BookChapterReadView(APIView):
    permission_classes = [permissions.AllowAny]
    # Content-protection: full chapter text is the one thing this API
    # actually needs to guard against bulk scraping (everything else is
    # metadata or already gated by payment/entitlement). See
    # THROTTLE_BOOK_CONTENT_READ in settings for why this scope is so much
    # tighter than the generic anon/user rate.
    throttle_scope = "book_content_read"

    def get(self, request, id, stable_id):
        book = get_object_or_404(Book, id=id)
        if not services.user_has_reading_access(book, request.user if request.user.is_authenticated else None):
            return Response({"detail": "You do not have access to this book."}, status=status.HTTP_403_FORBIDDEN)
        chapter = get_object_or_404(BookChapter, book=book, stable_id=stable_id)
        services.record_chapter_read_and_detect_scraping(request.user, request)
        return Response({
            "stable_id": str(chapter.stable_id),
            "order": chapter.order,
            "title": chapter.title,
            "content_blocks": chapter.content_blocks,
            "word_count": chapter.word_count,
        })


class FreeAccessView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, id):
        book = get_object_or_404(Book, id=id)
        if not book.is_free or not book.is_publicly_visible():
            return Response({"detail": "This book is not available as a free title."}, status=status.HTTP_400_BAD_REQUEST)
        services.grant_entitlement(book, request.user, source=BookEntitlement.SOURCE_FREE)
        return Response({"detail": "Added to your library."})


# ─────────────────────────────── Author studio ──────────────────────────────

class AuthorBookListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = Book.objects.filter(owner=request.user).order_by("-created_at")
        return Response(AuthorBookSerializer(qs, many=True).data)

    def post(self, request):
        services.assert_can_publish(request.user)
        serializer = AuthorBookSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        book = Book.objects.create(owner=request.user, **serializer.validated_data)
        return Response(AuthorBookSerializer(book).data, status=status.HTTP_201_CREATED)


class AuthorBookDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsBookOwner]

    def get_object(self, request, id):
        book = get_object_or_404(Book, id=id)
        self.check_object_permissions(request, book)
        return book

    def get(self, request, id):
        return Response(AuthorBookSerializer(self.get_object(request, id)).data)

    def patch(self, request, id):
        book = self.get_object(request, id)
        if book.status in {BookStatus.PENDING_REVIEW, BookStatus.UNDER_REVIEW}:
            raise ValidationError("This book is currently under review and cannot be edited. Wait for a decision.")
        wants_paid = request.data.get("is_free") is False or (
            "price_cents" in request.data and int(request.data.get("price_cents") or 0) > 0 and request.data.get("is_free") is not True
        )
        if wants_paid:
            shop = services.assert_eligible_for_price(user=request.user)
            book.publisher_shop = shop
        serializer = AuthorBookSerializer(book, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        was_published = book.status == BookStatus.PUBLISHED
        book = serializer.save()
        if was_published:
            # Editing a live book requires re-review before the edit goes
            # out — never silently mutate published content.
            book.content_version += 1
            book.status = BookStatus.PENDING_REVIEW
            book.submitted_at = timezone.now()
            book.save(update_fields=["content_version", "status", "submitted_at", "updated_at"])
        return Response(AuthorBookSerializer(book).data)

    def delete(self, request, id):
        book = self.get_object(request, id)
        if book.status != BookStatus.DRAFT:
            raise ValidationError("Only draft books can be deleted. Archive a published book instead.")
        book.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AuthorChapterUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, id, stable_id):
        book = get_object_or_404(Book, id=id, owner=request.user)
        if book.status in {BookStatus.PENDING_REVIEW, BookStatus.UNDER_REVIEW}:
            raise ValidationError("This book is currently under review and cannot be edited.")
        chapter = get_object_or_404(BookChapter, book=book, stable_id=stable_id)
        chapter = services.apply_chapter_edit(
            book, chapter, title=request.data.get("title"), content_blocks=request.data.get("content_blocks"),
        )
        book.refresh_from_db()
        return Response({
            "stable_id": str(chapter.stable_id), "title": chapter.title, "word_count": chapter.word_count,
            "book_status": book.status,
        })


class AcknowledgePolicyView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, id):
        book = get_object_or_404(Book, id=id, owner=request.user)
        ack = services.acknowledge_policy(
            user=request.user, book=book,
            ip_address=request.META.get("REMOTE_ADDR", ""),
        )
        return Response({"acknowledged_at": ack.acknowledged_at, "policy_version": ack.policy_version.version})


class PaidEligibilityView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        shop = services.eligible_shop_for_paid_publishing(request.user)
        return Response({
            "eligible": shop is not None,
            "shop_id": str(shop.id) if shop else None,
            "reason": None if shop else "No verified business with a working payout account is linked to your account.",
        })


class ManuscriptUploadView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "upload"  # same platform-wide upload-abuse ceiling every other upload endpoint uses

    def post(self, request, id):
        book = get_object_or_404(Book, id=id, owner=request.user)
        upload = request.FILES.get("file")
        if not upload:
            raise ValidationError("No file provided (expected multipart field 'file').")
        data = upload.read()
        manuscript = services.create_manuscript(
            book, uploaded_by=request.user, filename=upload.name,
            content_type=upload.content_type or "", data=data,
        )
        from .tasks import run_book_conversion

        run_book_conversion.delay(str(manuscript.id))
        manuscript.refresh_from_db()
        return Response(BookManuscriptSerializer(manuscript).data, status=status.HTTP_201_CREATED)


class ConversionStatusView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, id):
        book = get_object_or_404(Book, id=id, owner=request.user)
        manuscript = book.manuscripts.order_by("-created_at").first()
        if not manuscript:
            return Response({"detail": "No manuscript uploaded yet."}, status=status.HTTP_404_NOT_FOUND)
        job = manuscript.conversion_jobs.order_by("-created_at").first()
        return Response({
            "manuscript": BookManuscriptSerializer(manuscript).data,
            "job": BookConversionJobSerializer(job).data if job else None,
        })

    def post(self, request, id):
        """Retry conversion. Body: {"force": bool}."""
        book = get_object_or_404(Book, id=id, owner=request.user)
        manuscript = book.manuscripts.order_by("-created_at").first()
        if not manuscript:
            raise ValidationError("No manuscript uploaded yet.")
        from .tasks import run_book_conversion

        run_book_conversion.delay(str(manuscript.id), bool(request.data.get("force", False)))
        return Response({"detail": "Conversion retry queued."})


class ImagePlacementsListView(APIView):
    """GET /books/mine/<id>/image-placements/ — the author's (or an
    authorized GO reviewer's) numbered image-review list: see
    services.list_image_placements for the real reading-order numbering."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, id):
        book = get_object_or_404(Book, id=id)
        if book.owner_id != request.user.id and not can_review_books(request.user):
            raise PermissionDenied("You do not have access to this book's manuscript.")
        return Response(services.list_image_placements(book))


class ImagePlacementImageView(APIView):
    """POST /books/mine/<id>/image-placements/<block_id>/image/ — upload
    or replace the image for one specific placement. Multipart field
    'file'. Attaches to the placement's own stable block id — never
    appended to a gallery or the end of the chapter (see
    services.assign_image_to_placement)."""
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "upload"

    def post(self, request, id, block_id):
        book = get_object_or_404(Book, id=id, owner=request.user)
        upload = request.FILES.get("file")
        if not upload:
            raise ValidationError("No file provided (expected multipart field 'file').")
        data = upload.read()
        asset = services.create_or_get_image_asset(
            book, data=data, content_type=upload.content_type or "",
            uploaded_by=request.user, source=BookImageAsset.SOURCE_AUTHOR_UPLOADED,
        )
        if asset is None:
            raise ValidationError("That file could not be read as a valid JPEG, PNG, or WEBP image.")
        result = services.assign_image_to_placement(book, block_id, asset, user=request.user)
        return Response(result, status=status.HTTP_200_OK)


class BookImageAssetDownloadView(APIView):
    """GET /books/images/<asset_id>/ — a short-lived signed URL for one
    chapter image, issued only after the same access check every other
    protected read path uses (owner, authorized GO reviewer, or a reader
    with real entitlement to the book this asset belongs to). Mirrors
    apps.books.storage.sign_manuscript_for_download's honest-degrade
    behavior: on a backend with no presigned-URL support (local/dev disk
    storage) this reports that plainly rather than leaking a raw path."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, asset_id):
        asset = get_object_or_404(BookImageAsset, id=asset_id)
        book = asset.book
        is_owner = book.owner_id == request.user.id
        is_reviewer = can_review_books(request.user)
        if not (is_owner or is_reviewer or services.user_has_reading_access(book, request.user)):
            raise PermissionDenied("You do not have access to this image.")
        if is_reviewer and not is_owner:
            # Same audit trail AdminBookDetailView.get already leaves for
            # a reviewer opening a book's full manuscript text — a
            # reviewer viewing a manuscript's IMAGES is the same kind of
            # sensitive access and gets the same log.
            from .models import BookModerationAccess

            BookModerationAccess.objects.create(
                book=book, accessed_by=request.user, ip_address=request.META.get("REMOTE_ADDR", ""),
            )
        from .storage import sign_image_for_download

        signed = sign_image_for_download(asset.file.name)
        if signed is None:
            return Response(
                {"detail": "Signed image delivery is not configured in this environment."},
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )
        return Response({**signed, **BookImageAssetSerializer(asset).data})


class SubmitForReviewView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, id):
        book = get_object_or_404(Book, id=id, owner=request.user)
        book = services.submit_for_review(book, actor=request.user)
        return Response(AuthorBookSerializer(book).data)


# ─────────────────────────────── Library / reading progress ────────────────

class MyLibraryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = BookEntitlement.objects.filter(user=request.user, revoked_at__isnull=True).select_related("book").order_by("-granted_at")
        return Response(BookEntitlementSerializer(qs, many=True).data)


class ReadingProgressView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, id):
        book = get_object_or_404(Book, id=id)
        progress = BookReadingProgress.objects.filter(book=book, user=request.user).first()
        if not progress:
            return Response({"detail": "No progress recorded."}, status=status.HTTP_404_NOT_FOUND)
        return Response(BookReadingProgressSerializer(progress).data)

    def put(self, request, id):
        book = get_object_or_404(Book, id=id)
        if not services.user_has_reading_access(book, request.user):
            raise PermissionDenied("You do not have access to this book.")
        progress, _ = BookReadingProgress.objects.update_or_create(
            book=book, user=request.user,
            defaults={
                "chapter_stable_id": request.data.get("chapter_stable_id"),
                "block_index": int(request.data.get("block_index", 0) or 0),
                "percent_complete": float(request.data.get("percent_complete", 0) or 0),
                "is_completed": bool(request.data.get("is_completed", False)),
                "last_read_at": timezone.now(),
                "device_info": str(request.data.get("device_info", ""))[:255],
            },
        )
        return Response(BookReadingProgressSerializer(progress).data)


class PersonalLibraryOverviewView(APIView):
    """GET /books/library/overview/ — the four personal-library buckets
    (saved, currently reading, completed, bookmarked) in one call."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        buckets = services.get_personal_library(request.user)
        return Response({key: BookListSerializer(books, many=True).data for key, books in buckets.items()})


class SaveBookView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, id):
        book = get_object_or_404(Book, id=id)
        services.save_book(book, request.user)
        return Response({"saved": True}, status=status.HTTP_201_CREATED)

    def delete(self, request, id):
        book = get_object_or_404(Book, id=id)
        services.unsave_book(book, request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ChapterBookmarkToggleView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, id, stable_id):
        book = get_object_or_404(Book, id=id)
        if not services.user_has_reading_access(book, request.user):
            raise PermissionDenied("You do not have access to this book.")
        chapter = get_object_or_404(BookChapter, book=book, stable_id=stable_id)
        bookmark, created = services.toggle_chapter_bookmark(chapter, request.user, label=str(request.data.get("label", "")))
        if not created:
            return Response({"bookmarked": False})
        return Response({"bookmarked": True, "bookmark": BookChapterBookmarkSerializer(bookmark).data}, status=status.HTTP_201_CREATED)


class BookChapterBookmarksListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, id):
        book = get_object_or_404(Book, id=id)
        qs = BookChapterBookmark.objects.filter(book=book, user=request.user).select_related("chapter").order_by("chapter__order")
        return Response(BookChapterBookmarkSerializer(qs, many=True).data)


class ChapterHighlightListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, id, stable_id):
        book = get_object_or_404(Book, id=id)
        if not services.user_has_reading_access(book, request.user):
            raise PermissionDenied("You do not have access to this book.")
        chapter = get_object_or_404(BookChapter, book=book, stable_id=stable_id)
        qs = BookHighlight.objects.filter(chapter=chapter, user=request.user)
        return Response(BookHighlightSerializer(qs, many=True).data)

    def post(self, request, id, stable_id):
        book = get_object_or_404(Book, id=id)
        chapter = get_object_or_404(BookChapter, book=book, stable_id=stable_id)
        block_id = str(request.data.get("block_id", ""))
        if not block_id:
            raise ValidationError("block_id is required.")
        highlight = services.create_highlight(
            chapter, request.user, block_id=block_id,
            start_offset=int(request.data.get("start_offset", 0) or 0),
            end_offset=int(request.data.get("end_offset", 0) or 0),
            color=str(request.data.get("color", "yellow")),
            note=str(request.data.get("note", "")),
        )
        return Response(BookHighlightSerializer(highlight).data, status=status.HTTP_201_CREATED)


class BookHighlightsListView(APIView):
    """GET /books/<id>/highlights/ — all of the caller's own highlights
    across the whole book (for a consolidated "my highlights" view),
    never another user's."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, id):
        book = get_object_or_404(Book, id=id)
        qs = BookHighlight.objects.filter(book=book, user=request.user).select_related("chapter")
        return Response(BookHighlightSerializer(qs, many=True).data)


class HighlightDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, highlight_id):
        highlight = get_object_or_404(BookHighlight, id=highlight_id, user=request.user)
        highlight = services.update_highlight(
            highlight, request.user,
            color=request.data.get("color"),
            note=request.data.get("note") if "note" in request.data else None,
        )
        return Response(BookHighlightSerializer(highlight).data)

    def delete(self, request, highlight_id):
        services.delete_highlight(highlight_id, request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MyAnnotationsView(generics.ListAPIView):
    """GET /books/annotations/mine/ — the personalized Highlights &
    Comments Center's single feed: every highlight/comment the caller has
    ever saved, across every book, newest first. ?color=<color> filters
    to one highlight color; ?has_comment=true restricts to annotations
    that also carry a comment (has_comment=false finds highlight-only
    rows, the "comments without highlights" case the spec calls out is
    actually the inverse — a note with no distinct color choice still
    always has a color field, so there is no "highlightless comment" row
    to lose track of here; see MyBookAnnotationSerializer)."""
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = MyBookAnnotationSerializer

    def get_queryset(self):
        qs = BookHighlight.objects.filter(user=self.request.user).select_related("book", "chapter")
        color = self.request.query_params.get("color")
        if color:
            qs = qs.filter(color=color)
        has_comment = self.request.query_params.get("has_comment")
        if has_comment is not None:
            if has_comment.lower() in ("true", "1"):
                qs = qs.exclude(note="")
            elif has_comment.lower() in ("false", "0"):
                qs = qs.filter(note="")
        return qs


# ───────────────────────────────── Reviews ──────────────────────────────────

class BookReviewListCreateView(APIView):
    """GET is public (discovery/detail pages show reviews to anyone).
    POST creates or updates the caller's OWN review — a second POST with a
    different rating is a real edit, same idempotent-upsert shape
    highlights use, not a duplicate row (BookReview.unique_together)."""

    def get_permissions(self):
        if self.request.method == "POST":
            return [permissions.IsAuthenticated()]
        return [permissions.AllowAny()]

    def get(self, request, id):
        book = get_object_or_404(Book, id=id)
        qs = BookReview.objects.filter(book=book).select_related("user")
        return Response(BookReviewSerializer(qs, many=True).data)

    def post(self, request, id):
        book = get_object_or_404(Book, id=id)
        review = services.create_or_update_review(
            book, request.user,
            rating=int(request.data.get("rating", 0) or 0),
            review_text=str(request.data.get("review_text", "")),
        )
        return Response(BookReviewSerializer(review).data, status=status.HTTP_201_CREATED)


class ReviewDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, review_id):
        services.delete_review(review_id, request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ReportReviewView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, review_id):
        review = get_object_or_404(BookReview, id=review_id)
        services.report_review(review, request.user, reason=str(request.data.get("reason", "")))
        return Response({"detail": "Reported for review."}, status=status.HTTP_201_CREATED)


class ChapterNoteListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, id, stable_id):
        book = get_object_or_404(Book, id=id)
        if not services.user_has_reading_access(book, request.user):
            raise PermissionDenied("You do not have access to this book.")
        chapter = get_object_or_404(BookChapter, book=book, stable_id=stable_id)
        qs = BookNote.objects.filter(chapter=chapter, user=request.user)
        return Response(BookNoteSerializer(qs, many=True).data)

    def post(self, request, id, stable_id):
        book = get_object_or_404(Book, id=id)
        chapter = get_object_or_404(BookChapter, book=book, stable_id=stable_id)
        note = services.create_note(
            chapter, book, request.user, text=str(request.data.get("text", "")),
            block_id=str(request.data.get("block_id", "")),
            client_request_id=str(request.data.get("client_request_id", "")),
        )
        return Response(BookNoteSerializer(note).data, status=status.HTTP_201_CREATED)


class BookNotesListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, id):
        book = get_object_or_404(Book, id=id)
        qs = BookNote.objects.filter(book=book, user=request.user).select_related("chapter")
        return Response(BookNoteSerializer(qs, many=True).data)


class NoteDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, note_id):
        note = get_object_or_404(BookNote, id=note_id, user=request.user)
        note = services.update_note(note, request.user, text=str(request.data.get("text", "")))
        return Response(BookNoteSerializer(note).data)

    def delete(self, request, note_id):
        services.delete_note(note_id, request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─────────────────────────────── Purchases ──────────────────────────────────

class CheckoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, id):
        book = get_object_or_404(Book, id=id)
        if book.is_free or not book.is_publicly_visible():
            raise ValidationError("This book is not available for purchase.")
        if BookEntitlement.objects.filter(book=book, user=request.user, revoked_at__isnull=True).exists():
            raise ValidationError("You already own this book.")

        from apps.billing.direct_payments import create_direct_payment_intent
        from apps.billing.models import DirectPaymentIntent

        amount_cents = book.current_price_cents  # server-authoritative, never client-supplied
        purchase = BookPurchase.objects.create(
            book=book, buyer=request.user, amount_cents=amount_cents, currency=book.currency,
        )
        provider = str(request.data.get("provider", "flutterwave")).strip().lower()
        try:
            intent = create_direct_payment_intent(
                user=request.user,
                target_type=DirectPaymentIntent.TARGET_BOOK_PURCHASE,
                target_id=purchase.id,
                provider=provider,
                metadata={"book_id": str(book.id), "book_title": book.title},
            )
        except PermissionError as exc:
            raise PermissionDenied(str(exc))
        return Response({
            "purchase": BookPurchaseSerializer(purchase).data,
            "payment_url": intent.payment_url,
            "provider": intent.provider,
            "tx_ref": intent.tx_ref,
        }, status=status.HTTP_201_CREATED)


class MyPurchasesView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = BookPurchase.objects.filter(buyer=request.user).select_related("book").order_by("-created_at")
        return Response(BookPurchaseSerializer(qs, many=True).data)


# ─────────────────────────────── Royalties & refunds ─────────────────────────

class MyRoyaltiesView(APIView):
    """GET /books/royalties/mine/ — an author's own earnings ledger (every
    row is server-computed at sale time and reversed on refund, never
    edited after the fact) plus a lifetime summary, so AuthorStudioScreen
    can show real numbers instead of re-deriving them client-side from
    raw purchases."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from django.db.models import Sum

        qs = BookRoyaltyRecord.objects.filter(author=request.user).select_related("book")
        earned = qs.filter(status=BookRoyaltyRecord.STATUS_EARNED).aggregate(total=Sum("net_royalty_cents"))["total"] or 0
        reversed_total = qs.filter(status=BookRoyaltyRecord.STATUS_REVERSED).aggregate(
            total=Sum("net_royalty_cents")
        )["total"] or 0
        return Response({
            "records": BookRoyaltyRecordSerializer(qs.order_by("-created_at"), many=True).data,
            "lifetime_net_earned_cents": earned,
            "lifetime_reversed_cents": reversed_total,
        })


class RefundPurchaseView(APIView):
    """POST /books/purchases/<id>/refund/ — GO/staff only. Actually moves
    money back via the payment provider (apps.books.services.
    refund_purchase -> _execute_book_refund), then revokes the buyer's
    entitlement and reverses the author's royalty record. A provider this
    can't refund through yet (Stripe) fails loudly with a clear reason —
    see _execute_book_refund's docstring — never a silent fake success."""
    permission_classes = [permissions.IsAuthenticated, CanRefundBookPurchases]

    def post(self, request, id):
        purchase = get_object_or_404(BookPurchase, id=id)
        purchase = services.refund_purchase(purchase, actor=request.user, reason=str(request.data.get("reason", "")))
        return Response(BookPurchaseSerializer(purchase).data)


# ─────────────────────────────── Offline downloads ───────────────────────────

class RequestOfflineDownloadView(APIView):
    """POST /books/<id>/offline/request/ — issues (or renews, if one
    already exists for this exact device) an encrypted offline package.
    Re-checks real entitlement server-side on every call — never trusts
    a client-supplied is_paid/is_purchased/is_authorized flag; the only
    thing that grants a package is services.user_has_reading_access
    passing for request.user, same gate every other read path uses."""
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "book_offline_download"

    def post(self, request, id):
        book = get_object_or_404(Book, id=id)
        device_id = str(request.data.get("device_id", "")).strip()
        license_row, package = services.issue_or_renew_offline_license(book, request.user, device_id=device_id)
        return Response(package, status=status.HTTP_201_CREATED)


class VerifyOfflineLicenseView(APIView):
    """GET /books/<id>/offline/verify/?device_id=... — the periodic
    online check-in a device with connectivity makes before its locally-
    cached license expiry; never re-exposes the package DEK (the device
    already has it in secure storage)."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, id):
        book = get_object_or_404(Book, id=id)
        device_id = str(request.query_params.get("device_id", "")).strip()
        if not device_id:
            raise ValidationError("device_id is required.")
        result = services.verify_offline_license(book, request.user, device_id=device_id)
        return Response(result)


class RevokeOfflineLicenseView(APIView):
    """DELETE /books/<id>/offline/<device_id>/ — self-service "remove
    download" cleanup of server-side license state (the device itself
    deletes its local package/key independently; this just stops future
    renewal, matching the same honest offline-revocation limits
    documented on BookOfflineLicense)."""
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, id, device_id):
        book = get_object_or_404(Book, id=id)
        services.revoke_offline_license(book, request.user, device_id=device_id, reason="Removed by user.")
        return Response(status=status.HTTP_204_NO_CONTENT)


class MyOfflineDownloadsView(APIView):
    """GET /books/offline/mine/ — every active offline license across
    all of the caller's devices, for a "my downloads" list / storage-
    usage view. Never includes wrapped_package_dek (see
    BookOfflineLicenseSerializer)."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = BookOfflineLicense.objects.filter(
            user=request.user, status=BookOfflineLicense.STATUS_ACTIVE,
        ).select_related("book")
        return Response(BookOfflineLicenseSerializer(qs, many=True).data)
