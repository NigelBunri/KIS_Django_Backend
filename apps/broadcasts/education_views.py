import secrets

from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .education_models import (
    AssignmentSubmission,
    ClassAssignment,
    ClassroomStatus,
    DigitalBadge,
    LiveClassroom,
    Scholarship,
    ScholarshipApplication,
    StudentBadge,
    StudentProgressReport,
)
from .education_serializers import (
    AssignmentSubmissionSerializer,
    ClassAssignmentSerializer,
    DigitalBadgeSerializer,
    LiveClassroomSerializer,
    ScholarshipApplicationSerializer,
    ScholarshipSerializer,
    StudentBadgeSerializer,
    StudentProgressReportSerializer,
)


@extend_schema(tags=["Education – Classrooms"])
class LiveClassroomViewSet(viewsets.ModelViewSet):
    """CRUD for live classrooms plus start/end lifecycle actions."""

    queryset = LiveClassroom.objects.select_related("host").all()
    serializer_class = LiveClassroomSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["channel_id", "status"]
    search_fields = ["title"]
    ordering_fields = ["scheduled_at", "created_at"]

    @extend_schema(
        summary="Start a live classroom",
        responses={200: LiveClassroomSerializer},
    )
    @action(detail=True, methods=["post"], url_path="start")
    def start(self, request, pk=None):
        classroom = self.get_object()
        classroom.status = ClassroomStatus.LIVE
        classroom.save(update_fields=["status", "updated_at"])
        serializer = self.get_serializer(classroom)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        summary="End a live classroom",
        responses={200: LiveClassroomSerializer},
    )
    @action(detail=True, methods=["post"], url_path="end")
    def end(self, request, pk=None):
        classroom = self.get_object()
        classroom.status = ClassroomStatus.ENDED
        classroom.save(update_fields=["status", "updated_at"])
        serializer = self.get_serializer(classroom)
        return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(tags=["Education – Assignments"])
class ClassAssignmentViewSet(viewsets.ModelViewSet):
    """CRUD for class assignments."""

    queryset = ClassAssignment.objects.select_related("classroom").all()
    serializer_class = ClassAssignmentSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["classroom"]
    ordering_fields = ["due_date", "created_at"]


@extend_schema(tags=["Education – Submissions"])
class AssignmentSubmissionViewSet(viewsets.ModelViewSet):
    """CRUD for assignment submissions, filterable by assignment and student."""

    serializer_class = AssignmentSubmissionSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["assignment", "student", "status"]
    ordering_fields = ["submitted_at", "created_at"]

    def get_queryset(self):
        return AssignmentSubmission.objects.select_related(
            "assignment", "student"
        ).all()


@extend_schema(tags=["Education – Scholarships"])
class ScholarshipViewSet(viewsets.ModelViewSet):
    """CRUD for scholarships. List endpoint is public; mutations require auth."""

    serializer_class = ScholarshipSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["country", "is_active", "currency"]
    search_fields = ["title", "sponsor"]
    ordering_fields = ["deadline", "amount", "created_at"]

    def get_queryset(self):
        return Scholarship.objects.select_related("created_by").all()

    def get_permissions(self):
        if self.action == "list":
            return [AllowAny()]
        return [IsAuthenticated()]


@extend_schema(tags=["Education – Scholarship Applications"])
class ScholarshipApplicationViewSet(viewsets.ModelViewSet):
    """CRUD for scholarship applications."""

    serializer_class = ScholarshipApplicationSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["scholarship", "applicant", "status"]
    ordering_fields = ["created_at"]

    def get_queryset(self):
        return ScholarshipApplication.objects.select_related(
            "scholarship", "applicant"
        ).all()


@extend_schema(tags=["Education – Badges"])
class DigitalBadgeViewSet(viewsets.ModelViewSet):
    """CRUD for digital badge definitions."""

    queryset = DigitalBadge.objects.all()
    serializer_class = DigitalBadgeSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["is_active"]
    search_fields = ["title", "issuer"]


@extend_schema(tags=["Education – Student Badges"])
class StudentBadgeViewSet(viewsets.ModelViewSet):
    """List badges for students and award new badges (staff only)."""

    serializer_class = StudentBadgeSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["student", "badge"]
    ordering_fields = ["issued_at"]

    def get_queryset(self):
        return StudentBadge.objects.select_related("badge", "student").all()

    @extend_schema(
        summary="Award a badge to a student (staff only)",
        responses={201: StudentBadgeSerializer},
    )
    @action(detail=False, methods=["post"], url_path=r"(?P<badge_id>[^/.]+)/award")
    def award(self, request, badge_id=None):
        if not (request.user.is_staff or request.user.is_superuser):
            return Response(
                {"detail": "Staff access required."},
                status=status.HTTP_403_FORBIDDEN,
            )
        badge = get_object_or_404(DigitalBadge, pk=badge_id)
        student_id = request.data.get("student")
        if not student_id:
            return Response(
                {"detail": "student field is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from apps.accounts.models import User

        student = get_object_or_404(User, pk=student_id)
        verif_hash = secrets.token_hex(32)
        student_badge, created = StudentBadge.objects.get_or_create(
            badge=badge,
            student=student,
            defaults={"verif_hash": verif_hash},
        )
        if not created:
            return Response(
                {"detail": "Badge already awarded to this student."},
                status=status.HTTP_409_CONFLICT,
            )
        serializer = self.get_serializer(student_badge)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["Education – Progress Reports"])
class StudentProgressReportViewSet(viewsets.ModelViewSet):
    """CRUD for student progress reports."""

    serializer_class = StudentProgressReportSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["student", "classroom"]
    ordering_fields = ["created_at", "last_active"]

    def get_queryset(self):
        return StudentProgressReport.objects.select_related(
            "student", "classroom"
        ).all()


@extend_schema(
    tags=["Education – Transcript"],
    summary="Retrieve the current user's transcript",
    description=(
        "Returns all StudentProgressReport records for the authenticated user "
        "grouped as a transcript payload."
    ),
    responses={200: StudentProgressReportSerializer(many=True)},
)
class TranscriptView(APIView):
    """GET /education/transcript/ — returns all progress reports for the current user."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        reports = StudentProgressReport.objects.filter(
            student=request.user
        ).select_related("classroom").order_by("-created_at")
        serializer = StudentProgressReportSerializer(reports, many=True)
        return Response(
            {"transcript": serializer.data},
            status=status.HTTP_200_OK,
        )


@extend_schema(
    tags=["Education – Certificates"],
    summary="Generate a mock AI certificate",
    description=(
        "Generates a mock certificate JSON for the authenticated user "
        "with a SHA-256 verification hash scaffold (secrets.token_hex(32))."
    ),
    responses={201: {"type": "object"}},
)
class AICertificateView(APIView):
    """POST /education/certificates/generate/ — returns mock cert JSON with verif_hash."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        verif_hash = secrets.token_hex(32)
        certificate = {
            "recipient": {
                "id": str(request.user.id),
                "email": request.user.email,
                "name": getattr(request.user, "get_full_name", lambda: str(request.user))(),
            },
            "issued_at": timezone.now().isoformat(),
            "verif_hash": verif_hash,
            "certificate_url": f"https://kis.app/certificates/{verif_hash}",
            "issuer": "KIS Education Platform",
            "note": "This is a scaffold certificate. Blockchain anchoring pending.",
        }
        return Response(certificate, status=status.HTTP_201_CREATED)
