from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .education_views import (
    AICertificateView,
    AssignmentSubmissionViewSet,
    ClassAssignmentViewSet,
    DigitalBadgeViewSet,
    LiveClassroomViewSet,
    ScholarshipApplicationViewSet,
    ScholarshipViewSet,
    StudentBadgeViewSet,
    StudentProgressReportViewSet,
    TranscriptView,
)

router = DefaultRouter()
router.register(r"classrooms", LiveClassroomViewSet, basename="edu-classroom")
router.register(r"assignments", ClassAssignmentViewSet, basename="edu-assignment")
router.register(r"submissions", AssignmentSubmissionViewSet, basename="edu-submission")
router.register(r"scholarships", ScholarshipViewSet, basename="edu-scholarship")
router.register(r"scholarship-applications", ScholarshipApplicationViewSet, basename="edu-scholarship-application")
router.register(r"badges", DigitalBadgeViewSet, basename="edu-badge")
router.register(r"student-badges", StudentBadgeViewSet, basename="edu-student-badge")
router.register(r"progress-reports", StudentProgressReportViewSet, basename="edu-progress-report")

urlpatterns = [
    path("", include(router.urls)),
    path("transcript/", TranscriptView.as_view(), name="edu-transcript"),
    path("certificates/generate/", AICertificateView.as_view(), name="edu-certificate-generate"),
]
