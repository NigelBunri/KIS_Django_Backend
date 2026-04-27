import uuid
from datetime import timedelta

from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import Profile, User
from apps.chat.models import Conversation
from apps.channels.models import Channel
from apps.communities.models import Community
from apps.partners.models import Partner
from apps.billing.models import WalletTransaction
from apps.commerce.constants import KIS_COIN_CODE
from common.media_urls import normalize_image_payload


class BroadcastSourceType(models.TextChoices):
    COMMUNITY_POST = "community_post", "Community Post"
    PARTNER_POST = "partner_post", "Partner Post"
    CHANNEL_MESSAGE = "channel_message", "Channel Message"
    BROADCAST_FEED_ENTRY = "broadcast_feed_entry", "Broadcast Feed Entry"
    MARKET_PRODUCT = "market_product", "Market Product"
    MARKET_SERVICE = "market_service", "Market Service"
    EDUCATION_COURSE = "education_course", "Education Course"
    EDUCATION_PROFILE = "education_profile", "Education Profile"
    EDUCATION_BROADCAST = "education_broadcast", "Education Broadcast"


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


class EducationInstitutionType(models.TextChoices):
    SCHOOL = "school", "School"
    COLLEGE = "college", "College"
    UNIVERSITY = "university", "University"
    ACADEMY = "academy", "Academy"
    TRAINING_CENTER = "training_center", "Training Center"
    BOOTCAMP = "bootcamp", "Bootcamp"
    COMMUNITY = "community", "Community"
    OTHER = "other", "Other"


class EducationInstitutionMembershipPolicy(models.TextChoices):
    OPEN = "open", "Open Membership"
    APPLICATION = "application", "Application Required"
    CLOSED = "closed", "Closed Membership"


class EducationInstitutionMembershipRole(models.TextChoices):
    OWNER = "owner", "Owner"
    MANAGER = "manager", "Manager"
    ADMINISTRATOR = "administrator", "Administrator"
    LECTURER = "lecturer", "Lecturer"
    ACADEMIC_STAFF = "academic_staff", "Academic Staff"
    STUDENT = "student", "Student"


class EducationInstitutionMembershipStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    PENDING = "pending", "Pending"
    REJECTED = "rejected", "Rejected"
    INVITED = "invited", "Invited"
    REMOVED = "removed", "Removed"


class EducationInstitution(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="owned_education_institutions",
    )
    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name="education_institutions",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    institution_type = models.CharField(
        max_length=32,
        choices=EducationInstitutionType.choices,
        default=EducationInstitutionType.ACADEMY,
    )
    membership_policy = models.CharField(
        max_length=16,
        choices=EducationInstitutionMembershipPolicy.choices,
        default=EducationInstitutionMembershipPolicy.APPLICATION,
    )
    contact_email = models.EmailField(blank=True, default="")
    contact_phone = models.CharField(max_length=64, blank=True, default="")
    branding = models.JSONField(default=dict, blank=True)
    settings = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution"
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["owner", "is_active"]),
            models.Index(fields=["institution_type", "is_active"]),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if isinstance(self.branding, dict):
            image_keys = {
                "logo_url",
                "logoUrl",
                "image_url",
                "imageUrl",
                "banner_image_url",
                "bannerImageUrl",
                "cover_image_url",
                "coverImageUrl",
            }
            self.branding = {
                key: normalize_image_payload(value) if key in image_keys else value
                for key, value in self.branding.items()
            }
        super().save(*args, **kwargs)


class EducationInstitutionMembership(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="education_institution_memberships",
    )
    role = models.CharField(
        max_length=32,
        choices=EducationInstitutionMembershipRole.choices,
        default=EducationInstitutionMembershipRole.STUDENT,
    )
    status = models.CharField(
        max_length=16,
        choices=EducationInstitutionMembershipStatus.choices,
        default=EducationInstitutionMembershipStatus.PENDING,
    )
    title = models.CharField(max_length=255, blank=True, default="")
    permissions = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="created_education_institution_memberships",
        null=True,
        blank=True,
    )
    decided_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="decided_education_institution_memberships",
        null=True,
        blank=True,
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_membership"
        unique_together = [("institution", "user")]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["institution", "role"]),
            models.Index(fields=["user", "status"]),
        ]

    def __str__(self):
        return f"{self.institution_id}:{self.user_id}:{self.role}"


class EducationAcademicRecordStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"
    ARCHIVED = "archived", "Archived"


class EducationClassSessionStatus(models.TextChoices):
    SCHEDULED = "scheduled", "Scheduled"
    LIVE = "live", "Live"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class EducationClassSessionMode(models.TextChoices):
    ONLINE = "online", "Online"
    ONSITE = "onsite", "Onsite"
    HYBRID = "hybrid", "Hybrid"


class EducationMaterialKind(models.TextChoices):
    DOCUMENT = "document", "Document"
    VIDEO = "video", "Video"
    LINK = "link", "Link"
    SLIDES = "slides", "Slides"
    ASSIGNMENT = "assignment", "Assignment"
    REFERENCE = "reference", "Reference"


class EducationAssessmentType(models.TextChoices):
    MCQ = "mcq", "MCQ"
    THEORY = "theory", "Theory"
    MIXED = "mixed", "Mixed"


class EducationAssessmentQuestionType(models.TextChoices):
    MCQ = "mcq", "MCQ"
    TRUE_FALSE = "true_false", "True / False"
    SHORT_ANSWER = "short_answer", "Short Answer"
    ESSAY = "essay", "Essay"


class EducationAssessmentSubmissionStatus(models.TextChoices):
    STARTED = "started", "Started"
    SUBMITTED = "submitted", "Submitted"
    GRADED = "graded", "Graded"
    RETURNED = "returned", "Returned"
    CANCELLED = "cancelled", "Cancelled"


class EducationInstitutionEventType(models.TextChoices):
    EVENT = "event", "Event"
    TRAINING_SESSION = "training_session", "Training Session"


class EducationBroadcastKind(models.TextChoices):
    PROGRAM = "program", "Program"
    COURSE = "course", "Course"
    LESSON = "lesson", "Lesson"
    CLASS_SESSION = "class_session", "Class Session"
    TRAINING_SESSION = "training_session", "Training Session"
    EVENT = "event", "Event"
    INSTITUTION_NOTICE = "institution_notice", "Institution Notice"


class EducationBroadcastStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"
    ARCHIVED = "archived", "Archived"


class EducationEnrollmentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ENROLLED = "enrolled", "Enrolled"
    WAITLISTED = "waitlisted", "Waitlisted"
    CANCELLED = "cancelled", "Cancelled"
    COMPLETED = "completed", "Completed"


class EducationBookingStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PAYMENT_PENDING = "payment_pending", "Payment Pending"
    CONFIRMED = "confirmed", "Confirmed"
    WAITLISTED = "waitlisted", "Waitlisted"
    AWAITING_SATISFACTION = "awaiting_satisfaction", "Awaiting Satisfaction"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"
    EXPIRED = "expired", "Expired"
    REFUNDED = "refunded", "Refunded"


class EducationInstitutionStaffAssignmentRole(models.TextChoices):
    INSTRUCTOR = "instructor", "Instructor"
    COORDINATOR = "coordinator", "Coordinator"
    EXAMINER = "examiner", "Examiner"
    ADVISOR = "advisor", "Advisor"
    MODERATOR = "moderator", "Moderator"
    EVENT_HOST = "event_host", "Event Host"


class EducationInstitutionStaffAssignmentStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    INACTIVE = "inactive", "Inactive"


class EducationCourseModuleItemType(models.TextChoices):
    LESSON = "lesson", "Lesson"
    MATERIAL = "material", "Material"
    CLASS_SESSION = "class_session", "Class Session"
    ASSESSMENT = "assessment", "Assessment"
    EVENT = "event", "Event"
    BROADCAST = "broadcast", "Broadcast"


