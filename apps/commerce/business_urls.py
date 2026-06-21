"""
URL configuration for Business & Economy endpoints.

Mount in the main urls.py as:
    path("api/v1/business/", include("apps.commerce.business_urls"))
"""

from django.urls import path
from rest_framework.routers import DefaultRouter

from .business_views import (
    CrowdfundCampaignViewSet,
    SavingsGroupViewSet,
    JobListingViewSet,
    JobApplicationViewSet,
    BusinessMentorshipViewSet,
    CoWorkingSpaceViewSet,
    KingdomCertificationViewSet,
    ImpactReportViewSet,
    BusinessDashboardView,
)

router = DefaultRouter()
router.register(r"crowdfund", CrowdfundCampaignViewSet, basename="crowdfund-campaign")
router.register(r"savings", SavingsGroupViewSet, basename="savings-group")
router.register(r"jobs", JobListingViewSet, basename="job-listing")
router.register(r"job-applications", JobApplicationViewSet, basename="job-application")
router.register(r"mentorship", BusinessMentorshipViewSet, basename="business-mentorship")
router.register(r"coworking", CoWorkingSpaceViewSet, basename="coworking-space")
router.register(r"certifications", KingdomCertificationViewSet, basename="kingdom-certification")
router.register(r"impact", ImpactReportViewSet, basename="impact-report")

urlpatterns = [
    path("dashboard/", BusinessDashboardView.as_view(), name="business-dashboard"),
    *router.urls,
]
