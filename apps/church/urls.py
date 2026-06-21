from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    ChurchGivingViewSet,
    GivingStatsView,
    TithePledgeViewSet,
    OfferingCampaignViewSet,
    TitheStatementView,
    ChurchMembershipViewSet,
    AttendanceRecordViewSet,
    ChurchLifeEventViewSet,
    MemberBirthdayAnniversaryViewSet,
    SmallGroupViewSet,
    SmallGroupMembershipViewSet,
    SmallGroupAttendanceViewSet,
    DiscipleshipJourneyViewSet,
    SpiritualGiftsAssessmentViewSet,
    AccountabilityPartnerViewSet,
    PrayerRequestViewSet,
    PrayerWallView,
    FastingRecordViewSet,
    PrayerChainSlotViewSet,
    SongViewSet,
    SetListViewSet,
    MinistryDepartmentViewSet,
    VolunteerSignupViewSet,
    OutreachCampaignViewSet,
    EvangelismRecordViewSet,
)

router = DefaultRouter()

# Giving
router.register(r"giving/givings", ChurchGivingViewSet, basename="church-giving")
router.register(r"giving/pledges", TithePledgeViewSet, basename="tithe-pledge")
router.register(r"giving/campaigns", OfferingCampaignViewSet, basename="offering-campaign")

# Membership
router.register(r"membership", ChurchMembershipViewSet, basename="church-membership")
router.register(r"attendance", AttendanceRecordViewSet, basename="attendance-record")
router.register(r"life-events", ChurchLifeEventViewSet, basename="church-life-event")
router.register(r"birthdays", MemberBirthdayAnniversaryViewSet, basename="member-birthday")

# Small Groups
router.register(r"groups", SmallGroupViewSet, basename="small-group")
router.register(r"groups-membership", SmallGroupMembershipViewSet, basename="small-group-membership")
router.register(r"groups-attendance", SmallGroupAttendanceViewSet, basename="small-group-attendance")

# Discipleship
router.register(r"discipleship/journeys", DiscipleshipJourneyViewSet, basename="discipleship-journey")
router.register(r"discipleship/gifts", SpiritualGiftsAssessmentViewSet, basename="spiritual-gifts")
router.register(r"discipleship/accountability", AccountabilityPartnerViewSet, basename="accountability-partner")

# Prayer
router.register(r"prayer/requests", PrayerRequestViewSet, basename="prayer-request")
router.register(r"prayer/fasting", FastingRecordViewSet, basename="fasting-record")
router.register(r"prayer/chain", PrayerChainSlotViewSet, basename="prayer-chain-slot")

# Worship
router.register(r"worship/songs", SongViewSet, basename="song")
router.register(r"worship/setlists", SetListViewSet, basename="setlist")

# Ministry & Outreach
router.register(r"ministry/departments", MinistryDepartmentViewSet, basename="ministry-department")
router.register(r"ministry/volunteers", VolunteerSignupViewSet, basename="volunteer-signup")
router.register(r"outreach/campaigns", OutreachCampaignViewSet, basename="outreach-campaign")
router.register(r"outreach/evangelism", EvangelismRecordViewSet, basename="evangelism-record")

urlpatterns = [
    path("", include(router.urls)),
    # Custom non-router endpoints
    path("giving/stats/", GivingStatsView.as_view(), name="giving-stats"),
    path("giving/statement/", TitheStatementView.as_view(), name="tithe-statement"),
    path("prayer/wall/", PrayerWallView.as_view(), name="prayer-wall"),
]