class EducationInstitutionProgram(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="programs",
    )
    title = models.CharField(max_length=255)
    code = models.CharField(max_length=64, blank=True, default="")
    summary = models.TextField(blank=True, default="")
    description = models.TextField(blank=True, default="")
    cover_image_url = models.URLField(blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=EducationAcademicRecordStatus.choices,
        default=EducationAcademicRecordStatus.DRAFT,
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_program"
        ordering = ["title", "-created_at"]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["institution", "code"]),
        ]

    def __str__(self):
        return self.title


class EducationInstitutionStaffAssignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="staff_assignments",
    )
    membership = models.ForeignKey(
        EducationInstitutionMembership,
        on_delete=models.CASCADE,
        related_name="staff_assignments",
    )
    program = models.ForeignKey(
        "EducationInstitutionProgram",
        on_delete=models.SET_NULL,
        related_name="staff_assignments",
        null=True,
        blank=True,
    )
    course = models.ForeignKey(
        "EducationInstitutionCourse",
        on_delete=models.SET_NULL,
        related_name="staff_assignments",
        null=True,
        blank=True,
    )
    class_session = models.ForeignKey(
        "EducationInstitutionClassSession",
        on_delete=models.SET_NULL,
        related_name="staff_assignments",
        null=True,
        blank=True,
    )
    event = models.ForeignKey(
        "EducationInstitutionEvent",
        on_delete=models.SET_NULL,
        related_name="staff_assignments",
        null=True,
        blank=True,
    )
    assessment = models.ForeignKey(
        "EducationInstitutionAssessment",
        on_delete=models.SET_NULL,
        related_name="staff_assignments",
        null=True,
        blank=True,
    )
    role = models.CharField(
        max_length=24,
        choices=EducationInstitutionStaffAssignmentRole.choices,
        default=EducationInstitutionStaffAssignmentRole.INSTRUCTOR,
    )
    status = models.CharField(
        max_length=16,
        choices=EducationInstitutionStaffAssignmentStatus.choices,
        default=EducationInstitutionStaffAssignmentStatus.ACTIVE,
    )
    notes = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="created_education_staff_assignments",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_staff_assignment"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["membership", "status"]),
            models.Index(fields=["course", "status"]),
            models.Index(fields=["class_session", "status"]),
            models.Index(fields=["event", "status"]),
        ]

    def __str__(self):
        return f"{self.institution_id}:{self.membership_id}:{self.role}"


class EducationInstitutionCourse(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="courses_v2",
    )
    program = models.ForeignKey(
        EducationInstitutionProgram,
        on_delete=models.SET_NULL,
        related_name="courses",
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=255)
    code = models.CharField(max_length=64, blank=True, default="")
    summary = models.TextField(blank=True, default="")
    description = models.TextField(blank=True, default="")
    cover_image_url = models.URLField(blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=EducationAcademicRecordStatus.choices,
        default=EducationAcademicRecordStatus.DRAFT,
    )
    duration_minutes = models.PositiveIntegerField(default=0)
    seat_limit = models.PositiveIntegerField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    settings = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_course"
        ordering = ["title", "-created_at"]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["institution", "code"]),
            models.Index(fields=["program", "status"]),
        ]

    def __str__(self):
        return self.title


class EducationInstitutionCourseModule(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="course_modules",
    )
    course = models.ForeignKey(
        EducationInstitutionCourse,
        on_delete=models.CASCADE,
        related_name="modules_v2",
    )
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    module_order = models.PositiveIntegerField(default=0)
    is_preview = models.BooleanField(default=False)
    status = models.CharField(
        max_length=16,
        choices=EducationAcademicRecordStatus.choices,
        default=EducationAcademicRecordStatus.DRAFT,
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_course_module"
        ordering = ["module_order", "title", "-created_at"]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["course", "module_order"]),
        ]

    def __str__(self):
        return self.title


