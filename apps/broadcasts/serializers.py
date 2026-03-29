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
