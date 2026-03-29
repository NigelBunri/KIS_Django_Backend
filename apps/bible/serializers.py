from rest_framework import serializers

from .models import (
    BibleTranslation,
    BibleBook,
    BibleChapter,
    BibleVerse,
    BibleAudio,
    BibleAudioSegment,
    DailyDevotional,
    PrayerRequest,
    MeditationTopic,
    MeditationSchedule,
    MeditationEntry,
    BibleChatSession,
    BibleChatMessage,
    ReadingPlan,
    ReadingPlanItem,
    ReadingPlanEnrollment,
    ReadingHistory,
    BibleBookmark,
    BibleNote,
    BibleHighlight,
    MemoryVerse,
    BiblePreference,
    BibleCrossReference,
    BibleCourse,
    BibleCourseModule,
    BibleLesson,
    BibleCourseEnrollment,
    BibleLessonProgress,
    BibleLessonReaction,
    BibleLessonComment,
    BibleCourseReaction,
    BibleCourseComment,
    BibleCourseShare,
    BibleCourseTrack,
    BibleCourseTrackItem,
    BibleCoursePrerequisite,
    BibleQuiz,
    BibleQuizQuestion,
    BibleQuizChoice,
    BibleQuizAttempt,
    BibleAssignment,
    BibleAssignmentSubmission,
    BiblePeerReview,
    BibleCourseForum,
    BibleForumThread,
    BibleForumPost,
    BibleMentorAssignment,
    BibleLiveSession,
    BibleLiveAttendance,
    BibleLiveRecording,
    BibleCourseBundle,
    BibleCourseBundleItem,
    BibleCourseCoupon,
    BibleEnterpriseSeatPool,
    BibleRefundRequest,
    BibleCourseCredential,
)


class BibleTranslationSerializer(serializers.ModelSerializer):
    class Meta:
        model = BibleTranslation
        fields = ["id", "code", "name", "language", "sort_order", "is_active"]


class BibleBookSerializer(serializers.ModelSerializer):
    class Meta:
        model = BibleBook
        fields = ["id", "code", "name", "testament", "order"]


class BibleChapterSerializer(serializers.ModelSerializer):
    book = BibleBookSerializer(read_only=True)

    class Meta:
        model = BibleChapter
        fields = ["id", "book", "number"]


class BibleVerseSerializer(serializers.ModelSerializer):
    chapter = BibleChapterSerializer(read_only=True)

    class Meta:
        model = BibleVerse
        fields = ["id", "translation", "chapter", "number", "text"]


class BibleAudioSegmentSerializer(serializers.ModelSerializer):
    verse_number = serializers.IntegerField(source="verse.number", read_only=True)

    class Meta:
        model = BibleAudioSegment
        fields = ["id", "verse", "verse_number", "start_ms", "end_ms"]


class BibleAudioSerializer(serializers.ModelSerializer):
    segments = BibleAudioSegmentSerializer(many=True, read_only=True)

    class Meta:
        model = BibleAudio
        fields = ["id", "translation", "chapter", "audio_file", "duration_ms", "segments"]


class DailyDevotionalSerializer(serializers.ModelSerializer):
    translation = BibleTranslationSerializer(read_only=True)

    class Meta:
        model = DailyDevotional
        fields = ["id", "date", "translation", "passage_ref", "title", "content", "prayer_text"]


class PrayerRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = PrayerRequest
        fields = ["id", "title", "content", "answered", "is_public", "created_at"]
        read_only_fields = ["answered", "created_at"]


class MeditationTopicSerializer(serializers.ModelSerializer):
    class Meta:
        model = MeditationTopic
        fields = ["id", "name", "description", "is_active"]


class MeditationScheduleSerializer(serializers.ModelSerializer):
    topic_detail = MeditationTopicSerializer(source="topic", read_only=True)

    class Meta:
        model = MeditationSchedule
        fields = [
            "id",
            "topic",
            "topic_detail",
            "topic_label",
            "cadence",
            "time_of_day",
            "timezone",
            "active",
            "created_at",
        ]
        read_only_fields = ["created_at"]