class EducationInstitutionLesson(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="lessons_v2",
    )
    course = models.ForeignKey(
        EducationInstitutionCourse,
        on_delete=models.CASCADE,
        related_name="lessons",
    )
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    content = models.TextField(blank=True, default="")
    cover_image_url = models.URLField(blank=True, default="")
    lesson_order = models.PositiveIntegerField(default=0)
    duration_minutes = models.PositiveIntegerField(default=0)
    is_preview = models.BooleanField(default=False)
    status = models.CharField(
        max_length=16,
        choices=EducationAcademicRecordStatus.choices,
        default=EducationAcademicRecordStatus.DRAFT,
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_lesson"
        ordering = ["lesson_order", "title", "-created_at"]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["course", "lesson_order"]),
        ]

    def __str__(self):
        return self.title


class EducationInstitutionCourseModuleItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="course_module_items",
    )
    course = models.ForeignKey(
        EducationInstitutionCourse,
        on_delete=models.CASCADE,
        related_name="module_items",
    )
    module = models.ForeignKey(
        EducationInstitutionCourseModule,
        on_delete=models.CASCADE,
        related_name="items",
    )
    item_type = models.CharField(
        max_length=24,
        choices=EducationCourseModuleItemType.choices,
        default=EducationCourseModuleItemType.LESSON,
    )
    item_order = models.PositiveIntegerField(default=0)
    title_override = models.CharField(max_length=255, blank=True, default="")
    summary_override = models.TextField(blank=True, default="")
    estimated_minutes = models.PositiveIntegerField(default=0)
    lesson = models.ForeignKey(
        "EducationInstitutionLesson",
        on_delete=models.CASCADE,
        related_name="module_items",
        null=True,
        blank=True,
    )
    material = models.ForeignKey(
        "EducationInstitutionMaterial",
        on_delete=models.CASCADE,
        related_name="module_items",
        null=True,
        blank=True,
    )
    class_session = models.ForeignKey(
        "EducationInstitutionClassSession",
        on_delete=models.CASCADE,
        related_name="module_items",
        null=True,
        blank=True,
    )
    assessment = models.ForeignKey(
        "EducationInstitutionAssessment",
        on_delete=models.CASCADE,
        related_name="module_items",
        null=True,
        blank=True,
    )
    event = models.ForeignKey(
        "EducationInstitutionEvent",
        on_delete=models.CASCADE,
        related_name="module_items",
        null=True,
        blank=True,
    )
    broadcast = models.ForeignKey(
        "EducationInstitutionBroadcast",
        on_delete=models.CASCADE,
        related_name="module_items",
        null=True,
        blank=True,
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_course_module_item"
        ordering = ["item_order", "created_at"]
        indexes = [
            models.Index(fields=["institution", "item_type"]),
            models.Index(fields=["course", "item_order"]),
            models.Index(fields=["module", "item_order"]),
        ]

    def __str__(self):
        return f"{self.module_id}:{self.item_type}:{self.item_order}"


class EducationInstitutionClassSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="class_sessions",
    )
    course = models.ForeignKey(
        EducationInstitutionCourse,
        on_delete=models.CASCADE,
        related_name="class_sessions",
        null=True,
        blank=True,
    )
    lesson = models.ForeignKey(
        EducationInstitutionLesson,
        on_delete=models.SET_NULL,
        related_name="class_sessions",
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    cover_image_url = models.URLField(blank=True, default="")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    timezone_name = models.CharField(max_length=64, blank=True, default="UTC")
    delivery_mode = models.CharField(
        max_length=16,
        choices=EducationClassSessionMode.choices,
        default=EducationClassSessionMode.ONLINE,
    )
    location_text = models.CharField(max_length=255, blank=True, default="")
    meeting_url = models.URLField(blank=True, default="")
    seat_limit = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=EducationClassSessionStatus.choices,
        default=EducationClassSessionStatus.SCHEDULED,
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_class_session"
        ordering = ["starts_at", "-created_at"]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["institution", "starts_at"]),
            models.Index(fields=["course", "starts_at"]),
        ]

    def __str__(self):
        return self.title


