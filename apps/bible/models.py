from django.conf import settings
from django.db import models
from apps.partners.models import Partner


class BibleTranslation(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    language = models.CharField(max_length=20, default="en")
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self) -> str:
        return self.name


class BibleBook(models.Model):
    TESTAMENT_CHOICES = (
        ("OT", "Old Testament"),
        ("NT", "New Testament"),
    )

    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=100)
    testament = models.CharField(max_length=2, choices=TESTAMENT_CHOICES)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self) -> str:
        return self.name


class BibleChapter(models.Model):
    book = models.ForeignKey(BibleBook, on_delete=models.CASCADE, related_name="chapters")
    number = models.PositiveIntegerField()

    class Meta:
        unique_together = ("book", "number")
        ordering = ["book__order", "number"]

    def __str__(self) -> str:
        return f"{self.book.name} {self.number}"


class BibleVerse(models.Model):
    translation = models.ForeignKey(BibleTranslation, on_delete=models.CASCADE, related_name="verses")
    chapter = models.ForeignKey(BibleChapter, on_delete=models.CASCADE, related_name="verses")
    number = models.PositiveIntegerField()
    text = models.TextField()

    class Meta:
        unique_together = ("translation", "chapter", "number")
        ordering = ["chapter__book__order", "chapter__number", "number"]

    def __str__(self) -> str:
        return f"{self.chapter.book.name} {self.chapter.number}:{self.number}"


class BibleAudio(models.Model):
    translation = models.ForeignKey(BibleTranslation, on_delete=models.CASCADE, related_name="audio")
    chapter = models.ForeignKey(BibleChapter, on_delete=models.CASCADE, related_name="audio")
    audio_file = models.FileField(upload_to="bible/audio/", null=True, blank=True)
    duration_ms = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("translation", "chapter")

    def __str__(self) -> str:
        return f"{self.translation.code} {self.chapter}"


class BibleAudioSegment(models.Model):
    audio = models.ForeignKey(BibleAudio, on_delete=models.CASCADE, related_name="segments")
    verse = models.ForeignKey(BibleVerse, on_delete=models.CASCADE, related_name="segments")
    start_ms = models.PositiveIntegerField(default=0)
    end_ms = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["start_ms"]


class DailyDevotional(models.Model):
    date = models.DateField(unique=True)
    translation = models.ForeignKey(
        BibleTranslation, on_delete=models.SET_NULL, null=True, blank=True, related_name="devotionals"
    )
    passage_ref = models.CharField(max_length=120)
    title = models.CharField(max_length=200)
    content = models.TextField()
    prayer_text = models.TextField()

    class Meta:
        ordering = ["-date"]

    def __str__(self) -> str:
        return f"{self.date} - {self.title}"


class PrayerRequest(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="prayer_requests")
    title = models.CharField(max_length=200)
    content = models.TextField()
    answered = models.BooleanField(default=False)
    is_public = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class MeditationTopic(models.Model):
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]


class MeditationSchedule(models.Model):
    CADENCE_CHOICES = (
        ("daily", "Daily"),
        ("weekly", "Weekly"),
        ("custom", "Custom"),
    )

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="meditation_schedules")
    topic = models.ForeignKey(MeditationTopic, on_delete=models.SET_NULL, null=True, blank=True)
    topic_label = models.CharField(max_length=120, blank=True)
    cadence = models.CharField(max_length=20, choices=CADENCE_CHOICES, default="daily")
    time_of_day = models.TimeField(null=True, blank=True)
    timezone = models.CharField(max_length=64, default="UTC")
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)


class MeditationEntry(models.Model):
    SOURCE_CHOICES = (
        ("ai", "AI"),
        ("manual", "Manual"),
    )

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="meditation_entries")
    topic = models.ForeignKey(MeditationTopic, on_delete=models.SET_NULL, null=True, blank=True)
    date = models.DateField()
    title = models.CharField(max_length=200)
    content = models.TextField()
    prayer_text = models.TextField(blank=True)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="ai")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]


