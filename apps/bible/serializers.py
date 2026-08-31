from rest_framework import serializers
from apps.media.safety import validate_attachment_metadata_for_safe_messaging

from .models import (
    BibleTranslation,
    BibleBook,
    BibleChapter,
    BibleVerse,
    BibleAudio,
    BibleAudioSegment,
    BibleTranslationMetadata,
    DailyDevotional,
    BibleDailyPassage,
    BibleMeditationPost,
    BiblePrayerMonth,
    BiblePrayerDay,
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
    BibleReadingPlanEvent,
    BibleBookmark,
    BibleNote,
    BibleHighlight,
    MemoryVerse,
    BiblePreference,
    BibleCrossReference,
    BibleContentAuditLog,
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
    BibleCourseTrackAssignment,
    BibleCourseTrackProgress,
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
    metadata_status = serializers.CharField(source="metadata.validation_status", read_only=True)
    copyright_status = serializers.CharField(source="metadata.copyright_status", read_only=True)
    is_public = serializers.BooleanField(source="metadata.is_public", read_only=True)
    is_licensed = serializers.BooleanField(source="metadata.is_licensed", read_only=True)

    class Meta:
        model = BibleTranslation
        fields = [
            "id",
            "code",
            "name",
            "language",
            "sort_order",
            "is_active",
            "metadata_status",
            "copyright_status",
            "is_public",
            "is_licensed",
        ]


class BibleTranslationMetadataSerializer(serializers.ModelSerializer):
    translation_name = serializers.CharField(source="translation.name", read_only=True)
    can_be_public = serializers.BooleanField(read_only=True)

    class Meta:
        model = BibleTranslationMetadata
        fields = [
            "id",
            "translation",
            "translation_name",
            "code",
            "language",
            "display_language",
            "abbreviation",
            "full_name",
            "source_path",
            "source_filename",
            "source_hash",
            "copyright_status",
            "license_notes",
            "rights_holder",
            "license_review_status",
            "license_reviewed_by",
            "license_reviewed_at",
            "is_licensed",
            "is_public",
            "import_enabled",
            "validation_status",
            "validation_errors",
            "book_count",
            "chapter_count",
            "verse_count",
            "last_scanned_at",
            "last_imported_at",
            "created_at",
            "updated_at",
            "can_be_public",
        ]
        read_only_fields = [
            "translation",
            "translation_name",
            "code",
            "language",
            "source_path",
            "source_filename",
            "source_hash",
            "license_reviewed_by",
            "license_reviewed_at",
            "validation_status",
            "validation_errors",
            "book_count",
            "chapter_count",
            "verse_count",
            "last_scanned_at",
            "last_imported_at",
            "created_at",
            "updated_at",
            "can_be_public",
        ]


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


class BibleDailyPassageSerializer(serializers.ModelSerializer):
    partner_name = serializers.CharField(source="partner.name", read_only=True)
    translation_detail = BibleTranslationSerializer(source="translation", read_only=True)

    class Meta:
        model = BibleDailyPassage
        fields = [
            "id",
            "partner",
            "partner_name",
            "date",
            "language",
            "translation",
            "translation_detail",
            "title",
            "passage_ref",
            "scripture_refs",
            "exhortation",
            "prayer_text",
            "status",
            "published_at",
            "created_by",
            "reviewed_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["partner", "created_by", "reviewed_by", "created_at", "updated_at"]


class BibleMeditationPostSerializer(serializers.ModelSerializer):
    partner_name = serializers.CharField(source="partner.name", read_only=True)

    class Meta:
        model = BibleMeditationPost
        fields = [
            "id",
            "partner",
            "partner_name",
            "content_type",
            "title",
            "body",
            "video_url",
            "thumbnail_url",
            "scripture_refs",
            "tags",
            "language",
            "status",
            "published_at",
            "created_by",
            "reviewed_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["partner", "created_by", "reviewed_by", "created_at", "updated_at"]

    def validate(self, attrs):
        content_type = attrs.get("content_type") or getattr(self.instance, "content_type", "message")
        if content_type == "video" and not attrs.get("video_url") and not getattr(self.instance, "video_url", ""):
            raise serializers.ValidationError({"video_url": "Video meditations require a video URL."})
        if content_type == "message" and not attrs.get("body") and not getattr(self.instance, "body", ""):
            raise serializers.ValidationError({"body": "Message meditations require body text."})
        return attrs


class BiblePrayerDaySerializer(serializers.ModelSerializer):
    class Meta:
        model = BiblePrayerDay
        fields = ["id", "prayer_month", "day", "prayer_points", "exhortation", "scripture_refs"]


class BiblePrayerMonthSerializer(serializers.ModelSerializer):
    partner_name = serializers.CharField(source="partner.name", read_only=True)
    days = BiblePrayerDaySerializer(many=True, read_only=True)

    class Meta:
        model = BiblePrayerMonth
        fields = [
            "id",
            "partner",
            "partner_name",
            "year",
            "month",
            "language",
            "title",
            "theme",
            "status",
            "published_at",
            "created_by",
            "reviewed_by",
            "created_at",
            "updated_at",
            "days",
        ]
        read_only_fields = ["partner", "created_by", "reviewed_by", "created_at", "updated_at"]


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


class BibleReadingPlanEventSerializer(serializers.ModelSerializer):
    verse_refs = serializers.SerializerMethodField()
    chapter_refs = serializers.SerializerMethodField()

    class Meta:
        model = BibleReadingPlanEvent
        fields = [
            "id",
            "translation",
            "passage_ref",
            "chapters",
            "chapter_refs",
            "verses",
            "verse_refs",
            "start_at",
            "end_at",
            "recurrence",
            "reminder_offsets",
            "reminder_channels",
            "status",
            "source",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def validate(self, attrs):
        passage_ref = attrs.get("passage_ref") or getattr(self.instance, "passage_ref", "")
        if not passage_ref:
            raise serializers.ValidationError({"passage_ref": "A selected Bible passage is required."})
        return attrs

    def get_verse_refs(self, obj):
        return [
            f"{verse.chapter.book.name} {verse.chapter.number}:{verse.number}"
            for verse in obj.verses.select_related("chapter", "chapter__book").all()
        ]

    def get_chapter_refs(self, obj):
        return [
            f"{chapter.book.name} {chapter.number}"
            for chapter in obj.chapters.select_related("book").all()
        ]


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
    verse_text = serializers.CharField(source="verse.text", read_only=True)
    verse_ref = serializers.SerializerMethodField()

    class Meta:
        model = BibleNote
        fields = ["id", "verse", "verse_text", "verse_ref", "text", "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at"]

    def get_verse_ref(self, obj):
        return f"{obj.verse.chapter.book.name} {obj.verse.chapter.number}:{obj.verse.number}"


class BibleHighlightSerializer(serializers.ModelSerializer):
    verse_text = serializers.CharField(source="verse.text", read_only=True)
    verse_ref = serializers.SerializerMethodField()
    translation = serializers.IntegerField(source="verse.translation_id", read_only=True)

    class Meta:
        model = BibleHighlight
        fields = ["id", "verse", "verse_text", "verse_ref", "translation", "color", "created_at"]
        read_only_fields = ["created_at"]

    def get_verse_ref(self, obj):
        return f"{obj.verse.chapter.book.name} {obj.verse.chapter.number}:{obj.verse.number}"


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


class BibleContentAuditLogSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.display_name", read_only=True)
    partner_name = serializers.CharField(source="partner.name", read_only=True)

    class Meta:
        model = BibleContentAuditLog
        fields = ["id", "partner", "partner_name", "user", "user_name", "action", "target_type", "target_id", "metadata", "created_at"]
        read_only_fields = fields


class BibleCourseModuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = BibleCourseModule
        fields = ["id", "course", "title", "summary", "order"]


class BibleCourseTrackItemSerializer(serializers.ModelSerializer):
    course_title = serializers.CharField(source="course.title", read_only=True)
    course_cover_image = serializers.CharField(source="course.cover_image", read_only=True)
    course_duration_minutes = serializers.IntegerField(source="course.duration_minutes", read_only=True)
    course_progress_percent = serializers.SerializerMethodField()
    course_enrollment_status = serializers.SerializerMethodField()

    class Meta:
        model = BibleCourseTrackItem
        fields = [
            "id",
            "track",
            "course",
            "order",
            "course_title",
            "course_cover_image",
            "course_duration_minutes",
            "course_progress_percent",
            "course_enrollment_status",
        ]

    def _viewer_enrollment(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or user.is_anonymous:
            return None
        return BibleCourseEnrollment.objects.filter(user=user, course_id=obj.course_id).first()

    def get_course_progress_percent(self, obj):
        enrollment = self._viewer_enrollment(obj)
        return enrollment.progress_percent if enrollment else 0

    def get_course_enrollment_status(self, obj):
        enrollment = self._viewer_enrollment(obj)
        return enrollment.status if enrollment else None


class BibleCourseTrackAssignmentSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.display_name", read_only=True)
    assigned_by_name = serializers.CharField(source="assigned_by.display_name", read_only=True, default="")
    department_name = serializers.CharField(source="department.name", read_only=True, allow_null=True, default="")

    class Meta:
        model = BibleCourseTrackAssignment
        fields = [
            "id",
            "track",
            "user",
            "user_name",
            "department",
            "department_name",
            "assigned_by",
            "assigned_by_name",
            "assigned_at",
        ]
        read_only_fields = ["assigned_by", "assigned_at"]


class BibleCourseTrackProgressSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.display_name", read_only=True)

    class Meta:
        model = BibleCourseTrackProgress
        fields = ["id", "track", "user", "user_name", "status", "progress_percent", "completed_at", "updated_at"]
        read_only_fields = fields


class BibleCourseTrackSerializer(serializers.ModelSerializer):
    items = BibleCourseTrackItemSerializer(many=True, read_only=True)
    item_count = serializers.SerializerMethodField()
    assignment_count = serializers.SerializerMethodField()
    is_assigned = serializers.SerializerMethodField()
    my_status = serializers.SerializerMethodField()
    my_progress_percent = serializers.SerializerMethodField()

    class Meta:
        model = BibleCourseTrack
        fields = [
            "id",
            "partner",
            "title",
            "description",
            "is_published",
            "created_at",
            "items",
            "item_count",
            "assignment_count",
            "is_assigned",
            "my_status",
            "my_progress_percent",
        ]

    def get_item_count(self, obj):
        return obj.items.count()

    def get_assignment_count(self, obj):
        return obj.assignments.count()

    def _my_progress(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or user.is_anonymous:
            return None
        return obj.progress_records.filter(user=user).first()

    def get_is_assigned(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or user.is_anonymous:
            return False
        return obj.assignments.filter(user=user).exists()

    def get_my_status(self, obj):
        progress = self._my_progress(obj)
        return progress.status if progress else "not_started"

    def get_my_progress_percent(self, obj):
        progress = self._my_progress(obj)
        return progress.progress_percent if progress else 0


class BibleCoursePrerequisiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = BibleCoursePrerequisite
        fields = ["id", "course", "required_course", "required_percent"]


PRIVATE_ATTACHMENT_KEYS = {
    "absolute_path",
    "bucket",
    "file_path",
    "local_path",
    "path",
    "private_url",
    "signed_url",
    "storage_key",
    "storage_path",
    "token",
}


def _public_attachment_payload(value):
    if not isinstance(value, dict):
        return value
    return {
        str(key): _public_attachment_payload(item)
        for key, item in value.items()
        if str(key).lower() not in PRIVATE_ATTACHMENT_KEYS
    }


def _public_attachment_list(value):
    if not isinstance(value, list):
        return value
    return [_public_attachment_payload(item) for item in value]


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

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["attachments"] = _public_attachment_list(data.get("attachments"))
        return data

    def validate(self, attrs):
        validate_attachment_metadata_for_safe_messaging(attrs.get("attachments"))
        return super().validate(attrs)


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

    def validate(self, attrs):
        validate_attachment_metadata_for_safe_messaging(attrs.get("attachments"))
        return super().validate(attrs)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["attachments"] = _public_attachment_list(data.get("attachments"))
        return data


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