class EducationInstitutionMaterial(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="materials",
    )
    program = models.ForeignKey(
        EducationInstitutionProgram,
        on_delete=models.SET_NULL,
        related_name="materials",
        null=True,
        blank=True,
    )
    course = models.ForeignKey(
        EducationInstitutionCourse,
        on_delete=models.CASCADE,
        related_name="materials",
        null=True,
        blank=True,
    )
    lesson = models.ForeignKey(
        EducationInstitutionLesson,
        on_delete=models.CASCADE,
        related_name="materials",
        null=True,
        blank=True,
    )
    class_session = models.ForeignKey(
        EducationInstitutionClassSession,
        on_delete=models.SET_NULL,
        related_name="materials",
        null=True,
        blank=True,
    )
    assessment = models.ForeignKey(
        "EducationInstitutionAssessment",
        on_delete=models.SET_NULL,
        related_name="materials",
        null=True,
        blank=True,
    )
    program_links = models.ManyToManyField(
        EducationInstitutionProgram,
        related_name="linked_materials",
        blank=True,
    )
    course_links = models.ManyToManyField(
        "EducationInstitutionCourse",
        related_name="linked_materials",
        blank=True,
    )
    lesson_links = models.ManyToManyField(
        "EducationInstitutionLesson",
        related_name="linked_materials",
        blank=True,
    )
    class_session_links = models.ManyToManyField(
        "EducationInstitutionClassSession",
        related_name="linked_materials",
        blank=True,
    )
    assessment_links = models.ManyToManyField(
        "EducationInstitutionAssessment",
        related_name="linked_materials",
        blank=True,
    )
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    cover_image_url = models.URLField(blank=True, default="")
    kind = models.CharField(
        max_length=16,
        choices=EducationMaterialKind.choices,
        default=EducationMaterialKind.DOCUMENT,
    )
    resource_url = models.URLField(blank=True, default="")
    resource_name = models.CharField(max_length=255, blank=True, default="")
    resource_mime_type = models.CharField(max_length=128, blank=True, default="")
    storage_path = models.CharField(max_length=512, blank=True, default="")
    is_downloadable = models.BooleanField(default=True)
    status = models.CharField(
        max_length=16,
        choices=EducationAcademicRecordStatus.choices,
        default=EducationAcademicRecordStatus.DRAFT,
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_material"
        ordering = ["title", "-created_at"]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["program", "status"]),
            models.Index(fields=["course", "status"]),
            models.Index(fields=["lesson", "status"]),
            models.Index(fields=["class_session", "status"]),
            models.Index(fields=["assessment", "status"]),
        ]

    def __str__(self):
        return self.title


class EducationInstitutionAssessment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="assessments",
    )
    course = models.ForeignKey(
        EducationInstitutionCourse,
        on_delete=models.CASCADE,
        related_name="assessments",
        null=True,
        blank=True,
    )
    lesson = models.ForeignKey(
        EducationInstitutionLesson,
        on_delete=models.CASCADE,
        related_name="assessments",
        null=True,
        blank=True,
    )
    class_session = models.ForeignKey(
        EducationInstitutionClassSession,
        on_delete=models.CASCADE,
        related_name="assessments",
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    instructions = models.TextField(blank=True, default="")
    cover_image_url = models.URLField(blank=True, default="")
    assessment_type = models.CharField(
        max_length=16,
        choices=EducationAssessmentType.choices,
        default=EducationAssessmentType.MCQ,
    )
    status = models.CharField(
        max_length=16,
        choices=EducationAcademicRecordStatus.choices,
        default=EducationAcademicRecordStatus.DRAFT,
    )
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    duration_minutes = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=1)
    passing_score_percent = models.PositiveIntegerField(default=0)
    total_points = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    metadata = models.JSONField(default=dict, blank=True)
    settings = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_assessment"
        ordering = ["title", "-created_at"]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["course", "status"]),
            models.Index(fields=["lesson", "status"]),
            models.Index(fields=["class_session", "status"]),
        ]

    def __str__(self):
        return self.title


