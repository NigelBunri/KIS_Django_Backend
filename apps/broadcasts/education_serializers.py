from rest_framework import serializers

from .education_models import (
    AssignmentSubmission,
    ClassAssignment,
    DigitalBadge,
    LiveClassroom,
    Scholarship,
    ScholarshipApplication,
    StudentBadge,
    StudentProgressReport,
)


class LiveClassroomSerializer(serializers.ModelSerializer):
    class Meta:
        model = LiveClassroom
        fields = [
            "id",
            "channel_id",
            "title",
            "host",
            "scheduled_at",
            "status",
            "meeting_url",
            "recording_url",
            "whiteboard_url",
            "max_participants",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class ClassAssignmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClassAssignment
        fields = [
            "id",
            "classroom",
            "title",
            "description",
            "due_date",
            "max_score",
            "rubric",
            "file_urls",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class AssignmentSubmissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssignmentSubmission
        fields = [
            "id",
            "assignment",
            "student",
            "file_urls",
            "text_response",
            "score",
            "feedback",
            "status",
            "submitted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "submitted_at", "created_at", "updated_at"]


class ScholarshipSerializer(serializers.ModelSerializer):
    class Meta:
        model = Scholarship
        fields = [
            "id",
            "title",
            "sponsor",
            "amount",
            "currency",
            "deadline",
            "requirements",
            "application_url",
            "country",
            "is_active",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class ScholarshipApplicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScholarshipApplication
        fields = [
            "id",
            "scholarship",
            "applicant",
            "statement",
            "status",
            "documents",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class DigitalBadgeSerializer(serializers.ModelSerializer):
    class Meta:
        model = DigitalBadge
        fields = [
            "id",
            "title",
            "description",
            "criteria",
            "image_url",
            "issuer",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class StudentBadgeSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudentBadge
        fields = [
            "id",
            "badge",
            "student",
            "issued_at",
            "verif_hash",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "issued_at", "verif_hash", "created_at", "updated_at"]


class StudentProgressReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudentProgressReport
        fields = [
            "id",
            "student",
            "classroom",
            "attendance_count",
            "assignments_submitted",
            "average_score",
            "last_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
