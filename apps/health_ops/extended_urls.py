from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .extended_views import (
    AddictionRecoveryGroupViewSet,
    AISymptomsCheckerView,
    AllergyViewSet,
    BabyMilestoneViewSet,
    BloodTypeRegistryViewSet,
    ConditionViewSet,
    ConsultReviewViewSet,
    CrisisHotlineView,
    EMedicationViewSet,
    EmergencyAlertViewSet,
    HealthGoalViewSet,
    ImmunizationViewSet,
    MentalHealthJournalViewSet,
    MentalHealthSessionViewSet,
    MoodEntryViewSet,
    MyPractitionerProfileView,
    PractitionerDirectoryView,
    PractitionerVerificationReviewView,
    PractitionerVerificationStartView,
    PractitionerVerificationStatusView,
    PregnancyTrackerViewSet,
    RecoveryMilestoneViewSet,
    SOSCreateView,
    TelemedicineConsultViewSet,
)

router = DefaultRouter()
router.register(r"conditions", ConditionViewSet, basename="conditions")
router.register(r"allergies", AllergyViewSet, basename="allergies")
router.register(r"immunizations", ImmunizationViewSet, basename="immunizations")
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
    path("doctors/", PractitionerDirectoryView.as_view(), name="health-doctors"),
    path("symptoms/check/", AISymptomsCheckerView.as_view(), name="symptoms-check"),
    path("crisis/hotlines/", CrisisHotlineView.as_view(), name="crisis-hotlines"),
    path("emergency/sos/", SOSCreateView.as_view(), name="emergency-sos"),
    path("practitioners/me/", MyPractitionerProfileView.as_view(), name="health-practitioner-me"),
    path(
        "practitioners/<uuid:practitioner_id>/verification/status/",
        PractitionerVerificationStatusView.as_view(),
        name="health-practitioner-verification-status",
    ),
    path(
        "practitioners/<uuid:practitioner_id>/verification/start/",
        PractitionerVerificationStartView.as_view(),
        name="health-practitioner-verification-start",
    ),
    path(
        "practitioners/<uuid:practitioner_id>/verification/<uuid:case_id>/review/",
        PractitionerVerificationReviewView.as_view(),
        name="health-practitioner-verification-review",
    ),
]
