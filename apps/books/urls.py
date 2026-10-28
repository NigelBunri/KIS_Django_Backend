from django.urls import path

from . import views

urlpatterns = [
    # Public marketplace
    path("books/", views.BookListView.as_view(), name="books-list"),
    path("books/discover/", views.DiscoveryView.as_view(), name="books-discover"),
    path("books/policy/current/", views.ChristianPolicyCurrentView.as_view(), name="books-policy-current"),
    path("books/<uuid:id>/", views.BookDetailView.as_view(), name="books-detail"),
    path("books/<uuid:id>/chapters/<uuid:stable_id>/", views.BookChapterReadView.as_view(), name="books-chapter-read"),
    path("books/<uuid:id>/get-free/", views.FreeAccessView.as_view(), name="books-get-free"),

    # Author studio
    path("books/mine/", views.AuthorBookListCreateView.as_view(), name="books-mine"),
    path("books/mine/<uuid:id>/", views.AuthorBookDetailView.as_view(), name="books-mine-detail"),
    path("books/mine/<uuid:id>/chapters/<uuid:stable_id>/", views.AuthorChapterUpdateView.as_view(), name="books-mine-chapter"),
    path("books/mine/<uuid:id>/acknowledge-policy/", views.AcknowledgePolicyView.as_view(), name="books-acknowledge-policy"),
    path("books/mine/<uuid:id>/manuscript/", views.ManuscriptUploadView.as_view(), name="books-manuscript-upload"),
    path("books/mine/<uuid:id>/conversion/", views.ConversionStatusView.as_view(), name="books-conversion-status"),
    path("books/mine/<uuid:id>/image-placements/", views.ImagePlacementsListView.as_view(), name="books-image-placements"),
    path("books/mine/<uuid:id>/image-placements/<str:block_id>/image/", views.ImagePlacementImageView.as_view(), name="books-image-placement-upload"),
    path("books/images/<uuid:asset_id>/", views.BookImageAssetDownloadView.as_view(), name="books-image-download"),
    path("books/mine/<uuid:id>/submit/", views.SubmitForReviewView.as_view(), name="books-submit"),
    path("books/eligibility/paid/", views.PaidEligibilityView.as_view(), name="books-paid-eligibility"),

    # Library / reading
    path("books/library/mine/", views.MyLibraryView.as_view(), name="books-library-mine"),
    path("books/library/overview/", views.PersonalLibraryOverviewView.as_view(), name="books-library-overview"),
    path("books/<uuid:id>/progress/", views.ReadingProgressView.as_view(), name="books-progress"),
    path("books/<uuid:id>/save/", views.SaveBookView.as_view(), name="books-save"),

    # Bookmarks
    path("books/<uuid:id>/chapters/<uuid:stable_id>/bookmark/", views.ChapterBookmarkToggleView.as_view(), name="books-chapter-bookmark-toggle"),
    path("books/<uuid:id>/bookmarks/", views.BookChapterBookmarksListView.as_view(), name="books-chapter-bookmarks-list"),

    # Highlights
    path("books/<uuid:id>/chapters/<uuid:stable_id>/highlights/", views.ChapterHighlightListCreateView.as_view(), name="books-chapter-highlights"),
    path("books/<uuid:id>/highlights/", views.BookHighlightsListView.as_view(), name="books-highlights-list"),
    path("books/highlights/<uuid:highlight_id>/", views.HighlightDetailView.as_view(), name="books-highlight-detail"),
    path("books/annotations/mine/", views.MyAnnotationsView.as_view(), name="books-annotations-mine"),

    # Reviews
    path("books/<uuid:id>/reviews/", views.BookReviewListCreateView.as_view(), name="books-reviews"),
    path("books/reviews/<uuid:review_id>/", views.ReviewDetailView.as_view(), name="books-review-detail"),
    path("books/reviews/<uuid:review_id>/report/", views.ReportReviewView.as_view(), name="books-review-report"),

    # Notes
    path("books/<uuid:id>/chapters/<uuid:stable_id>/notes/", views.ChapterNoteListCreateView.as_view(), name="books-chapter-notes"),
    path("books/<uuid:id>/notes/", views.BookNotesListView.as_view(), name="books-notes-list"),
    path("books/notes/<uuid:note_id>/", views.NoteDetailView.as_view(), name="books-note-detail"),

    # Purchases
    path("books/<uuid:id>/checkout/", views.CheckoutView.as_view(), name="books-checkout"),
    path("books/purchases/mine/", views.MyPurchasesView.as_view(), name="books-purchases-mine"),
    path("books/purchases/<uuid:id>/refund/", views.RefundPurchaseView.as_view(), name="books-purchase-refund"),
    path("books/royalties/mine/", views.MyRoyaltiesView.as_view(), name="books-royalties-mine"),

    # Offline downloads
    path("books/offline/mine/", views.MyOfflineDownloadsView.as_view(), name="books-offline-mine"),
    path("books/<uuid:id>/offline/request/", views.RequestOfflineDownloadView.as_view(), name="books-offline-request"),
    path("books/<uuid:id>/offline/verify/", views.VerifyOfflineLicenseView.as_view(), name="books-offline-verify"),
    path("books/<uuid:id>/offline/<str:device_id>/", views.RevokeOfflineLicenseView.as_view(), name="books-offline-revoke"),
]
