from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    PetitionViewSet,
    CivicPollViewSet,
    LegalAidEntryViewSet,
    LegalDocumentTemplateViewSet,
    DiasporaCommunityViewSet,
    NGOProfileViewSet,
    GrantApplicationViewSet,
    ComplianceDeadlineViewSet,
    WhistleblowerSubmitView,
    WhistleblowerStatusView,
    BoardElectionViewSet,
    BoardResolutionViewSet,
)

app_name = "government"

router = DefaultRouter()
router.register(r"government/petitions", PetitionViewSet, basename="petition")
router.register(r"government/polls", CivicPollViewSet, basename="civic-poll")
router.register(r"government/legal-aid", LegalAidEntryViewSet, basename="legal-aid")
router.register(r"government/legal-documents", LegalDocumentTemplateViewSet, basename="legal-doc")
router.register(r"government/diaspora", DiasporaCommunityViewSet, basename="diaspora")
router.register(r"government/ngos", NGOProfileViewSet, basename="ngo")
router.register(r"government/grants", GrantApplicationViewSet, basename="grant")
router.register(r"government/compliance", ComplianceDeadlineViewSet, basename="compliance")
router.register(r"government/elections", BoardElectionViewSet, basename="election")
router.register(r"government/resolutions", BoardResolutionViewSet, basename="resolution")

urlpatterns = router.urls + [
    path(
        "government/whistleblower/submit/",
        WhistleblowerSubmitView.as_view(),
        name="whistleblower-submit",
    ),
    path(
        "government/whistleblower/status/",
        WhistleblowerStatusView.as_view(),
        name="whistleblower-status",
    ),
]
