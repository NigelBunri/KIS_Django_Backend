import os

from django.conf import settings
from django.urls import reverse
from rest_framework import serializers

from apps.broadcasts.media_utils import build_media_url, ensure_local_thumbnail
from apps.broadcasts.health_engine_policy import is_service_medium_allowed

from .models import (
    BroadcastFeature,
    BroadcastVideo,
    BroadcastLesson,
    LessonEnrollment,
    EducationInstitutionAssessment,
    EducationInstitutionAssessmentQuestion,
    EducationInstitutionAssessmentOption,
    EducationInstitutionAssessmentSubmission,
    EducationInstitutionAssessmentResponse,
    EducationInstitutionAssessmentResponseOption,
    EducationInstitutionBroadcast,
    EducationInstitutionBooking,
    EducationInstitutionEnrollment,
    EducationInstitutionEvent,
    EducationInstitutionStaffAssignment,
    EducationInstitutionProgram,
    EducationInstitutionCourse,
    EducationInstitutionCourseModule,
    EducationInstitutionCourseModuleItem,
    EducationInstitutionLesson,
    EducationInstitutionClassSession,
    EducationInstitutionMaterial,
    EducationInstitution,
    EducationInstitutionMembership,
    EducationProfile,
    EducationProfileCourse,
    EducationProfileModule,
    EducationProfileRole,
    EducationProfileRoleAssignment,
    Medium,
    Service,
    ServiceMediumMap,
)
from apps.partners.models import Partner
from apps.communities.models import Community
from apps.accounts.models import User




class MediumSerializer(serializers.ModelSerializer):
    class Meta:
        model = Medium
        fields = ["id", "name", "description", "system_flag", "created_at", "updated_at"]



class ServiceMediumMapSerializer(serializers.ModelSerializer):
    medium = MediumSerializer(read_only=True)

    class Meta:
        model = ServiceMediumMap
        fields = ["id", "medium", "created_at"]


class ServiceSerializer(serializers.ModelSerializer):
    medium_links = serializers.SerializerMethodField()
    medium_ids = serializers.SerializerMethodField()

    class Meta:
        model = Service
        fields = [
            "id",
            "name",
            "description",
            "is_default",
            "created_by",
            "medium_links",
            "medium_ids",
            "created_at",
            "updated_at",
        ]

    def get_medium_ids(self, obj: Service):
        return [
            str(link.medium_id)
            for link in obj.medium_links.all()
            if is_service_medium_allowed(link.medium_id, getattr(link.medium, "name", ""))
        ]

    def get_medium_links(self, obj: Service):
        rows = [
            link
            for link in obj.medium_links.all()
            if is_service_medium_allowed(link.medium_id, getattr(link.medium, "name", ""))
        ]
        return ServiceMediumMapSerializer(rows, many=True).data

class BroadcastFeatureSerializer(serializers.ModelSerializer):
    class Meta:
        model = BroadcastFeature
        fields = ["slug", "name", "description", "category", "default_enabled"]


class BroadcastFeatureStatusSerializer(BroadcastFeatureSerializer):
    enabled = serializers.BooleanField()


class BroadcastVideoSerializer(serializers.ModelSerializer):
    creator_name = serializers.SerializerMethodField()
    channel_name = serializers.SerializerMethodField()
    video_category = serializers.SerializerMethodField()
    stream_url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()
    media_file_path = serializers.SerializerMethodField()

    class Meta:
        model = BroadcastVideo
        fields = [
            "id",
            "title",
            "description",
            "video_url",
            "thumbnail_url",
            "type",
            "duration_seconds",
            "mime_type",
            "storage_path",
            "stream_url",
            "media_file_path",
            "transcript_segments",
            "video_category",
            "creator_name",
            "channel_name",
            "created_at",
        ]

    def get_creator_name(self, obj: BroadcastVideo):
        return obj.creator.display_name if obj.creator else None

    def get_channel_name(self, obj: BroadcastVideo):
        return obj.channel.name if obj.channel else None

    def get_video_category(self, obj: BroadcastVideo):
        if obj.duration_seconds < 4 * 60:
            return "shorts"
        return "videos"

    def get_stream_url(self, obj: BroadcastVideo):
        request = self.context.get("request")
        if not request:
            return obj.video_url
        return request.build_absolute_uri(
            reverse("broadcasts:video-stream", kwargs={"video_id": str(obj.id)})
        )

    def get_thumbnail_url(self, obj: BroadcastVideo):
        request = self.context.get("request")
        if obj.thumbnail_url and obj.thumbnail_url.startswith("http"):
            return obj.thumbnail_url
        rel = ensure_local_thumbnail(obj)
        if not rel:
            return obj.thumbnail_url or None
        if not request:
            return rel
        return build_media_url(request, rel)

    def get_media_file_path(self, obj: BroadcastVideo):
        media_root = getattr(settings, "MEDIA_ROOT", "media")
        return os.path.join(media_root, obj.storage_path)


