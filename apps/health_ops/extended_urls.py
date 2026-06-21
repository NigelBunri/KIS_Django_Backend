from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .extended_views import (
    AddictionRecoveryGroupViewSet,
    AISymptomsCheckerView,
    BabyMilestoneViewSet,
    BloodTypeRegistryViewSet,
    ConsultReviewViewSet,
    CrisisHotlineView,
    DoctorDirectoryView,
    EMedicationViewSet,
    EmergencyAlertViewSet,
    HealthGoalViewSet,
    MentalHealthJournalViewSet,
    MentalHealthSessionViewSet,
    MoodEntryViewSet,
    PregnancyTrackerViewSet,
    RecoveryMilestoneViewSet,
    SOSCreateView,
    TelemedicineConsultViewSet,
)

router = DefaultRouter()
router.register(r"consults", TelemedicineConsultViewSet, basename="tele-consults")
router.register(r"consult-reviews", ConsultReviewViewSet, basename="consult-reviews")
router.register(r"mental-sessions", MentalHealthSessionViewSet, basename="mental-sessions")
router.register(r"mood", MoodEntryViewSet, basename="mood")
router.register(r"mental-journals", MentalHealthJournalViewSet, basename="mental-journals")
router.register(r"recovery-groups", AddictionRecoveryGroupViewSet, basename="recovery-groups")
router.register(r"recovery-milestones", RecoveryMilestoneViewSet, basename="recovery-milestones")
router.register(r"pregnancy", PregnancyTrackerViewSet, basename="pregnancy")
router.register(r"baby-milestones", BabyMilestoneViewSet, basename="baby-milestones")
router.register(r"blood-registry", BloodTypeRegistryViewSet, basename="blood-registry")
router.register(r"medications", EMedicationViewSet, basename="medications")
router.register(r"health-goals", HealthGoalViewSet, basename="health-goals")
router.register(r"emergency-alerts", EmergencyAlertViewSet, basename="emergency-alerts")

urlpatterns = [
    path("", include(router.urls)),
    path("doctors/", DoctorDirectoryView.as_view(), name="health-doctors"),
    path("symptoms/check/", AISymptomsCheckerView.as_view(), name="symptoms-check"),
    path("crisis/hotlines/", CrisisHotlineView.as_view(), name="crisis-hotlines"),
    path("emergency/sos/", SOSCreateView.as_view(), name="emergency-sos"),
]
