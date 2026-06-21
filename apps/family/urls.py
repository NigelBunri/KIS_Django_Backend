# apps/family/urls.py
from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    FamilyAccountViewSet,
    FamilyMemberViewSet,
    ParentalControlViewSet,
    SOSAlertView,
    FamilyEventViewSet,
    FamilyPhotoAlbumViewSet,
    FamilyPhotoViewSet,
    MemorialPageViewSet,
    MilestoneRecordViewSet,
    TimeCapsuleViewSet,
    FamilyPrayerItemViewSet,
    FamilyNoticeBoardViewSet,
    GriefSupportGroupViewSet,
)

app_name = "family"

router = DefaultRouter()
router.register(r"accounts", FamilyAccountViewSet, basename="family-account")
router.register(r"members", FamilyMemberViewSet, basename="family-member")
router.register(r"parental-controls", ParentalControlViewSet, basename="parental-control")
router.register(r"events", FamilyEventViewSet, basename="family-event")
router.register(r"albums", FamilyPhotoAlbumViewSet, basename="family-album")
router.register(r"photos", FamilyPhotoViewSet, basename="family-photo")
router.register(r"memorials", MemorialPageViewSet, basename="memorial-page")
router.register(r"milestones", MilestoneRecordViewSet, basename="milestone-record")
router.register(r"capsules", TimeCapsuleViewSet, basename="time-capsule")
router.register(r"prayers", FamilyPrayerItemViewSet, basename="family-prayer")
router.register(r"notices", FamilyNoticeBoardViewSet, basename="family-notice")
router.register(r"grief-groups", GriefSupportGroupViewSet, basename="grief-group")

urlpatterns = [
    path("sos/", SOSAlertView.as_view(), name="sos-alert"),
] + router.urls
