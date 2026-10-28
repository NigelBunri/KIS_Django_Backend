from __future__ import annotations

from rest_framework import serializers

from .models import (
    Book,
    BookChapter,
    BookChapterBookmark,
    BookConversionJob,
    BookEntitlement,
    BookHighlight,
    BookImageAsset,
    BookLibraryItem,
    BookManuscript,
    BookNote,
    BookOfflineLicense,
    BookPublishingRestriction,
    BookPurchase,
    BookReadingProgress,
    BookReview,
    BookRoyaltyRecord,
    ChristianPublishingPolicyVersion,
)
from .services import user_has_reading_access


class ChristianPublishingPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = ChristianPublishingPolicyVersion
        fields = ["id", "version", "title", "body_markdown", "is_current", "created_at"]


class BookChapterSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = BookChapter
        fields = ["stable_id", "order", "title", "word_count", "source_page_range"]


class BookChapterFullSerializer(serializers.ModelSerializer):
    # content_blocks is a Python property (decrypts encrypted_content on
    # read) — not a real model field, so ModelSerializer can't introspect
    # it automatically; declared explicitly as a plain pass-through field.
    content_blocks = serializers.ListField(read_only=True)

    class Meta:
        model = BookChapter
        fields = ["stable_id", "order", "title", "content_blocks", "word_count", "source_page_range"]


class BookListSerializer(serializers.ModelSerializer):
    current_price_cents = serializers.IntegerField(read_only=True)
    chapter_count = serializers.SerializerMethodField()

    class Meta:
        model = Book
        fields = [
            "id", "title", "subtitle", "author_name", "cover_url", "language",
            "christian_category", "tags", "is_free", "price_cents", "currency",
            "current_price_cents", "status", "estimated_reading_minutes",
            "chapter_count", "published_at", "created_at",
            "average_rating", "review_count",
        ]

    def get_chapter_count(self, obj):
        return obj.chapters.count()