class EducationInstitutionAssessmentQuestion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assessment = models.ForeignKey(
        EducationInstitutionAssessment,
        on_delete=models.CASCADE,
        related_name="questions",
    )
    prompt = models.TextField()
    question_type = models.CharField(
        max_length=16,
        choices=EducationAssessmentQuestionType.choices,
        default=EducationAssessmentQuestionType.MCQ,
    )
    question_order = models.PositiveIntegerField(default=0)
    points = models.DecimalField(max_digits=8, decimal_places=2, default=1)
    is_required = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_assessment_question"
        ordering = ["question_order", "created_at"]
        indexes = [
            models.Index(fields=["assessment", "question_order"]),
            models.Index(fields=["assessment", "question_type"]),
        ]

    def __str__(self):
        return self.prompt[:80]


class EducationInstitutionAssessmentOption(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    question = models.ForeignKey(
        EducationInstitutionAssessmentQuestion,
        on_delete=models.CASCADE,
        related_name="options",
    )
    option_text = models.TextField()
    option_order = models.PositiveIntegerField(default=0)
    is_correct = models.BooleanField(default=False)
    explanation = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_assessment_option"
        ordering = ["option_order", "created_at"]
        indexes = [
            models.Index(fields=["question", "option_order"]),
        ]

    def __str__(self):
        return self.option_text[:80]


class EducationInstitutionAssessmentSubmission(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assessment = models.ForeignKey(
        EducationInstitutionAssessment,
        on_delete=models.CASCADE,
        related_name="submissions",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="education_assessment_submissions",
    )
    attempt_number = models.PositiveIntegerField(default=1)
    status = models.CharField(
        max_length=16,
        choices=EducationAssessmentSubmissionStatus.choices,
        default=EducationAssessmentSubmissionStatus.STARTED,
    )
    earned_points = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    score_percent = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    grader = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="graded_education_assessment_submissions",
        null=True,
        blank=True,
    )
    grader_feedback = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    submitted_at = models.DateTimeField(null=True, blank=True)
    graded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_assessment_submission"
        ordering = ["-created_at"]
        unique_together = [("assessment", "user", "attempt_number")]
        indexes = [
            models.Index(fields=["assessment", "status"]),
            models.Index(fields=["user", "status"]),
        ]

    def __str__(self):
        return f"{self.assessment_id}:{self.user_id}:#{self.attempt_number}"


class EducationInstitutionAssessmentResponse(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    submission = models.ForeignKey(
        EducationInstitutionAssessmentSubmission,
        on_delete=models.CASCADE,
        related_name="responses",
    )
    question = models.ForeignKey(
        EducationInstitutionAssessmentQuestion,
        on_delete=models.CASCADE,
        related_name="responses",
    )
    answer_text = models.TextField(blank=True, default="")
    is_correct = models.BooleanField(null=True, blank=True)
    earned_points = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    grader_feedback = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_assessment_response"
        unique_together = [("submission", "question")]
        indexes = [
            models.Index(fields=["submission", "question"]),
        ]

    def __str__(self):
        return f"{self.submission_id}:{self.question_id}"


class EducationInstitutionAssessmentResponseOption(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    response = models.ForeignKey(
        EducationInstitutionAssessmentResponse,
        on_delete=models.CASCADE,
        related_name="selected_options",
    )
    option = models.ForeignKey(
        EducationInstitutionAssessmentOption,
        on_delete=models.CASCADE,
        related_name="response_links",
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "education_institution_assessment_response_option"
        unique_together = [("response", "option")]
        indexes = [
            models.Index(fields=["response", "option"]),
        ]


class EducationInstitutionEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="events",
    )
    program = models.ForeignKey(
        EducationInstitutionProgram,
        on_delete=models.SET_NULL,
        related_name="events",
        null=True,
        blank=True,
    )
    course = models.ForeignKey(
        EducationInstitutionCourse,
        on_delete=models.SET_NULL,
        related_name="events",
        null=True,
        blank=True,
    )
    class_session = models.ForeignKey(
        EducationInstitutionClassSession,
        on_delete=models.SET_NULL,
        related_name="events",
        null=True,
        blank=True,
    )
    event_type = models.CharField(
        max_length=24,
        choices=EducationInstitutionEventType.choices,
        default=EducationInstitutionEventType.EVENT,
    )
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    description = models.TextField(blank=True, default="")
    cover_image_url = models.URLField(blank=True, default="")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    timezone_name = models.CharField(max_length=64, blank=True, default="UTC")
    delivery_mode = models.CharField(
        max_length=16,
        choices=EducationClassSessionMode.choices,
        default=EducationClassSessionMode.ONLINE,
    )
    location_text = models.CharField(max_length=255, blank=True, default="")
    meeting_url = models.URLField(blank=True, default="")
    seat_limit = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=EducationAcademicRecordStatus.choices,
        default=EducationAcademicRecordStatus.DRAFT,
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_event"
        ordering = ["starts_at", "-created_at"]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["program", "status"]),
            models.Index(fields=["course", "status"]),
            models.Index(fields=["class_session", "status"]),
            models.Index(fields=["institution", "event_type"]),
            models.Index(fields=["institution", "starts_at"]),
        ]

    def __str__(self):
        return self.title


class EducationInstitutionBroadcast(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="education_broadcasts",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="education_broadcasts_created",
    )
    broadcast_item = models.OneToOneField(
        BroadcastItem,
        on_delete=models.SET_NULL,
        related_name="education_broadcast_row",
        null=True,
        blank=True,
    )
    broadcast_kind = models.CharField(
        max_length=24,
        choices=EducationBroadcastKind.choices,
    )
    program = models.ForeignKey(
        EducationInstitutionProgram,
        on_delete=models.SET_NULL,
        related_name="broadcasts",
        null=True,
        blank=True,
    )
    course = models.ForeignKey(
        EducationInstitutionCourse,
        on_delete=models.CASCADE,
        related_name="broadcasts",
        null=True,
        blank=True,
    )
    lesson = models.ForeignKey(
        EducationInstitutionLesson,
        on_delete=models.CASCADE,
        related_name="broadcasts",
        null=True,
        blank=True,
    )
    class_session = models.ForeignKey(
        EducationInstitutionClassSession,
        on_delete=models.CASCADE,
        related_name="broadcasts",
        null=True,
        blank=True,
    )
    event = models.ForeignKey(
        EducationInstitutionEvent,
        on_delete=models.CASCADE,
        related_name="broadcasts",
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    description = models.TextField(blank=True, default="")
    cover_image_url = models.URLField(blank=True, default="")
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    timezone_name = models.CharField(max_length=64, blank=True, default="UTC")
    seat_limit = models.PositiveIntegerField(null=True, blank=True)
    booking_enabled = models.BooleanField(default=False)
    price_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    price_currency = models.CharField(max_length=8, blank=True, default=KIS_COIN_CODE)
    status = models.CharField(
        max_length=16,
        choices=EducationBroadcastStatus.choices,
        default=EducationBroadcastStatus.PUBLISHED,
    )
    published_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(default=_default_expires_at)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_broadcast"
        ordering = ["-published_at", "-created_at"]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["institution", "broadcast_kind"]),
            models.Index(fields=["program", "status"]),
            models.Index(fields=["published_at"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        self.cover_image_url = normalize_image_payload(self.cover_image_url)
        super().save(*args, **kwargs)


class EducationInstitutionEnrollment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="enrollments",
    )
    broadcast = models.ForeignKey(
        EducationInstitutionBroadcast,
        on_delete=models.CASCADE,
        related_name="enrollments",
    )
    program = models.ForeignKey(
        EducationInstitutionProgram,
        on_delete=models.SET_NULL,
        related_name="enrollments",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="education_enrollments",
    )
    course = models.ForeignKey(
        EducationInstitutionCourse,
        on_delete=models.CASCADE,
        related_name="enrollments",
        null=True,
        blank=True,
    )
    lesson = models.ForeignKey(
        EducationInstitutionLesson,
        on_delete=models.CASCADE,
        related_name="enrollments",
        null=True,
        blank=True,
    )
    class_session = models.ForeignKey(
        EducationInstitutionClassSession,
        on_delete=models.CASCADE,
        related_name="enrollments",
        null=True,
        blank=True,
    )
    event = models.ForeignKey(
        EducationInstitutionEvent,
        on_delete=models.CASCADE,
        related_name="enrollments",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=16,
        choices=EducationEnrollmentStatus.choices,
        default=EducationEnrollmentStatus.PENDING,
    )
    enrolled_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_enrollment"
        unique_together = [("broadcast", "user")]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["broadcast", "status"]),
            models.Index(fields=["program", "status"]),
            models.Index(fields=["user", "status"]),
        ]


