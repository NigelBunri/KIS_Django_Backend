"""
GO/staff-facing Books moderation — the Christian-publishing-policy review
queue, full-manuscript reader, moderation decisions, and author
publishing/account restrictions. Extends the existing admin_control app
(same RBAC, same audit log) rather than a second admin surface; the
website's ~/dev/website/app/control/admin/moderation page already shows
"book" flags for free via the platform-wide AdminContentQueueView — these
endpoints are the book-specific depth that generic queue can't provide
(full manuscript text, approve/reject/corrections, author restrictions).
"""
from __future__ import annotations

from django.core.paginator import Paginator
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from admin_control.audit.logging import AuditLogger

from apps.books.models import Book, BookModerationAccess, BookPublishingRestriction, BookStatus
from apps.books.permissions import CanBanBookAuthors, CanRestrictBooks, CanReviewBooks
from apps.books.serializers import BookChapterFullSerializer
from apps.books.services import apply_moderation_decision, apply_restriction, lift_restriction

REVIEWABLE_STATUSES = {
    BookStatus.PENDING_REVIEW, BookStatus.UNDER_REVIEW, BookStatus.CORRECTIONS_REQUESTED,
}

ACCOUNT_LEVEL_RESTRICTIONS = {
    BookPublishingRestriction.TYPE_ACCOUNT_SUSPENDED,
    BookPublishingRestriction.TYPE_ACCOUNT_BANNED,
}


def _safe_int(val, default, lo=1, hi=250):
    try:
        return max(lo, min(int(val), hi))
    except (TypeError, ValueError):
        return default


def _serialize_book_row(book: Book) -> dict:
    latest_manuscript = book.manuscripts.order_by("-created_at").first()
    latest_job = latest_manuscript.conversion_jobs.order_by("-created_at").first() if latest_manuscript else None
    open_restrictions = BookPublishingRestriction.objects.filter(
        user=book.owner, status=BookPublishingRestriction.STATUS_ACTIVE
    ).count()
    return {
        "id": str(book.id),
        "title": book.title,
        "author_name": book.author_name,
        "owner": {"id": str(book.owner_id), "email": getattr(book.owner, "email", None)},
        "publisher_shop_id": str(book.publisher_shop_id) if book.publisher_shop_id else None,
        "status": book.status,
        "christian_category": book.christian_category,
        "language": book.language,
        "is_free": book.is_free,
        "price_cents": book.price_cents,
        "rights_declared": book.rights_declared,
        "policy_acknowledged_at": book.policy_acknowledged_at,
        "chapter_count": book.chapters.count(),
        "word_count": book.word_count,
        "submitted_at": book.submitted_at,
        "conversion_status": latest_job.status if latest_job else None,
        "conversion_confidence": latest_job.confidence_score if latest_job else None,
        "conversion_warnings": latest_job.warnings if latest_job else [],
        "author_open_restrictions": open_restrictions,
        "created_at": book.created_at,
    }


class AdminBookQueueView(APIView):
    """GET /control/admin/books/queue/ — filters: status, category, author_id, search, page, per_page."""
    permission_classes = [CanReviewBooks]
    required_permission = "books.review"

    def get(self, request):
        qs = Book.objects.select_related("owner").order_by("-submitted_at", "-created_at")
        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        else:
            qs = qs.filter(status__in=REVIEWABLE_STATUSES | {BookStatus.PROCESSING, BookStatus.CONVERSION_FAILED})
        category = request.query_params.get("category")
        if category:
            qs = qs.filter(christian_category=category)
        author_id = request.query_params.get("author_id")
        if author_id:
            qs = qs.filter(owner_id=author_id)
        search = request.query_params.get("search")
        if search:
            qs = qs.filter(title__icontains=search)

        page_num = _safe_int(request.query_params.get("page", 1), 1, lo=1, hi=10000)
        per_page = _safe_int(request.query_params.get("per_page", 25), 25, lo=1, hi=100)
        paginator = Paginator(qs, per_page)
        page_obj = paginator.get_page(page_num)
        return Response({
            "books": [_serialize_book_row(b) for b in page_obj.object_list],
            "pagination": {
                "page": page_obj.number, "per_page": per_page,
                "total_pages": paginator.num_pages, "total_items": paginator.count,
            },
        })


