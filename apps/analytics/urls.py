from rest_framework.routers import DefaultRouter
from .views import (
    MetricViewSet,
    EventStreamViewSet,
    DashboardViewSet,
    AppSettingViewSet,
    FeatureFlagViewSet,
    AlertViewSet,
    EngagementScoreViewSet,
    ClinicalAnalyticsReportViewSet,
    RiskStratificationViewSet,
    OutcomeBenchmarkViewSet,
    PatientSatisfactionScoreViewSet,
    OutreachCampaignViewSet,
    WellnessChallengeViewSet,
    HabitTrackingEntryViewSet,
)

router = DefaultRouter()
router.register('metrics', MetricViewSet, basename='metric')
router.register('events', EventStreamViewSet, basename='eventstream')
router.register('dashboards', DashboardViewSet, basename='dashboard')
router.register('settings', AppSettingViewSet, basename='appsetting')
router.register('flags', FeatureFlagViewSet, basename='featureflag')
router.register('alerts', AlertViewSet, basename='alert')
router.register('engagement', EngagementScoreViewSet, basename='engagement')
router.register('clinical-reports', ClinicalAnalyticsReportViewSet, basename='clinicalreport')
router.register('risk-stratification', RiskStratificationViewSet, basename='risk')
router.register('outcome-benchmarks', OutcomeBenchmarkViewSet, basename='outcomebenchmark')
router.register('patient-satisfaction', PatientSatisfactionScoreViewSet, basename='patientsatisfaction')
router.register('outreach-campaigns', OutreachCampaignViewSet, basename='outreachcampaign')
router.register('wellness-challenges', WellnessChallengeViewSet, basename='wellnesschallenge')
router.register('habit-entries', HabitTrackingEntryViewSet, basename='habitentry')

urlpatterns = router.urls