class BroadcastLessonSerializer(serializers.ModelSerializer):
    partner_name = serializers.SerializerMethodField()
    community_name = serializers.SerializerMethodField()
    enrollment_count = serializers.IntegerField(read_only=True)
    is_enrolled = serializers.SerializerMethodField()

    class Meta:
        model = BroadcastLesson
        fields = [
            "id",
            "title",
            "summary",
            "lesson_url",
            "lesson_type",
            "partner_name",
            "community_name",
            "starts_at",
            "ends_at",
            "price_cents",
            "currency",
            "is_public",
            "public_info",
            "enrollment_count",
            "is_enrolled",
        ]

    def get_partner_name(self, obj: BroadcastLesson):
        return obj.partner.name if obj.partner else None

    def get_community_name(self, obj: BroadcastLesson):
        return obj.community.name if obj.community else None

    def get_is_enrolled(self, obj: BroadcastLesson):
        user = self.context.get("request").user if self.context.get("request") else None
        if not user or not getattr(user, "is_authenticated", False):
            return False
        return LessonEnrollment.objects.filter(lesson=obj, user=user).exists()


class LessonEnrollmentSerializer(serializers.ModelSerializer):
    lesson = BroadcastLessonSerializer(read_only=True)

    class Meta:
        model = LessonEnrollment
        fields = ["id", "lesson", "status", "enrolled_at"]


class EducationInstitutionMembershipSerializer(serializers.ModelSerializer):
    user_id = serializers.UUIDField(source="user.id", read_only=True)
    display_name = serializers.SerializerMethodField()
    phone = serializers.CharField(source="user.phone", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True, allow_null=True)

    class Meta:
        model = EducationInstitutionMembership
        fields = [
            "id",
            "user_id",
            "display_name",
            "phone",
            "email",
            "role",
            "status",
            "title",
            "permissions",
            "metadata",
            "created_at",
            "updated_at",
            "decided_at",
        ]

    def get_display_name(self, obj: EducationInstitutionMembership):
        user = getattr(obj, "user", None)
        return getattr(user, "display_name", "") or getattr(user, "username", "") or ""


class EducationInstitutionSerializer(serializers.ModelSerializer):
    memberships = EducationInstitutionMembershipSerializer(many=True, read_only=True)
    active_member_count = serializers.SerializerMethodField()
    pending_application_count = serializers.SerializerMethodField()
    current_membership = serializers.SerializerMethodField()
    can_manage = serializers.SerializerMethodField()

    class Meta:
        model = EducationInstitution
        fields = [
            "id",
            "name",
            "description",
            "institution_type",
            "membership_policy",
            "contact_email",
            "contact_phone",
            "branding",
            "settings",
            "metadata",
            "is_active",
            "active_member_count",
            "pending_application_count",
            "current_membership",
            "can_manage",
            "memberships",
            "created_at",
            "updated_at",
        ]

    def get_active_member_count(self, obj: EducationInstitution) -> int:
        prefetched = getattr(obj, "_prefetched_objects_cache", {})
        rows = prefetched.get("memberships")
        if rows is not None:
            return sum(1 for row in rows if row.status == "active")
        return obj.memberships.filter(status="active").count()

    def get_pending_application_count(self, obj: EducationInstitution) -> int:
        prefetched = getattr(obj, "_prefetched_objects_cache", {})
        rows = prefetched.get("memberships")
        if rows is not None:
            return sum(1 for row in rows if row.status == "pending")
        return obj.memberships.filter(status="pending").count()

    def get_current_membership(self, obj: EducationInstitution):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or not getattr(user, "is_authenticated", False):
            return None
        prefetched = getattr(obj, "_prefetched_objects_cache", {})
        rows = prefetched.get("memberships")
        if rows is not None:
            membership = next((row for row in rows if row.user_id == user.id), None)
        else:
            membership = obj.memberships.filter(user=user).select_related("user").first()
        if not membership:
            return None
        return EducationInstitutionMembershipSerializer(membership).data

    def get_can_manage(self, obj: EducationInstitution) -> bool:
        current = self.get_current_membership(obj) or {}
        return current.get("status") == "active" and current.get("role") in {
            "owner",
            "manager",
            "administrator",
        }