class BookDetailSerializer(BookListSerializer):
    has_access = serializers.SerializerMethodField()
    is_saved = serializers.SerializerMethodField()
    chapters = BookChapterSummarySerializer(many=True, read_only=True)

    class Meta(BookListSerializer.Meta):
        fields = BookListSerializer.Meta.fields + [
            "description", "word_count", "has_access", "is_saved", "chapters",
            "rejection_reason", "moderation_notes",
        ]

    def get_has_access(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        return user_has_reading_access(obj, user)

    def get_is_saved(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        if not user or not getattr(user, "is_authenticated", False):
            return False
        from .models import BookLibraryItem

        return BookLibraryItem.objects.filter(book=obj, user=user).exists()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        is_owner_or_staff = bool(user and getattr(user, "is_authenticated", False) and (
            instance.owner_id == user.id or getattr(user, "is_superuser", False)
        ))
        if not is_owner_or_staff:
            data.pop("rejection_reason", None)
            data.pop("moderation_notes", None)
        return data


class AuthorBookSerializer(serializers.ModelSerializer):
    current_price_cents = serializers.IntegerField(read_only=True)
    chapters = BookChapterSummarySerializer(many=True, read_only=True)

    class Meta:
        model = Book
        fields = [
            "id", "title", "subtitle", "author_name", "description", "cover_url",
            "language", "christian_category", "tags", "is_free", "price_cents",
            "currency", "current_price_cents", "sale_price_cents", "sale_starts_at",
            "sale_ends_at", "status", "rejection_reason", "moderation_notes",
            "rights_declared", "rights_declaration_text", "policy_acknowledged_at",
            "word_count", "estimated_reading_minutes", "scheduled_publish_at",
            "submitted_at", "published_at", "chapters", "created_at", "updated_at",
            "average_rating", "review_count", "view_count",
        ]
        read_only_fields = [
            "status", "rejection_reason", "moderation_notes", "submitted_at", "published_at",
            "average_rating", "review_count", "view_count",
        ]

    def validate(self, attrs):
        is_free = attrs.get("is_free", getattr(self.instance, "is_free", True))
        price_cents = attrs.get("price_cents", getattr(self.instance, "price_cents", 0))
        if not is_free and price_cents <= 0:
            raise serializers.ValidationError("Paid books must have a price greater than zero.")
        if is_free:
            attrs["price_cents"] = 0
        return attrs


class BookManuscriptSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookManuscript
        fields = [
            "id", "book", "original_filename", "declared_format", "detected_mime_type",
            "file_size_bytes", "status", "error_message", "created_at",
        ]
        read_only_fields = fields


class BookConversionJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookConversionJob
        fields = [
            "id", "manuscript", "attempt_number", "status", "started_at", "finished_at",
            "warnings", "confidence_score", "chapters_detected", "images_extracted", "error_message",
        ]
        read_only_fields = fields


class BookOfflineLicenseSerializer(serializers.ModelSerializer):
    book_title = serializers.CharField(source="book.title", read_only=True)

    class Meta:
        model = BookOfflineLicense
        # Deliberately excludes wrapped_package_dek — this serializer is
        # for listing/status display only; the raw DEK is only ever
        # returned once, inline in the issue/renew response body itself
        # (see views.RequestOfflineDownloadView), never through a
        # general-purpose serializer that could end up reused somewhere
        # it shouldn't be.
        fields = [
            "id", "book", "book_title", "device_id", "status", "package_version",
            "issued_at", "last_renewed_at", "expires_at", "revoked_at", "revoked_reason",
        ]
        read_only_fields = fields


class BookPurchaseSerializer(serializers.ModelSerializer):
    book_title = serializers.CharField(source="book.title", read_only=True)
    book_author_name = serializers.CharField(source="book.author_name", read_only=True)

    class Meta:
        model = BookPurchase
        fields = [
            "id", "book", "book_title", "book_author_name", "amount_cents", "currency", "status",
            "payment_method", "transaction_reference", "paid_at", "created_at",
            "refunded_at", "refund_reason",
        ]
        read_only_fields = fields


class BookRoyaltyRecordSerializer(serializers.ModelSerializer):
    book_title = serializers.CharField(source="book.title", read_only=True)

    class Meta:
        model = BookRoyaltyRecord
        fields = [
            "id", "book", "book_title", "gross_amount_cents", "commission_pct",
            "commission_cents", "net_royalty_cents", "status", "created_at", "reversed_at",
        ]
        read_only_fields = fields


class BookEntitlementSerializer(serializers.ModelSerializer):
    book = BookListSerializer(read_only=True)

    class Meta:
        model = BookEntitlement
        fields = ["id", "book", "source", "granted_at"]


class BookReadingProgressSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookReadingProgress
        fields = ["book", "chapter_stable_id", "block_index", "percent_complete", "is_completed", "last_read_at"]
        read_only_fields = ["book"]


class BookPublishingRestrictionSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookPublishingRestriction
        fields = [
            "id", "user", "restriction_type", "status", "reason", "policy_reference",
            "related_book", "imposed_by", "starts_at", "ends_at", "lifted_at",
            "lifted_by", "lift_notes", "created_at",
        ]
        read_only_fields = fields


class BookLibraryItemSerializer(serializers.ModelSerializer):
    book = BookListSerializer(read_only=True)

    class Meta:
        model = BookLibraryItem
        fields = ["id", "book", "created_at"]
        read_only_fields = fields


class BookChapterBookmarkSerializer(serializers.ModelSerializer):
    chapter_title = serializers.CharField(source="chapter.title", read_only=True)
    chapter_order = serializers.IntegerField(source="chapter.order", read_only=True)

    class Meta:
        model = BookChapterBookmark
        fields = ["id", "book", "chapter", "chapter_title", "chapter_order", "label", "created_at"]
        read_only_fields = ["id", "book", "chapter_title", "chapter_order", "created_at"]


class BookHighlightSerializer(serializers.ModelSerializer):
    chapter_stable_id = serializers.UUIDField(source="chapter.stable_id", read_only=True)

    class Meta:
        model = BookHighlight
        fields = [
            "id", "book", "chapter", "chapter_stable_id", "block_id", "start_offset",
            "end_offset", "color", "highlighted_text", "note", "created_at",
        ]
        read_only_fields = ["id", "book", "chapter_stable_id", "highlighted_text", "created_at"]


class MyBookAnnotationSerializer(BookHighlightSerializer):
    """One row of the personalized Highlights & Comments Center — a
    BookHighlight plus just enough book/chapter display context that the
    center doesn't need a second round-trip per card. `has_comment` and
    `resolved` are the two things the center's filtering/navigation UI
    actually branches on (see apps.books.services.resolve_highlight_anchor
    for what "resolved" means)."""
    book_title = serializers.CharField(source="book.title", read_only=True)
    book_cover_url = serializers.CharField(source="book.cover_url", read_only=True)
    chapter_title = serializers.CharField(source="chapter.title", read_only=True)
    chapter_order = serializers.IntegerField(source="chapter.order", read_only=True)
    has_comment = serializers.SerializerMethodField()
    resolved = serializers.SerializerMethodField()

    class Meta(BookHighlightSerializer.Meta):
        fields = BookHighlightSerializer.Meta.fields + [
            "book_title", "book_cover_url", "chapter_title", "chapter_order", "has_comment", "resolved", "updated_at",
        ]
        read_only_fields = BookHighlightSerializer.Meta.read_only_fields + [
            "book_title", "book_cover_url", "chapter_title", "chapter_order", "has_comment", "resolved", "updated_at",
        ]

    def get_has_comment(self, obj) -> bool:
        return bool(obj.note)

    def get_resolved(self, obj) -> bool:
        from .services import resolve_highlight_anchor

        return resolve_highlight_anchor(obj)["resolved"]


class BookImageAssetSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookImageAsset
        fields = ["id", "width", "height", "content_type", "file_size_bytes", "source", "created_at"]
        read_only_fields = fields


class BookReviewSerializer(serializers.ModelSerializer):
    reviewer_display_name = serializers.SerializerMethodField()

    class Meta:
        model = BookReview
        fields = ["id", "book", "user", "rating", "review_text", "reviewer_display_name", "created_at", "updated_at"]
        read_only_fields = ["id", "book", "user", "reviewer_display_name", "created_at", "updated_at"]

    def get_reviewer_display_name(self, obj):
        return getattr(obj.user, "display_name", "") or getattr(obj.user, "username", "") or "KIS Reader"


class BookNoteSerializer(serializers.ModelSerializer):
    chapter_stable_id = serializers.SerializerMethodField()

    class Meta:
        model = BookNote
        fields = ["id", "book", "chapter", "chapter_stable_id", "block_id", "text", "created_at", "updated_at"]
        read_only_fields = ["id", "book", "chapter_stable_id", "created_at", "updated_at"]

    def get_chapter_stable_id(self, obj):
        return str(obj.chapter.stable_id) if obj.chapter_id else None