class AdminBookDetailReaderView(APIView):
    """
    GET /control/admin/books/<id>/ — the complete submission for review:
    every chapter in full, conversion diagnostics, rights/policy status.
    Logs a BookModerationAccess row every time a reviewer opens it.
    """
    permission_classes = [CanReviewBooks]
    required_permission = "books.review"

    def get(self, request, id):
        book = Book.objects.select_related("owner", "publisher_shop").filter(id=id).first()
        if not book:
            return Response({"detail": "Book not found."}, status=status.HTTP_404_NOT_FOUND)

        BookModerationAccess.objects.create(
            book=book, accessed_by=request.user, ip_address=request.META.get("REMOTE_ADDR", ""),
        )
        AuditLogger.log(
            actor=request.user, action_type="books.manuscript_viewed", target_app="books",
            target_model="Book", target_pk=str(book.id), severity="info", metadata={},
        )

        chapters = list(book.chapters.order_by("order"))
        manuscripts = list(book.manuscripts.order_by("-created_at"))
        jobs = []
        for m in manuscripts:
            jobs.extend(m.conversion_jobs.order_by("-created_at"))

        restrictions = BookPublishingRestriction.objects.filter(user=book.owner).order_by("-created_at")[:25]
        prior_books = Book.objects.filter(owner=book.owner).exclude(id=book.id).order_by("-created_at")[:25]

        return Response({
            "book": _serialize_book_row(book),
            "description": book.description,
            "rights_declaration_text": book.rights_declaration_text,
            "chapters": BookChapterFullSerializer(chapters, many=True).data,
            "manuscripts": [
                {
                    "id": str(m.id), "original_filename": m.original_filename,
                    "declared_format": m.declared_format, "status": m.status,
                    "file_size_bytes": m.file_size_bytes, "error_message": m.error_message,
                } for m in manuscripts
            ],
            "conversion_jobs": [
                {
                    "id": str(j.id), "status": j.status, "warnings": j.warnings,
                    "confidence_score": j.confidence_score, "chapters_detected": j.chapters_detected,
                    "images_extracted": j.images_extracted, "error_message": j.error_message,
                } for j in jobs
            ],
            "author_history": {
                "open_restrictions": [
                    {
                        "id": str(r.id), "restriction_type": r.restriction_type, "status": r.status,
                        "reason": r.reason, "starts_at": r.starts_at, "ends_at": r.ends_at,
                    } for r in restrictions
                ],
                "prior_books": [{"id": str(b.id), "title": b.title, "status": b.status} for b in prior_books],
            },
        })


class AdminBookActionView(APIView):
    """POST /control/admin/books/<id>/action/ — body: {action, notes}."""
    permission_classes = [CanReviewBooks]
    required_permission = "books.review"

    RESTRICTING_ACTIONS = {"block", "restore"}

    def post(self, request, id):
        book = Book.objects.filter(id=id).first()
        if not book:
            return Response({"detail": "Book not found."}, status=status.HTTP_404_NOT_FOUND)

        action = str(request.data.get("action", "")).strip().lower()
        notes = str(request.data.get("notes", "")).strip()

        # Block/restore is a step above plain review — require books.restrict
        # too, so a reviewer who can only approve/reject can't also suspend
        # a live, purchasable book on their own.
        from apps.books.permissions import can_restrict_books

        if action in self.RESTRICTING_ACTIONS and not can_restrict_books(request.user):
            return Response({"detail": "Book restriction authority required for this action."}, status=status.HTTP_403_FORBIDDEN)

        try:
            book = apply_moderation_decision(book, action=action, actor=request.user, notes=notes)
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        AuditLogger.log(
            actor=request.user, action_type=f"books.{action}", target_app="books",
            target_model="Book", target_pk=str(book.id), severity="warning" if action in {"block", "reject"} else "info",
            metadata={"notes": notes},
        )
        return Response(_serialize_book_row(book))