class EducationInstitutionProgramSerializer(serializers.ModelSerializer):
    class Meta:
        model = EducationInstitutionProgram
        fields = [
            "id",
            "title",
            "code",
            "summary",
            "description",
            "status",
            "metadata",
            "created_at",
            "updated_at",
        ]


class EducationInstitutionStaffAssignmentSerializer(serializers.ModelSerializer):
    membership_id = serializers.UUIDField(source="membership.id", read_only=True)
    user_id = serializers.UUIDField(source="membership.user.id", read_only=True)
    display_name = serializers.SerializerMethodField()
    program_id = serializers.UUIDField(source="program.id", read_only=True, allow_null=True)
    course_id = serializers.UUIDField(source="course.id", read_only=True, allow_null=True)
    class_session_id = serializers.UUIDField(source="class_session.id", read_only=True, allow_null=True)
    event_id = serializers.UUIDField(source="event.id", read_only=True, allow_null=True)
    assessment_id = serializers.UUIDField(source="assessment.id", read_only=True, allow_null=True)

    class Meta:
        model = EducationInstitutionStaffAssignment
        fields = [
            "id",
            "membership_id",
            "user_id",
            "display_name",
            "program_id",
            "course_id",
            "class_session_id",
            "event_id",
            "assessment_id",
            "role",
            "status",
            "notes",
            "metadata",
            "created_at",
            "updated_at",
        ]

    def get_display_name(self, obj: EducationInstitutionStaffAssignment):
        user = getattr(getattr(obj, "membership", None), "user", None)
        return getattr(user, "display_name", "") or getattr(user, "username", "") or ""


class EducationInstitutionCourseSerializer(serializers.ModelSerializer):
    program_id = serializers.UUIDField(source="program.id", read_only=True)

    class Meta:
        model = EducationInstitutionCourse
        fields = [
            "id",
            "program_id",
            "title",
            "code",
            "summary",
            "description",
            "status",
            "duration_minutes",
            "seat_limit",
            "metadata",
            "settings",
            "created_at",
            "updated_at",
        ]


class EducationInstitutionCourseModuleItemSerializer(serializers.ModelSerializer):
    lesson_id = serializers.UUIDField(source="lesson.id", read_only=True, allow_null=True)
    material_id = serializers.UUIDField(source="material.id", read_only=True, allow_null=True)
    class_session_id = serializers.UUIDField(source="class_session.id", read_only=True, allow_null=True)
    assessment_id = serializers.UUIDField(source="assessment.id", read_only=True, allow_null=True)
    event_id = serializers.UUIDField(source="event.id", read_only=True, allow_null=True)
    broadcast_id = serializers.UUIDField(source="broadcast.id", read_only=True, allow_null=True)

    class Meta:
        model = EducationInstitutionCourseModuleItem
        fields = [
            "id",
            "item_type",
            "item_order",
            "title_override",
            "summary_override",
            "estimated_minutes",
            "lesson_id",
            "material_id",
            "class_session_id",
            "assessment_id",
            "event_id",
            "broadcast_id",
            "metadata",
            "created_at",
            "updated_at",
        ]


class EducationInstitutionCourseModuleSerializer(serializers.ModelSerializer):
    course_id = serializers.UUIDField(source="course.id", read_only=True)
    items = EducationInstitutionCourseModuleItemSerializer(many=True, read_only=True)

    class Meta:
        model = EducationInstitutionCourseModule
        fields = [
            "id",
            "course_id",
            "title",
            "summary",
            "module_order",
            "is_preview",
            "status",
            "metadata",
            "items",
            "created_at",
            "updated_at",
        ]