class MeditationEntrySerializer(serializers.ModelSerializer):
    topic_detail = MeditationTopicSerializer(source="topic", read_only=True)

    class Meta:
        model = MeditationEntry
        fields = ["id", "topic", "topic_detail", "date", "title", "content", "prayer_text", "source", "created_at"]
        read_only_fields = ["created_at"]


class BibleChatMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = BibleChatMessage
        fields = ["id", "role", "content", "created_at"]
        read_only_fields = ["created_at"]


class BibleChatSessionSerializer(serializers.ModelSerializer):
    messages = BibleChatMessageSerializer(many=True, read_only=True)

    class Meta:
        model = BibleChatSession
        fields = ["id", "topic", "created_at", "updated_at", "messages"]
        read_only_fields = ["created_at", "updated_at"]


class ReadingPlanItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReadingPlanItem
        fields = ["id", "day_index", "passage_ref"]


class ReadingPlanSerializer(serializers.ModelSerializer):
    items = ReadingPlanItemSerializer(many=True, read_only=True)

    class Meta:
        model = ReadingPlan
        fields = ["id", "name", "description", "is_system", "days_count", "items"]


class ReadingPlanEnrollmentSerializer(serializers.ModelSerializer):
    plan_detail = ReadingPlanSerializer(source="plan", read_only=True)

    class Meta:
        model = ReadingPlanEnrollment
        fields = ["id", "plan", "plan_detail", "start_date", "current_day", "status"]


class ReadingHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ReadingHistory
        fields = ["id", "translation", "chapter", "last_verse", "updated_at"]
        read_only_fields = ["updated_at"]


class BibleBookmarkSerializer(serializers.ModelSerializer):
    verse_text = serializers.CharField(source="verse.text", read_only=True)
    verse_ref = serializers.SerializerMethodField()

    class Meta:
        model = BibleBookmark
        fields = ["id", "verse", "verse_text", "verse_ref", "created_at"]
        read_only_fields = ["created_at"]

    def get_verse_ref(self, obj):
        return f"{obj.verse.chapter.book.name} {obj.verse.chapter.number}:{obj.verse.number}"


class BibleNoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = BibleNote
        fields = ["id", "verse", "text", "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at"]


class BibleHighlightSerializer(serializers.ModelSerializer):
    class Meta:
        model = BibleHighlight
        fields = ["id", "verse", "color", "created_at"]
        read_only_fields = ["created_at"]


class MemoryVerseSerializer(serializers.ModelSerializer):
    class Meta:
        model = MemoryVerse
        fields = ["id", "verse", "next_review_date", "streak", "created_at"]
        read_only_fields = ["created_at"]


class BiblePreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = BiblePreference
        fields = [
            "id",
            "default_translation",
            "font_size",
            "audio_speed",
            "enable_audio_sync",
            "enable_parallel_view",
            "enable_daily_reminders",
            "enable_offline_cache",
        ]


class BibleCrossReferenceSerializer(serializers.ModelSerializer):
    verse_ref = serializers.SerializerMethodField()
    related_ref = serializers.SerializerMethodField()

    class Meta:
        model = BibleCrossReference
        fields = ["id", "verse", "related_verse", "note", "verse_ref", "related_ref"]

    def get_verse_ref(self, obj):
        return f"{obj.verse.chapter.book.name} {obj.verse.chapter.number}:{obj.verse.number}"

    def get_related_ref(self, obj):
        return f"{obj.related_verse.chapter.book.name} {obj.related_verse.chapter.number}:{obj.related_verse.number}"


class BibleCourseModuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = BibleCourseModule
        fields = ["id", "course", "title", "summary", "order"]


class BibleCourseTrackItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = BibleCourseTrackItem
        fields = ["id", "track", "course", "order"]


class BibleCourseTrackSerializer(serializers.ModelSerializer):
    items = BibleCourseTrackItemSerializer(many=True, read_only=True)

    class Meta:
        model = BibleCourseTrack
        fields = ["id", "partner", "title", "description", "is_published", "created_at", "items"]


class BibleCoursePrerequisiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = BibleCoursePrerequisite
        fields = ["id", "course", "required_course", "required_percent"]


class BibleLessonSerializer(serializers.ModelSerializer):
    module_detail = BibleCourseModuleSerializer(source="module", read_only=True)
    completed = serializers.SerializerMethodField()
    reaction_count = serializers.SerializerMethodField()
    comment_count = serializers.SerializerMethodField()
    viewer_reaction = serializers.SerializerMethodField()
    last_position_ms = serializers.SerializerMethodField()

    class Meta:
        model = BibleLesson
        fields = [
            "id",
            "course",
            "module",
            "module_detail",
            "title",
            "summary",
            "content",
            "transcript",
            "captions_url",
            "language",
            "order",
            "duration_minutes",
            "video_url",
            "audio_url",
            "attachments",
            "is_free",
            "completed",
            "reaction_count",
            "comment_count",
            "viewer_reaction",
            "last_position_ms",
        ]

    def get_completed(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or user.is_anonymous:
            return False
        return BibleLessonProgress.objects.filter(user=user, lesson=obj, completed=True).exists()

    def get_reaction_count(self, obj):
        return obj.reactions.count()

    def get_comment_count(self, obj):
        return obj.comments.count()

    def get_viewer_reaction(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or user.is_anonymous:
            return None
        reaction = BibleLessonReaction.objects.filter(user=user, lesson=obj).first()
        return reaction.emoji if reaction else None

    def get_last_position_ms(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or user.is_anonymous:
            return 0
        progress = BibleLessonProgress.objects.filter(user=user, lesson=obj).first()
        return progress.last_position_ms if progress else 0


class BibleQuizChoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = BibleQuizChoice
        fields = ["id", "question", "text", "is_correct"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or user.is_anonymous:
            data.pop("is_correct", None)
            return data
        course = instance.question.quiz.course
        if course.partner and course.partner.owner_id != user.id:
            data.pop("is_correct", None)
        return data


class BibleQuizQuestionSerializer(serializers.ModelSerializer):
    choices = BibleQuizChoiceSerializer(many=True, read_only=True)

    class Meta:
        model = BibleQuizQuestion
        fields = ["id", "quiz", "prompt", "kind", "points", "order", "choices"]


class BibleQuizSerializer(serializers.ModelSerializer):
    questions = BibleQuizQuestionSerializer(many=True, read_only=True)

    class Meta:
        model = BibleQuiz
        fields = [
            "id",
            "course",
            "lesson",
            "title",
            "description",
            "pass_score",
            "time_limit_minutes",
            "attempts_allowed",
            "is_exam",
            "is_active",
            "order",
            "questions",
        ]


class BibleQuizAttemptSerializer(serializers.ModelSerializer):
    class Meta:
        model = BibleQuizAttempt
        fields = ["id", "quiz", "user", "score", "max_score", "passed", "answers", "started_at", "completed_at"]
        read_only_fields = ["user", "score", "max_score", "passed", "started_at", "completed_at"]


class BibleAssignmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = BibleAssignment
        fields = ["id", "course", "lesson", "title", "description", "due_at", "max_points", "rubric", "order", "is_required"]


class BibleAssignmentSubmissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = BibleAssignmentSubmission
        fields = [
            "id",
            "assignment",
            "user",
            "content_text",
            "attachments",
            "score",
            "feedback",
            "status",
            "plagiarism_score",
            "plagiarism_status",
            "created_at",
            "graded_at",
        ]
        read_only_fields = ["user", "score", "feedback", "status", "plagiarism_score", "plagiarism_status", "created_at", "graded_at"]


class BiblePeerReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = BiblePeerReview
        fields = ["id", "submission", "reviewer", "score", "feedback", "created_at"]
        read_only_fields = ["reviewer", "created_at"]


class BibleForumPostSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.display_name", read_only=True)

    class Meta:
        model = BibleForumPost
        fields = ["id", "thread", "user", "user_name", "content", "created_at"]
        read_only_fields = ["user", "created_at", "user_name"]


class BibleForumThreadSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source="created_by.display_name", read_only=True)
    posts = BibleForumPostSerializer(many=True, read_only=True)

    class Meta:
        model = BibleForumThread
        fields = ["id", "forum", "title", "created_by", "created_by_name", "is_pinned", "is_locked", "created_at", "posts"]
        read_only_fields = ["created_by", "created_at"]


class BibleCourseForumSerializer(serializers.ModelSerializer):
    threads = BibleForumThreadSerializer(many=True, read_only=True)

    class Meta:
        model = BibleCourseForum
        fields = ["id", "course", "is_locked", "created_at", "threads"]


class BibleMentorAssignmentSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.display_name", read_only=True)

    class Meta:
        model = BibleMentorAssignment
        fields = ["id", "course", "user", "user_name", "role", "created_at"]
        read_only_fields = ["created_at"]


class BibleLiveRecordingSerializer(serializers.ModelSerializer):
    class Meta:
        model = BibleLiveRecording
        fields = ["id", "session", "title", "media_url", "duration_minutes", "created_at"]


class BibleLiveAttendanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = BibleLiveAttendance
        fields = ["id", "session", "user", "status", "joined_at", "left_at"]
        read_only_fields = ["user", "joined_at", "left_at"]


class BibleLiveSessionSerializer(serializers.ModelSerializer):
    host_name = serializers.CharField(source="host.display_name", read_only=True)
    recordings = BibleLiveRecordingSerializer(many=True, read_only=True)

    class Meta:
        model = BibleLiveSession
        fields = ["id", "course", "title", "description", "start_at", "end_at", "meeting_url", "host", "host_name", "is_recorded", "created_at", "recordings"]


class BibleCourseBundleItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = BibleCourseBundleItem
        fields = ["id", "bundle", "course", "order"]


class BibleCourseBundleSerializer(serializers.ModelSerializer):
    items = BibleCourseBundleItemSerializer(many=True, read_only=True)

    class Meta:
        model = BibleCourseBundle
        fields = ["id", "partner", "title", "description", "price_amount", "price_currency", "is_active", "created_at", "items"]


class BibleCourseCouponSerializer(serializers.ModelSerializer):
    class Meta:
        model = BibleCourseCoupon
        fields = ["id", "code", "course", "bundle", "percent_off", "amount_off", "max_redemptions", "redeemed_count", "valid_until", "is_active", "created_at"]


class BibleEnterpriseSeatPoolSerializer(serializers.ModelSerializer):
    class Meta:
        model = BibleEnterpriseSeatPool
        fields = ["id", "partner", "course", "seats_total", "seats_used", "expires_at", "created_at"]


class BibleRefundRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = BibleRefundRequest
        fields = ["id", "enrollment", "reason", "status", "created_at"]
        read_only_fields = ["status", "created_at"]


class BibleCourseCredentialSerializer(serializers.ModelSerializer):
    class Meta:
        model = BibleCourseCredential
        fields = ["id", "enrollment", "badge_name", "share_token", "issued_at"]


class BibleCourseSerializer(serializers.ModelSerializer):
    modules = BibleCourseModuleSerializer(many=True, read_only=True)
    lessons = BibleLessonSerializer(many=True, read_only=True)
    prerequisites = BibleCoursePrerequisiteSerializer(many=True, read_only=True)
    reaction_count = serializers.SerializerMethodField()
    comment_count = serializers.SerializerMethodField()
    viewer_reaction = serializers.SerializerMethodField()
    is_enrolled = serializers.SerializerMethodField()
    enrollment_id = serializers.SerializerMethodField()
    enrollment_status = serializers.SerializerMethodField()
    enrollment_progress = serializers.SerializerMethodField()
    enrollment_paid = serializers.SerializerMethodField()
    partner_name = serializers.CharField(source="partner.name", read_only=True)
    partner_display_name = serializers.CharField(
        source="partner.organization_profile.display_name",
        read_only=True,
        allow_null=True,
        default="",
    )
    instructor_name = serializers.SerializerMethodField()

    class Meta:
        model = BibleCourse
        fields = [
            "id",
            "partner",
            "partner_name",
            "partner_display_name",
            "instructor_name",
            "title",
            "subtitle",
            "description",
            "cover_image",
            "level",
            "duration_minutes",
            "is_bible_course",
            "is_free",
            "is_public",
            "price_amount",
            "price_currency",
            "published",
            "created_at",
            "modules",
            "lessons",
            "prerequisites",
            "reaction_count",
            "comment_count",
            "viewer_reaction",
            "is_enrolled",
            "enrollment_id",
            "enrollment_status",
            "enrollment_progress",
            "enrollment_paid",
        ]

    def get_reaction_count(self, obj):
        return getattr(obj, "reactions", None).count() if hasattr(obj, "reactions") else 0

    def get_comment_count(self, obj):
        return getattr(obj, "comments", None).count() if hasattr(obj, "comments") else 0

    def get_viewer_reaction(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or user.is_anonymous:
            return None
        reaction = BibleCourseReaction.objects.filter(course=obj, user=user).first()
        return reaction.emoji if reaction else None

    def get_instructor_name(self, obj):
        partner = getattr(obj, "partner", None)
        owner = getattr(partner, "owner", None) if partner else None
        if owner:
            return owner.display_name or owner.email or owner.username or owner.phone or "Instructor"
        return "David Williams"

    def get_is_enrolled(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or user.is_anonymous:
            return False
        return obj.enrollments.filter(user=user).exists()

    def _get_enrollment(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or user.is_anonymous:
            return None
        return obj.enrollments.filter(user=user).first()

    def get_enrollment_id(self, obj):
        enrollment = self._get_enrollment(obj)
        return enrollment.id if enrollment else None

    def get_enrollment_status(self, obj):
        enrollment = self._get_enrollment(obj)
        return enrollment.status if enrollment else None

    def get_enrollment_progress(self, obj):
        enrollment = self._get_enrollment(obj)
        return enrollment.progress_percent if enrollment else 0

    def get_enrollment_paid(self, obj):
        enrollment = self._get_enrollment(obj)
        return enrollment.is_paid if enrollment else False


class BibleCourseEnrollmentSerializer(serializers.ModelSerializer):
    course_detail = BibleCourseSerializer(source="course", read_only=True)

    class Meta:
        model = BibleCourseEnrollment
        fields = ["id", "course", "course_detail", "status", "progress_percent", "is_paid", "created_at"]


class BibleLessonProgressSerializer(serializers.ModelSerializer):
    class Meta:
        model = BibleLessonProgress
        fields = ["id", "lesson", "completed", "last_position_ms", "updated_at"]


class BibleCourseReactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = BibleCourseReaction
        fields = ["id", "emoji", "created_at"]


class BibleLessonReactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = BibleLessonReaction
        fields = ["id", "emoji", "created_at"]


class BibleCourseCommentSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.display_name", read_only=True)
    text = serializers.CharField(write_only=True, required=False)
    course = serializers.PrimaryKeyRelatedField(queryset=BibleCourse.objects.all(), write_only=True, required=False)

    class Meta:
        model = BibleCourseComment
        fields = ["id", "user", "user_name", "content", "created_at", "course", "text"]
        read_only_fields = ["user", "created_at", "user_name"]

    def validate(self, attrs):
        if not attrs.get("content") and attrs.get("text"):
            attrs["content"] = attrs.pop("text")
        return attrs


class BibleLessonCommentSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.display_name", read_only=True)

    class Meta:
        model = BibleLessonComment
        fields = ["id", "user", "user_name", "content", "created_at", "lesson"]
        read_only_fields = ["user", "created_at", "user_name"]


class BibleCourseShareSerializer(serializers.ModelSerializer):
    class Meta:
        model = BibleCourseShare
        fields = ["id", "created_at"]