class BibleChatSession(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bible_sessions")
    topic = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class BibleChatMessage(models.Model):
    ROLE_CHOICES = (
        ("user", "User"),
        ("assistant", "Assistant"),
    )

    session = models.ForeignKey(BibleChatSession, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


class ReadingPlan(models.Model):
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    is_system = models.BooleanField(default=True)
    days_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["name"]


class ReadingPlanItem(models.Model):
    plan = models.ForeignKey(ReadingPlan, on_delete=models.CASCADE, related_name="items")
    day_index = models.PositiveIntegerField()
    passage_ref = models.CharField(max_length=120)

    class Meta:
        unique_together = ("plan", "day_index")
        ordering = ["day_index"]


class ReadingPlanEnrollment(models.Model):
    STATUS_CHOICES = (
        ("active", "Active"),
        ("paused", "Paused"),
        ("completed", "Completed"),
    )

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reading_plans")
    plan = models.ForeignKey(ReadingPlan, on_delete=models.CASCADE)
    start_date = models.DateField()
    current_day = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")


class ReadingHistory(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reading_history")
    translation = models.ForeignKey(BibleTranslation, on_delete=models.SET_NULL, null=True, blank=True)
    chapter = models.ForeignKey(BibleChapter, on_delete=models.CASCADE)
    last_verse = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]


class BibleBookmark(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bible_bookmarks")
    verse = models.ForeignKey(BibleVerse, on_delete=models.CASCADE, related_name="bookmarked_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "verse")


class BibleNote(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bible_notes")
    verse = models.ForeignKey(BibleVerse, on_delete=models.CASCADE, related_name="notes")
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class BibleHighlight(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bible_highlights")
    verse = models.ForeignKey(BibleVerse, on_delete=models.CASCADE, related_name="highlights")
    color = models.CharField(max_length=32, default="#FACC15")
    created_at = models.DateTimeField(auto_now_add=True)


class MemoryVerse(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memory_verses")
    verse = models.ForeignKey(BibleVerse, on_delete=models.CASCADE)
    next_review_date = models.DateField(null=True, blank=True)
    streak = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)


class BiblePreference(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bible_preferences")
    default_translation = models.ForeignKey(BibleTranslation, on_delete=models.SET_NULL, null=True, blank=True)
    font_size = models.PositiveIntegerField(default=16)
    audio_speed = models.DecimalField(max_digits=4, decimal_places=2, default=1.0)
    enable_audio_sync = models.BooleanField(default=True)
    enable_parallel_view = models.BooleanField(default=False)
    enable_daily_reminders = models.BooleanField(default=True)
    enable_offline_cache = models.BooleanField(default=False)


class BibleCrossReference(models.Model):
    verse = models.ForeignKey(BibleVerse, on_delete=models.CASCADE, related_name="cross_refs")
    related_verse = models.ForeignKey(BibleVerse, on_delete=models.CASCADE, related_name="reverse_cross_refs")
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        unique_together = ("verse", "related_verse")


class BibleCourse(models.Model):
    partner = models.ForeignKey(
        "partners.Partner",
        on_delete=models.CASCADE,
        related_name="bible_courses",
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=200)
    subtitle = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    cover_image = models.URLField(blank=True)
    level = models.CharField(max_length=50, default="beginner")
    duration_minutes = models.PositiveIntegerField(default=0)
    is_bible_course = models.BooleanField(default=False)
    is_free = models.BooleanField(default=True)
    is_public = models.BooleanField(default=False)
    price_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    price_currency = models.CharField(max_length=10, default="USD")
    published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title


class BibleCourseModule(models.Model):
    course = models.ForeignKey(BibleCourse, on_delete=models.CASCADE, related_name="modules")
    title = models.CharField(max_length=200)
    summary = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["order"]
        unique_together = ("course", "order")


class BibleLesson(models.Model):
    course = models.ForeignKey(BibleCourse, on_delete=models.CASCADE, related_name="lessons")
    module = models.ForeignKey(BibleCourseModule, on_delete=models.SET_NULL, null=True, blank=True, related_name="lessons")
    title = models.CharField(max_length=200)
    summary = models.TextField(blank=True)
    content = models.TextField(blank=True)
    transcript = models.TextField(blank=True)
    captions_url = models.URLField(blank=True)
    language = models.CharField(max_length=20, default="en")
    order = models.PositiveIntegerField(default=1)
    duration_minutes = models.PositiveIntegerField(default=5)
    video_url = models.URLField(blank=True)
    audio_url = models.URLField(blank=True)
    attachments = models.JSONField(default=list, blank=True)
    is_free = models.BooleanField(default=True)

    class Meta:
        ordering = ["order"]
        unique_together = ("course", "order")


class BibleCourseEnrollment(models.Model):
    STATUS_CHOICES = (
        ("active", "Active"),
        ("completed", "Completed"),
        ("paused", "Paused"),
    )

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bible_course_enrollments")
    course = models.ForeignKey(BibleCourse, on_delete=models.CASCADE, related_name="enrollments")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    progress_percent = models.PositiveIntegerField(default=0)
    is_paid = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "course")


class BibleLessonProgress(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bible_lesson_progress")
    lesson = models.ForeignKey(BibleLesson, on_delete=models.CASCADE, related_name="progress")
    completed = models.BooleanField(default=False)
    last_position_ms = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "lesson")


class BibleLessonReaction(models.Model):
    lesson = models.ForeignKey(BibleLesson, on_delete=models.CASCADE, related_name="reactions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="lesson_reactions")
    emoji = models.CharField(max_length=16, default="❤️")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("lesson", "user")


class BibleLessonComment(models.Model):
    lesson = models.ForeignKey(BibleLesson, on_delete=models.CASCADE, related_name="comments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="lesson_comments")
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class BibleCourseReaction(models.Model):
    course = models.ForeignKey(BibleCourse, on_delete=models.CASCADE, related_name="reactions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="course_reactions")
    emoji = models.CharField(max_length=16, default="❤️")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("course", "user")


class BibleCourseComment(models.Model):
    course = models.ForeignKey(BibleCourse, on_delete=models.CASCADE, related_name="comments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="course_comments")
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class BibleCourseShare(models.Model):
    course = models.ForeignKey(BibleCourse, on_delete=models.CASCADE, related_name="shares")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="course_shares")
    created_at = models.DateTimeField(auto_now_add=True)


class BibleCourseTrack(models.Model):
    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name="course_tracks", null=True, blank=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    is_published = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class BibleCourseTrackItem(models.Model):
    track = models.ForeignKey(BibleCourseTrack, on_delete=models.CASCADE, related_name="items")
    course = models.ForeignKey(BibleCourse, on_delete=models.CASCADE, related_name="track_items")
    order = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["order"]
        unique_together = ("track", "order")


class BibleCoursePrerequisite(models.Model):
    course = models.ForeignKey(BibleCourse, on_delete=models.CASCADE, related_name="prerequisites")
    required_course = models.ForeignKey(BibleCourse, on_delete=models.CASCADE, related_name="required_for")
    required_percent = models.PositiveIntegerField(default=100)

    class Meta:
        unique_together = ("course", "required_course")


class BibleQuiz(models.Model):
    QUESTION_KINDS = (
        ("single_choice", "Single choice"),
        ("multiple_choice", "Multiple choice"),
        ("true_false", "True/False"),
        ("short_answer", "Short answer"),
    )

    course = models.ForeignKey(BibleCourse, on_delete=models.CASCADE, related_name="quizzes")
    lesson = models.ForeignKey(BibleLesson, on_delete=models.SET_NULL, null=True, blank=True, related_name="quizzes")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    pass_score = models.PositiveIntegerField(default=70)
    time_limit_minutes = models.PositiveIntegerField(default=0)
    attempts_allowed = models.PositiveIntegerField(default=3)
    is_exam = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["order"]


class BibleQuizQuestion(models.Model):
    quiz = models.ForeignKey(BibleQuiz, on_delete=models.CASCADE, related_name="questions")
    prompt = models.TextField()
    kind = models.CharField(max_length=20, choices=BibleQuiz.QUESTION_KINDS, default="single_choice")
    points = models.PositiveIntegerField(default=1)
    order = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["order"]


class BibleQuizChoice(models.Model):
    question = models.ForeignKey(BibleQuizQuestion, on_delete=models.CASCADE, related_name="choices")
    text = models.CharField(max_length=300)
    is_correct = models.BooleanField(default=False)


class BibleQuizAttempt(models.Model):
    quiz = models.ForeignKey(BibleQuiz, on_delete=models.CASCADE, related_name="attempts")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="quiz_attempts")
    score = models.PositiveIntegerField(default=0)
    max_score = models.PositiveIntegerField(default=0)
    passed = models.BooleanField(default=False)
    answers = models.JSONField(default=list, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]


class BibleAssignment(models.Model):
    course = models.ForeignKey(BibleCourse, on_delete=models.CASCADE, related_name="assignments")
    lesson = models.ForeignKey(BibleLesson, on_delete=models.SET_NULL, null=True, blank=True, related_name="assignments")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    max_points = models.PositiveIntegerField(default=100)
    rubric = models.JSONField(default=list, blank=True)
    order = models.PositiveIntegerField(default=1)
    is_required = models.BooleanField(default=True)

    class Meta:
        ordering = ["order"]


class BibleAssignmentSubmission(models.Model):
    STATUS_CHOICES = (
        ("submitted", "Submitted"),
        ("graded", "Graded"),
    )
    PLAGIARISM_STATUS = (
        ("pending", "Pending"),
        ("clean", "Clean"),
        ("flagged", "Flagged"),
    )
    assignment = models.ForeignKey(BibleAssignment, on_delete=models.CASCADE, related_name="submissions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="assignment_submissions")
    content_text = models.TextField(blank=True)
    attachments = models.JSONField(default=list, blank=True)
    score = models.PositiveIntegerField(default=0)
    feedback = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="submitted")
    plagiarism_score = models.PositiveIntegerField(default=0)
    plagiarism_status = models.CharField(max_length=20, choices=PLAGIARISM_STATUS, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)
    graded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = ("assignment", "user")


class BiblePeerReview(models.Model):
    submission = models.ForeignKey(BibleAssignmentSubmission, on_delete=models.CASCADE, related_name="peer_reviews")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="peer_reviews")
    score = models.PositiveIntegerField(default=0)
    feedback = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("submission", "reviewer")


class BibleCourseForum(models.Model):
    course = models.OneToOneField(BibleCourse, on_delete=models.CASCADE, related_name="forum")
    is_locked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class BibleForumThread(models.Model):
    forum = models.ForeignKey(BibleCourseForum, on_delete=models.CASCADE, related_name="threads")
    title = models.CharField(max_length=200)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="forum_threads")
    is_pinned = models.BooleanField(default=False)
    is_locked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_pinned", "-created_at"]


class BibleForumPost(models.Model):
    thread = models.ForeignKey(BibleForumThread, on_delete=models.CASCADE, related_name="posts")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="forum_posts")
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


class BibleMentorAssignment(models.Model):
    course = models.ForeignKey(BibleCourse, on_delete=models.CASCADE, related_name="mentors")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mentor_courses")
    role = models.CharField(max_length=50, default="mentor")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("course", "user")


class BibleLiveSession(models.Model):
    course = models.ForeignKey(BibleCourse, on_delete=models.CASCADE, related_name="live_sessions")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField(null=True, blank=True)
    meeting_url = models.URLField(blank=True)
    host = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="hosted_live_sessions")
    is_recorded = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_at"]


class BibleLiveAttendance(models.Model):
    session = models.ForeignKey(BibleLiveSession, on_delete=models.CASCADE, related_name="attendances")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="live_attendances")
    status = models.CharField(max_length=20, default="registered")
    joined_at = models.DateTimeField(null=True, blank=True)
    left_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("session", "user")


class BibleLiveRecording(models.Model):
    session = models.ForeignKey(BibleLiveSession, on_delete=models.CASCADE, related_name="recordings")
    title = models.CharField(max_length=200)
    media_url = models.URLField()
    duration_minutes = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class BibleCourseBundle(models.Model):
    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name="course_bundles")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    price_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    price_currency = models.CharField(max_length=10, default="USD")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class BibleCourseBundleItem(models.Model):
    bundle = models.ForeignKey(BibleCourseBundle, on_delete=models.CASCADE, related_name="items")
    course = models.ForeignKey(BibleCourse, on_delete=models.CASCADE, related_name="bundle_items")
    order = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["order"]
        unique_together = ("bundle", "course")


class BibleCourseCoupon(models.Model):
    code = models.CharField(max_length=32, unique=True)
    course = models.ForeignKey(BibleCourse, on_delete=models.CASCADE, related_name="coupons", null=True, blank=True)
    bundle = models.ForeignKey(BibleCourseBundle, on_delete=models.CASCADE, related_name="coupons", null=True, blank=True)
    percent_off = models.PositiveIntegerField(default=0)
    amount_off = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    max_redemptions = models.PositiveIntegerField(default=0)
    redeemed_count = models.PositiveIntegerField(default=0)
    valid_until = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class BibleEnterpriseSeatPool(models.Model):
    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name="seat_pools")
    course = models.ForeignKey(BibleCourse, on_delete=models.CASCADE, related_name="seat_pools", null=True, blank=True)
    seats_total = models.PositiveIntegerField(default=0)
    seats_used = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class BibleRefundRequest(models.Model):
    STATUS = (
        ("requested", "Requested"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    )
    enrollment = models.ForeignKey(BibleCourseEnrollment, on_delete=models.CASCADE, related_name="refund_requests")
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS, default="requested")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class BibleCourseCredential(models.Model):
    enrollment = models.OneToOneField(BibleCourseEnrollment, on_delete=models.CASCADE, related_name="credential")
    badge_name = models.CharField(max_length=200, default="Course Certificate")
    share_token = models.CharField(max_length=64, unique=True)
    issued_at = models.DateTimeField(auto_now_add=True)