class EducationInstitutionLessonSerializer(serializers.ModelSerializer):
    course_id = serializers.UUIDField(source="course.id", read_only=True)

    class Meta:
        model = EducationInstitutionLesson
        fields = [
            "id",
            "course_id",
            "title",
            "summary",
            "content",
            "lesson_order",
            "duration_minutes",
            "is_preview",
            "status",
            "metadata",
            "created_at",
            "updated_at",
        ]


class EducationInstitutionClassSessionSerializer(serializers.ModelSerializer):
    course_id = serializers.UUIDField(source="course.id", read_only=True)
    lesson_id = serializers.UUIDField(source="lesson.id", read_only=True)

    class Meta:
        model = EducationInstitutionClassSession
        fields = [
            "id",
            "course_id",
            "lesson_id",
            "title",
            "summary",
            "starts_at",
            "ends_at",
            "timezone_name",
            "delivery_mode",
            "location_text",
            "meeting_url",
            "seat_limit",
            "status",
            "metadata",
            "created_at",
            "updated_at",
        ]


class EducationInstitutionMaterialSerializer(serializers.ModelSerializer):
    program_id = serializers.UUIDField(source="program.id", read_only=True, allow_null=True)
    course_id = serializers.UUIDField(source="course.id", read_only=True)
    lesson_id = serializers.UUIDField(source="lesson.id", read_only=True)
    class_session_id = serializers.UUIDField(source="class_session.id", read_only=True, allow_null=True)
    assessment_id = serializers.UUIDField(source="assessment.id", read_only=True, allow_null=True)
    program_ids = serializers.SerializerMethodField()
    course_ids = serializers.SerializerMethodField()
    lesson_ids = serializers.SerializerMethodField()
    class_session_ids = serializers.SerializerMethodField()
    assessment_ids = serializers.SerializerMethodField()

    class Meta:
        model = EducationInstitutionMaterial
        fields = [
            "id",
            "program_id",
            "program_ids",
            "course_id",
            "course_ids",
            "lesson_id",
            "lesson_ids",
            "class_session_id",
            "class_session_ids",
            "assessment_id",
            "assessment_ids",
            "title",
            "summary",
            "kind",
            "resource_url",
            "resource_name",
            "resource_mime_type",
            "storage_path",
            "is_downloadable",
            "status",
            "metadata",
            "created_at",
            "updated_at",
        ]

    def get_program_ids(self, obj):
        ids = list(obj.program_links.values_list("id", flat=True))
        if obj.program_id and obj.program_id not in ids:
            ids.insert(0, obj.program_id)
        return [str(value) for value in ids]

    def get_course_ids(self, obj):
        ids = list(obj.course_links.values_list("id", flat=True))
        if obj.course_id and obj.course_id not in ids:
            ids.insert(0, obj.course_id)
        return [str(value) for value in ids]

    def get_lesson_ids(self, obj):
        ids = list(obj.lesson_links.values_list("id", flat=True))
        if obj.lesson_id and obj.lesson_id not in ids:
            ids.insert(0, obj.lesson_id)
        return [str(value) for value in ids]

    def get_class_session_ids(self, obj):
        ids = list(obj.class_session_links.values_list("id", flat=True))
        if obj.class_session_id and obj.class_session_id not in ids:
            ids.insert(0, obj.class_session_id)
        return [str(value) for value in ids]

    def get_assessment_ids(self, obj):
        ids = list(obj.assessment_links.values_list("id", flat=True))
        if obj.assessment_id and obj.assessment_id not in ids:
            ids.insert(0, obj.assessment_id)
        return [str(value) for value in ids]


class EducationInstitutionAssessmentOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = EducationInstitutionAssessmentOption
        fields = [
            "id",
            "option_text",
            "option_order",
            "is_correct",
            "explanation",
            "created_at",
            "updated_at",
        ]


class EducationInstitutionAssessmentQuestionSerializer(serializers.ModelSerializer):
    options = EducationInstitutionAssessmentOptionSerializer(many=True, read_only=True)

    class Meta:
        model = EducationInstitutionAssessmentQuestion
        fields = [
            "id",
            "prompt",
            "question_type",
            "question_order",
            "points",
            "is_required",
            "metadata",
            "options",
            "created_at",
            "updated_at",
        ]


