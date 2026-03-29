import uuid
from datetime import timedelta

from django.db import models
from django.utils import timezone

from apps.accounts.models import Profile, User
from apps.chat.models import Conversation
from apps.channels.models import Channel
from apps.communities.models import Community
from apps.partners.models import Partner


class BroadcastSourceType(models.TextChoices):
    COMMUNITY_POST = "community_post", "Community Post"
    PARTNER_POST = "partner_post", "Partner Post"
    CHANNEL_MESSAGE = "channel_message", "Channel Message"
    BROADCAST_FEED_ENTRY = "broadcast_feed_entry", "Broadcast Feed Entry"
    MARKET_PRODUCT = "market_product", "Market Product"
    MARKET_SERVICE = "market_service", "Market Service"
    EDUCATION_COURSE = "education_course", "Education Course"
    EDUCATION_PROFILE = "education_profile", "Education Profile"


def _default_expires_at():
    return timezone.now() + timedelta(days=10)


class BroadcastItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_type = models.CharField(max_length=32, choices=BroadcastSourceType.choices, db_index=True)
    source_id = models.CharField(max_length=128, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    conversation_id = models.UUIDField(null=True, blank=True, db_index=True)
    channel = models.ForeignKey(Channel, null=True, blank=True, on_delete=models.SET_NULL, related_name="broadcast_items")
    broadcasted_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="broadcasts")
    broadcasted_at = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField(default=_default_expires_at, db_index=True)
    comment_conversation = models.ForeignKey(
        Conversation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="broadcast_comment_threads",
    )
    is_deleted = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcast_item"
        unique_together = [("source_type", "source_id")]
        indexes = [
            models.Index(fields=["source_type", "source_id"]),
            models.Index(fields=["expires_at"]),
        ]


class EducationProfileType(models.TextChoices):
    COURSE = "course", "Course"
    DEGREE = "degree", "Degree Program"
    CAMP = "camp", "Camp"
    VOCATIONAL = "vocational", "Vocational Training"
    WORKSHOP = "workshop", "Workshop"
    MISC = "misc", "Other"


class BroadcastFeedProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile = models.OneToOneField(Profile, on_delete=models.CASCADE, related_name="broadcast_feed_profile")
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcast_feed_profile"


class BroadcastHealthProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile = models.OneToOneField(Profile, on_delete=models.CASCADE, related_name="broadcast_health_profile")
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcast_health_profile"


class BroadcastHealthInstitution(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    health_profile = models.ForeignKey(
        BroadcastHealthProfile,
        on_delete=models.CASCADE,
        related_name="institution_rows",
    )
    institution_uid = models.CharField(max_length=128, db_index=True)
    institution_type = models.CharField(max_length=64, default="clinic")
    name = models.CharField(max_length=255)
    owner_user = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="broadcast_health_institutions_owned",
    )
    owner_name = models.CharField(max_length=255, blank=True, default="")
    owner_phone = models.CharField(max_length=64, blank=True, default="")
    owner_email = models.CharField(max_length=255, blank=True, default="")
    members_target_count = models.PositiveIntegerField(default=1)
    membership_open = models.BooleanField(default=False)
    membership_discount_pct = models.PositiveIntegerField(default=10)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcast_health_institution"
        constraints = [
            models.UniqueConstraint(
                fields=["health_profile", "institution_uid"],
                name="broadcast_health_institution_unique_profile_uid",
            ),
        ]
        indexes = [
            models.Index(fields=["health_profile", "institution_uid"]),
            models.Index(fields=["owner_user"]),
        ]


class BroadcastHealthInstitutionMember(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        BroadcastHealthInstitution,
        on_delete=models.CASCADE,
        related_name="member_rows",
    )
    member_uid = models.CharField(max_length=128, db_index=True)
    name = models.CharField(max_length=255)
    role = models.CharField(max_length=64, default="staff")
    phone = models.CharField(max_length=64, blank=True, default="")
    email = models.CharField(max_length=255, blank=True, default="")
    user = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="broadcast_health_memberships",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcast_health_institution_member"
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "member_uid"],
                name="broadcast_health_member_unique_institution_uid",
            ),
        ]
        indexes = [
            models.Index(fields=["institution", "member_uid"]),
            models.Index(fields=["user"]),
        ]


