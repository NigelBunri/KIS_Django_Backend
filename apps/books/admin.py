from django.contrib import admin

from .models import (
    Book,
    BookChapter,
    BookChapterBookmark,
    BookContentKey,
    BookConversionJob,
    BookEntitlement,
    BookHighlight,
    BookLibraryItem,
    BookManuscript,
    BookModerationAccess,
    BookNote,
    BookPolicyAcknowledgment,
    BookPublishingRestriction,
    BookOfflineLicense,
    BookPurchase,
    BookReadingProgress,
    BookReview,
    BookRoyaltyRecord,
    ChristianPublishingPolicyVersion,
)


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = ["title", "author_name", "owner", "status", "is_free", "price_cents", "created_at"]
    list_filter = ["status", "is_free", "christian_category"]
    search_fields = ["title", "author_name", "owner__email"]


@admin.register(BookManuscript)
class BookManuscriptAdmin(admin.ModelAdmin):
    list_display = ["book", "declared_format", "status", "file_size_bytes", "created_at"]


@admin.register(BookConversionJob)
class BookConversionJobAdmin(admin.ModelAdmin):
    list_display = ["manuscript", "status", "attempt_number", "confidence_score", "chapters_detected"]


@admin.register(BookContentKey)
class BookContentKeyAdmin(admin.ModelAdmin):
    # wrapped_dek is encrypted ciphertext, not the key itself — safe to
    # show its existence, not worth exposing the raw token in list view.
    list_display = ["book", "key_version", "created_at"]
    readonly_fields = ["book", "key_version", "created_at"]


admin.site.register(BookChapter)
admin.site.register(BookEntitlement)
admin.site.register(BookReadingProgress)
admin.site.register(ChristianPublishingPolicyVersion)
admin.site.register(BookPolicyAcknowledgment)
admin.site.register(BookPublishingRestriction)
admin.site.register(BookModerationAccess)
admin.site.register(BookLibraryItem)
admin.site.register(BookChapterBookmark)
admin.site.register(BookHighlight)
admin.site.register(BookNote)


@admin.register(BookReview)
class BookReviewAdmin(admin.ModelAdmin):
    list_display = ["book", "user", "rating", "created_at"]
    list_filter = ["rating"]


@admin.register(BookPurchase)
class BookPurchaseAdmin(admin.ModelAdmin):
    list_display = ["book", "buyer", "amount_cents", "status", "created_at", "refunded_at"]
    list_filter = ["status", "payment_method"]


@admin.register(BookRoyaltyRecord)
class BookRoyaltyRecordAdmin(admin.ModelAdmin):
    list_display = ["book", "author", "gross_amount_cents", "net_royalty_cents", "status", "created_at"]
    list_filter = ["status"]
    readonly_fields = ["purchase", "book", "author", "gross_amount_cents", "commission_pct", "commission_cents", "net_royalty_cents"]


@admin.register(BookOfflineLicense)
class BookOfflineLicenseAdmin(admin.ModelAdmin):
    list_display = ["book", "user", "device_id", "status", "package_version", "expires_at"]
    list_filter = ["status"]
    # wrapped_package_dek is Fernet ciphertext, not the key itself — safe
    # to show it exists, never worth exposing/editing from the admin UI.
    readonly_fields = ["wrapped_package_dek"]