class EducationInstitutionAssessmentSerializer(serializers.ModelSerializer):
    course_id = serializers.UUIDField(source="course.id", read_only=True)
    lesson_id = serializers.UUIDField(source="lesson.id", read_only=True)
    class_session_id = serializers.UUIDField(source="class_session.id", read_only=True)
    questions = EducationInstitutionAssessmentQuestionSerializer(many=True, read_only=True)

    class Meta:
        model = EducationInstitutionAssessment
        fields = [
            "id",
            "course_id",
            "lesson_id",
            "class_session_id",
            "title",
            "summary",
            "instructions",
            "assessment_type",
            "status",
            "starts_at",
            "ends_at",
            "duration_minutes",
            "max_attempts",
            "passing_score_percent",
            "total_points",
            "metadata",
            "settings",
            "questions",
            "created_at",
            "updated_at",
        ]


class EducationInstitutionAssessmentResponseOptionSerializer(serializers.ModelSerializer):
    option_id = serializers.UUIDField(source="option.id", read_only=True)

    class Meta:
        model = EducationInstitutionAssessmentResponseOption
        fields = ["id", "option_id", "created_at"]


class EducationInstitutionAssessmentResponseSerializer(serializers.ModelSerializer):
    question_id = serializers.UUIDField(source="question.id", read_only=True)
    selected_options = EducationInstitutionAssessmentResponseOptionSerializer(many=True, read_only=True)

    class Meta:
        model = EducationInstitutionAssessmentResponse
        fields = [
            "id",
            "question_id",
            "answer_text",
            "is_correct",
            "earned_points",
            "grader_feedback",
            "metadata",
            "selected_options",
            "created_at",
            "updated_at",
        ]


class EducationInstitutionAssessmentSubmissionSerializer(serializers.ModelSerializer):
    assessment_id = serializers.UUIDField(source="assessment.id", read_only=True)
    user_id = serializers.UUIDField(source="user.id", read_only=True)
    grader_id = serializers.UUIDField(source="grader.id", read_only=True, allow_null=True)
    responses = EducationInstitutionAssessmentResponseSerializer(many=True, read_only=True)

    class Meta:
        model = EducationInstitutionAssessmentSubmission
        fields = [
            "id",
            "assessment_id",
            "user_id",
            "attempt_number",
            "status",
            "earned_points",
            "score_percent",
            "grader_id",
            "grader_feedback",
            "metadata",
            "responses",
            "started_at",
            "submitted_at",
            "graded_at",
            "created_at",
            "updated_at",
        ]


class EducationInstitutionEventSerializer(serializers.ModelSerializer):
    program_id = serializers.UUIDField(source="program.id", read_only=True, allow_null=True)
    course_id = serializers.UUIDField(source="course.id", read_only=True, allow_null=True)
    class_session_id = serializers.UUIDField(source="class_session.id", read_only=True, allow_null=True)

    class Meta:
        model = EducationInstitutionEvent
        fields = [
            "id",
            "program_id",
            "course_id",
            "class_session_id",
            "event_type",
            "title",
            "summary",
            "description",
            "starts_at",
            "ends_at",
            "timezone_name",
            "delivery_mode",
            "location_text",
            "meeting_url",
            "seat_limit",
            "status",
            "metadata",
            "created_at",
            "updated_at",
        ]


class EducationInstitutionBroadcastSerializer(serializers.ModelSerializer):
    program_id = serializers.UUIDField(source="program.id", read_only=True, allow_null=True)
    course_id = serializers.UUIDField(source="course.id", read_only=True)
    lesson_id = serializers.UUIDField(source="lesson.id", read_only=True)
    class_session_id = serializers.UUIDField(source="class_session.id", read_only=True)
    event_id = serializers.UUIDField(source="event.id", read_only=True)
    institution_id = serializers.UUIDField(source="institution.id", read_only=True)
    created_by_id = serializers.UUIDField(source="created_by.id", read_only=True)
    membership_policy = serializers.CharField(source="institution.membership_policy", read_only=True)

    class Meta:
        model = EducationInstitutionBroadcast
        fields = [
            "id",
            "institution_id",
            "created_by_id",
            "broadcast_kind",
            "program_id",
            "course_id",
            "lesson_id",
            "class_session_id",
            "event_id",
            "title",
            "summary",
            "description",
            "cover_image_url",
            "starts_at",
            "ends_at",
            "timezone_name",
            "seat_limit",
            "booking_enabled",
            "price_amount",
            "price_currency",
            "membership_policy",
            "status",
            "published_at",
            "expires_at",
            "metadata",
            "created_at",
            "updated_at",
        ]