class BroadcastHealthInstitutionService(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        BroadcastHealthInstitution,
        on_delete=models.CASCADE,
        related_name="service_rows",
    )
    service_uid = models.CharField(max_length=128, db_index=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    active = models.BooleanField(default=True)
    base_price_cents = models.PositiveIntegerField(null=True, blank=True)
    medium_ids = models.JSONField(default=list, blank=True)
    medium_names = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcast_health_institution_service"
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "service_uid"],
                name="broadcast_health_service_unique_institution_uid",
            ),
        ]
        indexes = [
            models.Index(fields=["institution", "service_uid"]),
        ]


class BroadcastMarketProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile = models.OneToOneField(Profile, on_delete=models.CASCADE, related_name="broadcast_market_profile")
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcast_market_profile"


class Medium(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True, default="")
    system_flag = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mediums"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Service(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=180)
    description = models.TextField(blank=True, default="")
    is_default = models.BooleanField(default=False, db_index=True)
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="broadcast_health_services",
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "services"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["name", "created_by"], name="service_name_per_creator_uniq")
        ]

    def __str__(self):
        return self.name


class ServiceMediumMap(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name="medium_links")
    medium = models.ForeignKey(Medium, on_delete=models.CASCADE, related_name="service_links")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "service_medium_map"
        constraints = [
            models.UniqueConstraint(fields=["service", "medium"], name="service_medium_unique")
        ]


class EducationProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="education_profiles")
    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name="broadcast_education_profiles",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    profile_type = models.CharField(max_length=32, choices=EducationProfileType.choices, default=EducationProfileType.COURSE)
    metadata = models.JSONField(default=dict, blank=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_profile"
        indexes = [
            models.Index(fields=["user", "is_default"]),
            models.Index(fields=["profile", "is_default"]),
        ]

    def __str__(self):
        return f"{self.user_id}:{self.name}"


class EducationProfileCourse(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile = models.ForeignKey(EducationProfile, on_delete=models.CASCADE, related_name="courses")
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_profile_course"
        ordering = ["-created_at"]


class EducationProfileModule(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile = models.ForeignKey(EducationProfile, on_delete=models.CASCADE, related_name="modules")
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    resource_url = models.URLField(blank=True, null=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_profile_module"
        ordering = ["-created_at"]


class EducationProfileRole(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile = models.ForeignKey(EducationProfile, on_delete=models.CASCADE, related_name="roles")
    name = models.CharField(max_length=150)
    permissions = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_profile_role"
        ordering = ["-created_at"]


class EducationProfileRoleAssignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    role = models.ForeignKey(EducationProfileRole, on_delete=models.CASCADE, related_name="assignments")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="education_role_assignments")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "education_profile_role_assignment"
        unique_together = [("role", "user")]


FEATURE_DEFINITIONS = [
    {
        "slug": "broadcast_scheduling",
        "name": "Broadcast scheduling",
        "description": "Plan broadcasts ahead of time and let the system auto-launch.",
        "category": "Planning",
        "default_enabled": True,
    },
    {
        "slug": "live_ratings",
        "name": "Live ratings",
        "description": "Let viewers rate the broadcast in real time.",
        "category": "Engagement",
        "default_enabled": True,
    },
    {
        "slug": "automated_highlights",
        "name": "Automated highlights",
        "description": "AI-generated highlight reels after every broadcast.",
        "category": "Discovery",
        "default_enabled": True,
    },
    {
        "slug": "interactive_polling",
        "name": "Interactive polls",
        "description": "Embed polls that update instantly for the audience.",
        "category": "Engagement",
        "default_enabled": True,
    },
    {
        "slug": "collaborative_annotations",
        "name": "Collaborative annotations",
        "description": "Viewers and hosts can pin notes or callouts together.",
        "category": "Collaboration",
        "default_enabled": False,
    },
    {
        "slug": "layered_reactions",
        "name": "Layered reactions",
        "description": "Support stacked reactions and reaction heatmaps.",
        "category": "Engagement",
        "default_enabled": True,
    },
    {
        "slug": "monetized_pin",
        "name": "Monetized pin",
        "description": "Pin your product, link or CTA as a paid highlight.",
        "category": "Commerce",
        "default_enabled": False,
    },
    {
        "slug": "multi_host",
        "name": "Multi-host desk",
        "description": "Switch smoothly between hosts and moderators.",
        "category": "Production",
        "default_enabled": True,
    },
    {
        "slug": "audience_qna",
        "name": "Audience Q&A",
        "description": "Curate an expert Q&A queue for live broadcasts.",
        "category": "Engagement",
        "default_enabled": True,
    },
    {
        "slug": "live_translation",
        "name": "Live translation",
        "description": "Auto-translate captions for every viewer region.",
        "category": "Accessibility",
        "default_enabled": False,
    },
    {
        "slug": "custom_cta",
        "name": "Custom CTA",
        "description": "Embed programmable CTAs with tracking.",
        "category": "Commerce",
        "default_enabled": True,
    },
    {
        "slug": "private_replay",
        "name": "Private replay",
        "description": "Share replays only with approved viewers.",
        "category": "Privacy",
        "default_enabled": False,
    },
    {
        "slug": "broadcast_rankings",
        "name": "Broadcast rankings",
        "description": "Show your placement on a dynamic leaderboard.",
        "category": "Discovery",
        "default_enabled": True,
    },
    {
        "slug": "real_time_moderation",
        "name": "Real-time moderation",
        "description": "Auto-filter comments and highlight infractions.",
        "category": "Safety",
        "default_enabled": True,
    },
    {
        "slug": "adaptive_layout",
        "name": "Adaptive layout",
        "description": "Switch between cinematic, grid, and engagement layouts.",
        "category": "Production",
        "default_enabled": False,
    },
    {
        "slug": "lesson_mode",
        "name": "Lesson mode",
        "description": "Treat a broadcast as a structured lesson with swipeable modules.",
        "category": "Education",
        "default_enabled": True,
    },
    {
        "slug": "lesson_enrollment",
        "name": "Lesson enrollment automation",
        "description": "Auto-enroll viewers and track lesson-only memberships.",
        "category": "Learning",
        "default_enabled": True,
    },
    {
        "slug": "lesson_only_membership",
        "name": "Lesson-only membership",
        "description": "Grant access only to the lesson segment regardless of broader partner feeds.",
        "category": "Access",
        "default_enabled": False,
    },
    {
        "slug": "broadcast_dropkit",
        "name": "Broadcast drop kit",
        "description": "Drop digital kits or products tied to the broadcast in-view.",
        "category": "Commerce",
        "default_enabled": False,
    },
    {
        "slug": "ai_moderator_insights",
        "name": "AI moderator insights",
        "description": "Surface AI-curated moderation cues and risk signals mid-session.",
        "category": "Safety",
        "default_enabled": True,
    },
    {
        "slug": "co_host_scheduler",
        "name": "Co-host scheduler",
        "description": "Queue co-hosts and guests, then transition them live with confirmations.",
        "category": "Production",
        "default_enabled": False,
    },
    {
        "slug": "vaulted_replay",
        "name": "Vaulted replay",
        "description": "Store replays behind a vault that unlocks per membership or purchase.",
        "category": "Discovery",
        "default_enabled": False,
    },
    {
        "slug": "broadcast_storefront",
        "name": "Broadcast storefront",
        "description": "Show a curated storefront inside the broadcast feed for instant purchases.",
        "category": "Commerce",
        "default_enabled": True,
    },
    {
        "slug": "real_time_transcriptions",
        "name": "Real-time transcriptions",
        "description": "Deliver on-screen captions plus downloadable transcripts.",
        "category": "Accessibility",
        "default_enabled": True,
    },
    {
        "slug": "subscriber_only_comments",
        "name": "Subscriber-only comments",
        "description": "Restrict commenting to subscribers to keep chats premium.",
        "category": "Access",
        "default_enabled": False,
    },
    {
        "slug": "broadcast_rewards",
        "name": "Broadcast rewards",
        "description": "Issue credits or badges for attendees who complete an experience.",
        "category": "Engagement",
        "default_enabled": False,
    },
    {
        "slug": "viewer_progress_tracker",
        "name": "Viewer progress tracker",
        "description": "Track watched segments, highlight drop-in/out points, and resume.",
        "category": "Insights",
        "default_enabled": True,
    },
    {
        "slug": "auto_mixer",
        "name": "Auto mixer",
        "description": "Let the system balance audio/video feeds and add transitions.",
        "category": "Production",
        "default_enabled": False,
    },
    {
        "slug": "global_chat_rooms",
        "name": "Global chat rooms",
        "description": "Spawn regional chat rooms to pair with the broadcast view.",
        "category": "Community",
        "default_enabled": True,
    },
    {
        "slug": "audience_heatmap",
        "name": "Audience heatmap",
        "description": "Visualize who is watching and where engagement spikes happen.",
        "category": "Insights",
        "default_enabled": True,
    },
]


class BroadcastReaction(models.Model):
    id = models.BigAutoField(primary_key=True)
    broadcast_item = models.ForeignKey(
        BroadcastItem,
        on_delete=models.CASCADE,
        related_name="reactions",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="broadcast_reactions",
    )
    emoji = models.CharField(max_length=16, default="❤️")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "broadcast_reaction"
        unique_together = [("broadcast_item", "user")]
        indexes = [
            models.Index(fields=["broadcast_item", "user"]),
        ]


class BroadcastFeature(models.Model):
    slug = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=64, blank=True)
    default_enabled = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcast_feature"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class BroadcastFeatureFlag(models.Model):
    feature = models.ForeignKey(
        BroadcastFeature,
        on_delete=models.CASCADE,
        related_name="flags",
    )
    channel = models.ForeignKey(
        Channel,
        on_delete=models.CASCADE,
        related_name="feature_flags",
    )
    broadcast_item = models.ForeignKey(
        BroadcastItem,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="feature_flags",
    )
    enabled = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcast_feature_flag"
        unique_together = [
            ("feature", "channel", "broadcast_item"),
        ]
        indexes = [
            models.Index(fields=["feature", "channel"]),
        ]


class BroadcastVideo(models.Model):
    VIDEO_TYPES = [
        ("short", "Short"),
        ("video", "Video"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    channel = models.ForeignKey(Channel, null=True, blank=True, on_delete=models.SET_NULL, related_name="videos")
    creator = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="videos")
    video_url = models.URLField()
    thumbnail_url = models.URLField(blank=True)
    mime_type = models.CharField(max_length=256, blank=True)
    storage_path = models.CharField(max_length=1024, blank=True)
    type = models.CharField(max_length=16, choices=VIDEO_TYPES, default="video", db_index=True)
    duration_seconds = models.PositiveIntegerField(default=0)
    transcript_segments = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcast_video"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title


class BroadcastLesson(models.Model):
    LESSON_TYPES = [
        ("partner", "Partner"),
        ("community", "Community"),
        ("global", "Global"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    broadcast_item = models.OneToOneField(
        BroadcastItem,
        on_delete=models.CASCADE,
        related_name="lesson",
    )
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True)
    lesson_url = models.URLField(blank=True)
    lesson_type = models.CharField(max_length=16, choices=LESSON_TYPES, default="global", db_index=True)
    partner = models.ForeignKey(
        Partner,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lessons",
    )
    community = models.ForeignKey(
        Community,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lessons",
    )
    public_info = models.JSONField(default=dict, blank=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    price_cents = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=10, default="USD")
    is_public = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcast_lesson"
        indexes = [
            models.Index(fields=["lesson_type"]),
            models.Index(fields=["partner"]),
            models.Index(fields=["community"]),
        ]

    def __str__(self) -> str:
        return self.title


class LessonEnrollmentStatus(models.TextChoices):
    ENROLLED = "enrolled", "Enrolled"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class LessonEnrollment(models.Model):
    id = models.BigAutoField(primary_key=True)
    lesson = models.ForeignKey(
        BroadcastLesson,
        on_delete=models.CASCADE,
        related_name="enrollments",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="lesson_enrollments",
    )
    status = models.CharField(
        max_length=16,
        choices=LessonEnrollmentStatus.choices,
        default=LessonEnrollmentStatus.ENROLLED,
    )
    enrolled_at = models.DateTimeField(default=timezone.now)
    partner_membership_id = models.BigIntegerField(null=True, blank=True)
    community_membership_id = models.BigIntegerField(null=True, blank=True)

    class Meta:
        db_table = "lesson_enrollment"
        unique_together = [("lesson", "user")]
        indexes = [
            models.Index(fields=["lesson", "user"]),
            models.Index(fields=["user", "status"]),
        ]
