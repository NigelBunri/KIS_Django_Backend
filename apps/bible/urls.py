from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    KCANBookListView,
    KCANMessageTopicListView,
    KCANMinisterListView,
    KCANMessageListView,
    TranslationListView,
    BibleTranslationMetadataViewSet,
    BookListView,
    ChapterListView,
    ReaderView,
    ParallelReaderView,
    VerseSearchView,
    DailyDevotionalView,
    BibleDailyPassageViewSet,
    BibleMeditationPostViewSet,
    BiblePrayerMonthViewSet,
    BiblePrayerDayViewSet,
    BibleContentAuditLogViewSet,
    MeditationTopicViewSet,
    MeditationScheduleViewSet,
    MeditationEntryViewSet,
    PrayerRequestViewSet,
    BibleCourseViewSet,
    BibleCourseModuleViewSet,
    BibleLessonViewSet,
    BibleCourseEnrollmentViewSet,
    BibleLessonProgressViewSet,
    ReadingPlanViewSet,
    ReadingPlanEnrollmentViewSet,
    ReadingHistoryViewSet,
    BibleReadingPlanEventViewSet,
    BibleBookmarkViewSet,
    BibleNoteViewSet,
    BibleHighlightViewSet,
    MemoryVerseViewSet,
    BiblePreferenceViewSet,
    BibleCrossReferenceViewSet,
    BibleStatsView,
    BibleSpiritualGrowthSummaryView,
    BibleCourseReactionView,
    BibleCourseCommentViewSet,
    BibleCourseShareView,
    BibleLessonReactionView,
    BibleLessonCommentViewSet,
    BibleCourseTrackViewSet,
    BibleCourseTrackItemViewSet,
    BibleCoursePrerequisiteViewSet,
    BibleQuizViewSet,
    BibleQuizQuestionViewSet,
    BibleQuizChoiceViewSet,
    BibleQuizAttemptViewSet,
    BibleAssignmentViewSet,
    BibleAssignmentSubmissionViewSet,
    BiblePeerReviewViewSet,
    BibleCourseForumViewSet,
    BibleForumThreadViewSet,
    BibleForumPostViewSet,
    BibleMentorAssignmentViewSet,
    BibleLiveSessionViewSet,
    BibleLiveAttendanceViewSet,
    BibleLiveRecordingViewSet,
    BibleCourseBundleViewSet,
    BibleCourseBundleItemViewSet,
    BibleCourseCouponViewSet,
    BibleEnterpriseSeatPoolViewSet,
    BibleRefundRequestViewSet,
    BibleCourseCredentialViewSet,
    BibleCourseCredentialShareView,
)

app_name = "bible"