class EducationInstitutionEnrollmentSerializer(serializers.ModelSerializer):
    broadcast_id = serializers.UUIDField(source="broadcast.id", read_only=True)
    program_id = serializers.UUIDField(source="program.id", read_only=True, allow_null=True)
    user_id = serializers.UUIDField(source="user.id", read_only=True)
    course_id = serializers.UUIDField(source="course.id", read_only=True, allow_null=True)
    lesson_id = serializers.UUIDField(source="lesson.id", read_only=True, allow_null=True)
    class_session_id = serializers.UUIDField(source="class_session.id", read_only=True, allow_null=True)
    event_id = serializers.UUIDField(source="event.id", read_only=True, allow_null=True)

    class Meta:
        model = EducationInstitutionEnrollment
        fields = [
            "id",
            "broadcast_id",
            "program_id",
            "user_id",
            "course_id",
            "lesson_id",
            "class_session_id",
            "event_id",
            "status",
            "enrolled_at",
            "completed_at",
            "metadata",
            "created_at",
            "updated_at",
        ]


class EducationInstitutionBookingSerializer(serializers.ModelSerializer):
    broadcast_id = serializers.UUIDField(source="broadcast.id", read_only=True)
    program_id = serializers.UUIDField(source="program.id", read_only=True, allow_null=True)
    course_id = serializers.UUIDField(source="course.id", read_only=True, allow_null=True)
    class_session_id = serializers.UUIDField(source="class_session.id", read_only=True, allow_null=True)
    event_id = serializers.UUIDField(source="event.id", read_only=True, allow_null=True)
    user_id = serializers.UUIDField(source="user.id", read_only=True)
    wallet_transaction_id = serializers.UUIDField(source="wallet_transaction.id", read_only=True, allow_null=True)
    provider_credit_transaction_id = serializers.UUIDField(
        source="provider_credit_transaction.id",
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = EducationInstitutionBooking
        fields = [
            "id",
            "broadcast_id",
            "program_id",
            "course_id",
            "class_session_id",
            "event_id",
            "user_id",
            "status",
            "seat_count",
            "amount_cents",
            "currency",
            "payment_method",
            "wallet_transaction_id",
            "provider_credit_transaction_id",
            "reserved_at",
            "confirmed_at",
            "provider_completed_at",
            "payer_satisfied_at",
            "satisfaction_deadline",
            "metadata",
            "created_at",
            "updated_at",
        ]


class EducationProfileCourseSerializer(serializers.ModelSerializer):
    class Meta:
        model = EducationProfileCourse
        fields = ["id", "title", "summary", "metadata", "created_at"]


class EducationProfileModuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = EducationProfileModule
        fields = ["id", "title", "summary", "resource_url", "created_at"]


class EducationProfileRoleAssignmentSerializer(serializers.ModelSerializer):
    user_id = serializers.UUIDField(source="user.id")
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = EducationProfileRoleAssignment
        fields = ["id", "user_id", "display_name", "created_at"]

    def get_display_name(self, obj: EducationProfileRoleAssignment):
        return obj.user.display_name if obj.user else None


class EducationProfileRoleSerializer(serializers.ModelSerializer):
    assignments = EducationProfileRoleAssignmentSerializer(many=True, read_only=True)

    class Meta:
        model = EducationProfileRole
        fields = ["id", "name", "permissions", "assignments", "created_at"]


class EducationProfileSerializer(serializers.ModelSerializer):
    courses = EducationProfileCourseSerializer(many=True, read_only=True)
    modules = EducationProfileModuleSerializer(many=True, read_only=True)
    roles = EducationProfileRoleSerializer(many=True, read_only=True)
    owner_id = serializers.UUIDField(source="user.id")

    class Meta:
        model = EducationProfile
        fields = [
            "id",
            "owner_id",
            "name",
            "description",
            "profile_type",
            "is_default",
            "courses",
            "modules",
            "roles",
            "metadata",
            "created_at",
            "updated_at",
        ]