class AdminAuthorRestrictionView(APIView):
    """POST /control/admin/authors/<user_id>/restrictions/ — impose a
    publishing or account restriction. body: {restriction_type, reason,
    policy_reference, related_book_id, ends_at (ISO8601 or null)}."""
    permission_classes = [CanRestrictBooks]
    required_permission = "books.restrict"

    def post(self, request, user_id):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        target_user = User.objects.filter(id=user_id).first()
        if not target_user:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        restriction_type = str(request.data.get("restriction_type", "")).strip()
        valid_types = {c for c, _ in BookPublishingRestriction.TYPE_CHOICES}
        if restriction_type not in valid_types:
            return Response({"detail": f"Invalid restriction_type. Choose from: {sorted(valid_types)}"}, status=status.HTTP_400_BAD_REQUEST)

        from apps.books.permissions import can_ban_book_authors

        if restriction_type in ACCOUNT_LEVEL_RESTRICTIONS and not can_ban_book_authors(request.user):
            return Response({"detail": "Account-restriction authority required for this action."}, status=status.HTTP_403_FORBIDDEN)

        reason = str(request.data.get("reason", "")).strip()
        if not reason:
            return Response({"detail": "A reason is required."}, status=status.HTTP_400_BAD_REQUEST)

        ends_at = request.data.get("ends_at")
        ends_at_dt = None
        if ends_at:
            from django.utils.dateparse import parse_datetime

            ends_at_dt = parse_datetime(ends_at)
            if not ends_at_dt:
                return Response({"detail": "ends_at must be ISO-8601 or null."}, status=status.HTTP_400_BAD_REQUEST)

        related_book = None
        related_book_id = request.data.get("related_book_id")
        if related_book_id:
            related_book = Book.objects.filter(id=related_book_id).first()

        restriction = apply_restriction(
            user=target_user, restriction_type=restriction_type, reason=reason,
            imposed_by=request.user, policy_reference=str(request.data.get("policy_reference", "")),
            related_book=related_book, ends_at=ends_at_dt,
        )
        AuditLogger.log(
            actor=request.user, action_type="books.author_restricted", target_app="books",
            target_model="BookPublishingRestriction", target_pk=str(restriction.id),
            severity="warning", metadata={"restriction_type": restriction_type, "reason": reason, "user_id": str(target_user.id)},
        )
        return Response({
            "id": str(restriction.id), "restriction_type": restriction.restriction_type,
            "status": restriction.status, "starts_at": restriction.starts_at, "ends_at": restriction.ends_at,
        }, status=status.HTTP_201_CREATED)


class AdminAuthorRestrictionLiftView(APIView):
    """POST /control/admin/authors/restrictions/<id>/lift/ — body: {notes}."""
    permission_classes = [CanRestrictBooks]
    required_permission = "books.restrict"

    def post(self, request, id):
        restriction = BookPublishingRestriction.objects.filter(id=id).first()
        if not restriction:
            return Response({"detail": "Restriction not found."}, status=status.HTTP_404_NOT_FOUND)
        if restriction.status != BookPublishingRestriction.STATUS_ACTIVE:
            return Response({"detail": "This restriction is not active."}, status=status.HTTP_400_BAD_REQUEST)

        from apps.books.permissions import can_ban_book_authors

        if restriction.restriction_type in ACCOUNT_LEVEL_RESTRICTIONS and not can_ban_book_authors(request.user):
            return Response({"detail": "Account-restriction authority required to lift this restriction."}, status=status.HTTP_403_FORBIDDEN)

        restriction = lift_restriction(restriction, lifted_by=request.user, notes=str(request.data.get("notes", "")))
        AuditLogger.log(
            actor=request.user, action_type="books.restriction_lifted", target_app="books",
            target_model="BookPublishingRestriction", target_pk=str(restriction.id), severity="info", metadata={},
        )
        return Response({"id": str(restriction.id), "status": restriction.status, "lifted_at": restriction.lifted_at})


class AdminAuthorModerationHistoryView(APIView):
    """GET /control/admin/authors/<user_id>/moderation-history/"""
    permission_classes = [CanReviewBooks]
    required_permission = "books.review"

    def get(self, request, user_id):
        books = Book.objects.filter(owner_id=user_id).order_by("-created_at")
        restrictions = BookPublishingRestriction.objects.filter(user_id=user_id).order_by("-created_at")
        from apps.moderation.models import Flag

        flags = Flag.objects.filter(
            target_type="book", target_id__in=books.values("id")
        ).order_by("-created_at")[:50]
        return Response({
            "books": [_serialize_book_row(b) for b in books],
            "restrictions": [
                {
                    "id": str(r.id), "restriction_type": r.restriction_type, "status": r.status,
                    "reason": r.reason, "starts_at": r.starts_at, "ends_at": r.ends_at,
                    "lifted_at": r.lifted_at, "imposed_by_id": str(r.imposed_by_id) if r.imposed_by_id else None,
                } for r in restrictions
            ],
            "flags": [
                {"id": str(f.id), "status": f.status, "severity": f.severity, "reason": f.reason, "created_at": f.created_at}
                for f in flags
            ],
        })