router = DefaultRouter()
router.register("bible/translation-registry", BibleTranslationMetadataViewSet, basename="bible-translation-registry")
router.register("bible/topics", MeditationTopicViewSet, basename="bible-topics")
router.register("bible/daily-passages", BibleDailyPassageViewSet, basename="bible-daily-passages")
router.register("bible/meditation-posts", BibleMeditationPostViewSet, basename="bible-meditation-posts")
router.register("bible/prayer-months", BiblePrayerMonthViewSet, basename="bible-prayer-months")
router.register("bible/prayer-days", BiblePrayerDayViewSet, basename="bible-prayer-days")
router.register("bible/content-audit", BibleContentAuditLogViewSet, basename="bible-content-audit")
router.register("bible/schedules", MeditationScheduleViewSet, basename="bible-schedules")
router.register("bible/meditations", MeditationEntryViewSet, basename="bible-meditations")
router.register("bible/prayers", PrayerRequestViewSet, basename="bible-prayers")
router.register("bible/courses", BibleCourseViewSet, basename="bible-courses")
router.register("bible/course-modules", BibleCourseModuleViewSet, basename="bible-course-modules")
router.register("bible/lessons", BibleLessonViewSet, basename="bible-lessons")
router.register("bible/course-enrollments", BibleCourseEnrollmentViewSet, basename="bible-course-enrollments")
router.register("bible/lesson-progress", BibleLessonProgressViewSet, basename="bible-lesson-progress")
router.register("bible/course-comments", BibleCourseCommentViewSet, basename="bible-course-comments")
router.register("bible/lesson-comments", BibleLessonCommentViewSet, basename="bible-lesson-comments")
router.register("bible/course-tracks", BibleCourseTrackViewSet, basename="bible-course-tracks")
router.register("bible/course-track-items", BibleCourseTrackItemViewSet, basename="bible-course-track-items")
router.register("bible/course-prerequisites", BibleCoursePrerequisiteViewSet, basename="bible-course-prerequisites")
router.register("bible/quizzes", BibleQuizViewSet, basename="bible-quizzes")
router.register("bible/quiz-questions", BibleQuizQuestionViewSet, basename="bible-quiz-questions")
router.register("bible/quiz-choices", BibleQuizChoiceViewSet, basename="bible-quiz-choices")
router.register("bible/quiz-attempts", BibleQuizAttemptViewSet, basename="bible-quiz-attempts")
router.register("bible/assignments", BibleAssignmentViewSet, basename="bible-assignments")
router.register("bible/assignment-submissions", BibleAssignmentSubmissionViewSet, basename="bible-assignment-submissions")
router.register("bible/peer-reviews", BiblePeerReviewViewSet, basename="bible-peer-reviews")
router.register("bible/course-forums", BibleCourseForumViewSet, basename="bible-course-forums")
router.register("bible/forum-threads", BibleForumThreadViewSet, basename="bible-forum-threads")
router.register("bible/forum-posts", BibleForumPostViewSet, basename="bible-forum-posts")
router.register("bible/mentors", BibleMentorAssignmentViewSet, basename="bible-mentors")
router.register("bible/live-sessions", BibleLiveSessionViewSet, basename="bible-live-sessions")
router.register("bible/live-attendance", BibleLiveAttendanceViewSet, basename="bible-live-attendance")
router.register("bible/live-recordings", BibleLiveRecordingViewSet, basename="bible-live-recordings")
router.register("bible/course-bundles", BibleCourseBundleViewSet, basename="bible-course-bundles")
router.register("bible/course-bundle-items", BibleCourseBundleItemViewSet, basename="bible-course-bundle-items")
router.register("bible/course-coupons", BibleCourseCouponViewSet, basename="bible-course-coupons")
router.register("bible/course-seat-pools", BibleEnterpriseSeatPoolViewSet, basename="bible-course-seat-pools")
router.register("bible/course-refunds", BibleRefundRequestViewSet, basename="bible-course-refunds")
router.register("bible/credentials", BibleCourseCredentialViewSet, basename="bible-credentials")
router.register("bible/plans", ReadingPlanViewSet, basename="bible-plans")
router.register("bible/plan-enrollments", ReadingPlanEnrollmentViewSet, basename="bible-plan-enrollments")
router.register("bible/history", ReadingHistoryViewSet, basename="bible-history")
router.register("bible/reading-events", BibleReadingPlanEventViewSet, basename="bible-reading-events")
router.register("bible/bookmarks", BibleBookmarkViewSet, basename="bible-bookmarks")
router.register("bible/notes", BibleNoteViewSet, basename="bible-notes")
router.register("bible/highlights", BibleHighlightViewSet, basename="bible-highlights")
router.register("bible/memory", MemoryVerseViewSet, basename="bible-memory")
router.register("bible/preferences", BiblePreferenceViewSet, basename="bible-preferences")
router.register("bible/cross-references", BibleCrossReferenceViewSet, basename="bible-cross-references")
router.register("bible/kcan-books", KCANBookListView, basename="kcan-books")
router.register("bible/kcan-message-topics", KCANMessageTopicListView, basename="kcan-message-topics")
router.register("bible/kcan-ministers", KCANMinisterListView, basename="kcan-ministers")
router.register("bible/kcan-messages", KCANMessageListView, basename="kcan-messages")

urlpatterns = [
    path("bible/translations/", TranslationListView.as_view(), name="bible-translations"),
    path("bible/books/", BookListView.as_view(), name="bible-books"),
    path("bible/chapters/", ChapterListView.as_view(), name="bible-chapters"),
    path("bible/reader/", ReaderView.as_view(), name="bible-reader"),
    path("bible/reader/parallel/", ParallelReaderView.as_view(), name="bible-reader-parallel"),
    path("bible/search/", VerseSearchView.as_view(), name="bible-search"),
    path("bible/daily/", DailyDevotionalView.as_view(), name="bible-daily"),
    path("bible/courses/<int:pk>/react/", BibleCourseReactionView.as_view(), name="bible-course-react"),
    path("bible/courses/<int:pk>/share/", BibleCourseShareView.as_view(), name="bible-course-share"),
    path("bible/lessons/<int:pk>/react/", BibleLessonReactionView.as_view(), name="bible-lesson-react"),
    path("bible/credentials/share/<str:token>/", BibleCourseCredentialShareView.as_view(), name="bible-credential-share"),
    path("bible/stats/", BibleStatsView.as_view(), name="bible-stats"),
    path("bible/spiritual-growth-summary/", BibleSpiritualGrowthSummaryView.as_view(), name="bible-spiritual-growth-summary"),
    path("", include(router.urls)),
]