class EducationInstitutionBooking(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution = models.ForeignKey(
        EducationInstitution,
        on_delete=models.CASCADE,
        related_name="bookings",
    )
    broadcast = models.ForeignKey(
        EducationInstitutionBroadcast,
        on_delete=models.CASCADE,
        related_name="bookings",
    )
    program = models.ForeignKey(
        EducationInstitutionProgram,
        on_delete=models.SET_NULL,
        related_name="bookings",
        null=True,
        blank=True,
    )
    course = models.ForeignKey(
        EducationInstitutionCourse,
        on_delete=models.SET_NULL,
        related_name="bookings",
        null=True,
        blank=True,
    )
    class_session = models.ForeignKey(
        EducationInstitutionClassSession,
        on_delete=models.SET_NULL,
        related_name="bookings",
        null=True,
        blank=True,
    )
    event = models.ForeignKey(
        EducationInstitutionEvent,
        on_delete=models.SET_NULL,
        related_name="bookings",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="education_bookings",
    )
    status = models.CharField(
        max_length=24,
        choices=EducationBookingStatus.choices,
        default=EducationBookingStatus.PENDING,
    )
    seat_count = models.PositiveIntegerField(default=1)
    amount_cents = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=8, default=KIS_COIN_CODE)
    payment_method = models.CharField(max_length=32, blank=True, default="")
    wallet_transaction = models.ForeignKey(
        WalletTransaction,
        on_delete=models.SET_NULL,
        related_name="education_bookings",
        null=True,
        blank=True,
    )
    provider_credit_transaction = models.ForeignKey(
        WalletTransaction,
        on_delete=models.SET_NULL,
        related_name="education_booking_provider_payouts",
        null=True,
        blank=True,
    )
    reserved_at = models.DateTimeField(default=timezone.now)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    provider_completed_at = models.DateTimeField(null=True, blank=True)
    payer_satisfied_at = models.DateTimeField(null=True, blank=True)
    satisfaction_deadline = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "education_institution_booking"
        unique_together = [("broadcast", "user")]
        indexes = [
            models.Index(fields=["institution", "status"]),
            models.Index(fields=["broadcast", "status"]),
            models.Index(fields=["program", "status"]),
            models.Index(fields=["course", "status"]),
            models.Index(fields=["class_session", "status"]),
            models.Index(fields=["event", "status"]),
            models.Index(fields=["user", "status"]),
            models.Index(fields=["satisfaction_deadline"]),
        ]

    @property
    def complaint_window_expires(self):
        if not self.provider_completed_at:
            return None
        return self.provider_completed_at + timedelta(days=3)


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
