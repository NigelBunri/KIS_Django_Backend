from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db.models import Q
from .models import (
    Metric,
    EventStream,
    Dashboard,
    AppSetting,
    FeatureFlag,
    Alert,
    EngagementScore,
    ClinicalAnalyticsReport,
    RiskStratification,
    OutcomeBenchmark,
    PatientSatisfactionScore,
    OutreachCampaign,
    WellnessChallenge,
    HabitTrackingEntry,
)
from .serializers import (
    MetricSerializer,
    EventStreamSerializer,
    DashboardSerializer,
    AppSettingSerializer,
    FeatureFlagSerializer,
    AlertSerializer,
    EngagementScoreSerializer,
    ClinicalAnalyticsReportSerializer,
    RiskStratificationSerializer,
    OutcomeBenchmarkSerializer,
    PatientSatisfactionScoreSerializer,
    OutreachCampaignSerializer,
    WellnessChallengeSerializer,
    HabitTrackingEntrySerializer,
)
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import OrderingFilter, SearchFilter
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiResponse, OpenApiParameter, OpenApiTypes, OpenApiExample
from .tasks import (
    compute_predictive_metrics,
    process_event_stream,
    compute_risk_stratification,
    generate_clinical_analytics_report,
)


class StaffOnlyModelViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdminUser]


class StaffOnlyReadOnlyModelViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAdminUser]


class OwnedHealthcareQuerysetMixin:
    def scope_healthcare_queryset(self, qs):
        user = self.request.user
        if user.is_staff or user.is_superuser:
            return qs
        return qs.filter(self.owner_filter(user)).distinct()

    def get_queryset(self):
        return self.scope_healthcare_queryset(super().get_queryset())


def owned_organization_filter(user):
    return Q(organization__owner=user)


def owned_profile_filter(user):
    return Q(profile__organization__owner=user)


def owned_patient_filter(user):
    return Q(patient__organization__owner=user)


@extend_schema_view(
    list=extend_schema(summary="List Metrics", responses={200: MetricSerializer(many=True)}, tags=["Metrics"]),
    retrieve=extend_schema(summary="Retrieve Metric", responses={200: MetricSerializer}, tags=["Metrics"]),
    create=extend_schema(summary="Create Metric", request=MetricSerializer, responses={201: MetricSerializer}, tags=["Metrics"]),
    update=extend_schema(summary="Update Metric", request=MetricSerializer, responses={200: MetricSerializer}, tags=["Metrics"]),
    destroy=extend_schema(summary="Delete Metric", responses={204: OpenApiResponse(description="deleted")}, tags=["Metrics"]),
)
class MetricViewSet(StaffOnlyModelViewSet):
    queryset = Metric.objects.all()
    serializer_class = MetricSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['name','kind','source']
    search_fields = ['name']
    ordering_fields = ['captured_at','value']

    @extend_schema(summary="Trigger predictive computation for a metric", request=None, responses={200: OpenApiResponse(description="prediction queued")}, tags=["Metrics"])
    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated])
    def predict(self, request, pk=None):
        metric = self.get_object()
        compute_predictive_metrics.delay(str(metric.id))
        return Response({'detail': 'prediction queued'})

@extend_schema_view(
    list=extend_schema(summary="List Event Stream", responses={200: EventStreamSerializer(many=True)}, tags=["EventStream"]),
    create=extend_schema(summary="Ingest Event", request=EventStreamSerializer, responses={201: EventStreamSerializer}, tags=["EventStream"]),
)
class EventStreamViewSet(StaffOnlyModelViewSet):
    queryset = EventStream.objects.all()
    serializer_class = EventStreamSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ev = serializer.save()
        # enqueue processing
        process_event_stream.delay(str(ev.id))
        return Response(serializer.data, status=status.HTTP_201_CREATED)

@extend_schema_view(
    list=extend_schema(summary="List Dashboards", responses={200: DashboardSerializer(many=True)}, tags=["Dashboards"]),
    retrieve=extend_schema(summary="Retrieve Dashboard", responses={200: DashboardSerializer}, tags=["Dashboards"]),
    create=extend_schema(summary="Create Dashboard", request=DashboardSerializer, responses={201: DashboardSerializer}, tags=["Dashboards"]),
)
class DashboardViewSet(StaffOnlyModelViewSet):
    queryset = Dashboard.objects.all()
    serializer_class = DashboardSerializer

@extend_schema_view(
    list=extend_schema(summary="List App Settings", responses={200: AppSettingSerializer(many=True)}, tags=["Settings"]),
    retrieve=extend_schema(summary="Retrieve App Setting", responses={200: AppSettingSerializer}, tags=["Settings"]),
    create=extend_schema(summary="Create App Setting", request=AppSettingSerializer, responses={201: AppSettingSerializer}, tags=["Settings"]),
)
class AppSettingViewSet(StaffOnlyModelViewSet):
    queryset = AppSetting.objects.all()
    serializer_class = AppSettingSerializer

@extend_schema_view(
    list=extend_schema(summary="List Feature Flags", responses={200: FeatureFlagSerializer(many=True)}, tags=["FeatureFlags"]),
    retrieve=extend_schema(summary="Retrieve Feature Flag", responses={200: FeatureFlagSerializer}, tags=["FeatureFlags"]),
    create=extend_schema(summary="Create Feature Flag", request=FeatureFlagSerializer, responses={201: FeatureFlagSerializer}, tags=["FeatureFlags"]),
)
class FeatureFlagViewSet(StaffOnlyModelViewSet):
    queryset = FeatureFlag.objects.all()
    serializer_class = FeatureFlagSerializer

    @extend_schema(summary="Evaluate a feature flag for a target", request=None, responses={200: OpenApiResponse(description="flag evaluation result")}, tags=["FeatureFlags"])
    @action(detail=True, methods=['post'], url_path='evaluate', permission_classes=[IsAuthenticated])
    def evaluate(self, request, pk=None):
        flag = self.get_object()
        # naive evaluation stub
        target = request.data.get('target')
        enabled = flag.enabled
        # more complex audience checks would go here
        return Response({'key': flag.key, 'enabled': enabled, 'target': target})

@extend_schema_view(
    list=extend_schema(summary="List Alerts", responses={200: AlertSerializer(many=True)}, tags=["Alerts"]),
    retrieve=extend_schema(summary="Retrieve Alert", responses={200: AlertSerializer}, tags=["Alerts"]),
    create=extend_schema(summary="Create Alert", request=AlertSerializer, responses={201: AlertSerializer}, tags=["Alerts"]),
)
class AlertViewSet(StaffOnlyModelViewSet):
    queryset = Alert.objects.all()
    serializer_class = AlertSerializer

    @extend_schema(summary="Acknowledge an alert", request=None, responses={200: OpenApiResponse(description="acknowledged")}, tags=["Alerts"])
    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated])
    def acknowledge(self, request, pk=None):
        alert = self.get_object()
        alert.acknowledged_by = request.user.id
        alert.triggered_at = alert.triggered_at or timezone.now()
        alert.save()
        return Response({'detail': 'acknowledged'})

@extend_schema_view(
    list=extend_schema(summary="List Engagement Scores", responses={200: EngagementScoreSerializer(many=True)}, tags=["Engagement"]),
    retrieve=extend_schema(summary="Retrieve Engagement Score", responses={200: EngagementScoreSerializer}, tags=["Engagement"]),
)
class EngagementScoreViewSet(StaffOnlyReadOnlyModelViewSet):
    queryset = EngagementScore.objects.all()
    serializer_class = EngagementScoreSerializer


@extend_schema_view(
    list=extend_schema(summary="List Clinical Analytics Reports", responses={200: ClinicalAnalyticsReportSerializer(many=True)}, tags=["ClinicalAnalytics"]),
    create=extend_schema(summary="Create Clinical Analytics Report", request=ClinicalAnalyticsReportSerializer, responses={201: ClinicalAnalyticsReportSerializer}, tags=["ClinicalAnalytics"]),
    retrieve=extend_schema(summary="Retrieve Clinical Analytics Report", responses={200: ClinicalAnalyticsReportSerializer}, tags=["ClinicalAnalytics"]),
)
class ClinicalAnalyticsReportViewSet(OwnedHealthcareQuerysetMixin, viewsets.ModelViewSet):
    queryset = ClinicalAnalyticsReport.objects.select_related("profile", "organization", "created_by").all()
    serializer_class = ClinicalAnalyticsReportSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["profile", "organization", "report_type", "status"]
    ordering_fields = ["period_start", "created_at"]

    def owner_filter(self, user):
        return owned_organization_filter(user) | owned_profile_filter(user) | Q(created_by=user)

    @extend_schema(summary="Regenerate clinical analytics reports", request=None, responses={202: OpenApiResponse(description="queued")}, tags=["ClinicalAnalytics"])
    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated])
    def refresh(self, request):
        profile_id = request.data.get("profile_id")
        generate_clinical_analytics_report.delay(profile_id if profile_id else None)
        return Response({"detail": "report refresh queued"})


@extend_schema_view(
    list=extend_schema(summary="List Risk Stratifications", responses={200: RiskStratificationSerializer(many=True)}, tags=["Risk"]),
    retrieve=extend_schema(summary="Retrieve Risk Stratification", responses={200: RiskStratificationSerializer}, tags=["Risk"]),
    create=extend_schema(summary="Create Risk Stratification Record", request=RiskStratificationSerializer, responses={201: RiskStratificationSerializer}, tags=["Risk"]),
)
class RiskStratificationViewSet(OwnedHealthcareQuerysetMixin, viewsets.ModelViewSet):
    queryset = RiskStratification.objects.select_related("patient", "profile").all()
    serializer_class = RiskStratificationSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["level", "profile"]
    ordering_fields = ["score", "assessed_at"]

    def owner_filter(self, user):
        return owned_patient_filter(user) | owned_profile_filter(user)

    @extend_schema(summary="Trigger risk stratification recalculation", request=None, responses={202: OpenApiResponse(description="queued")}, tags=["Risk"])
    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated])
    def compute(self, request):
        compute_risk_stratification.delay()
        return Response({"detail": "risk computation queued"})


@extend_schema_view(
    list=extend_schema(summary="List Outcome Benchmarks", responses={200: OutcomeBenchmarkSerializer(many=True)}, tags=["Outcomes"]),
    create=extend_schema(summary="Create Outcome Benchmark", request=OutcomeBenchmarkSerializer, responses={201: OutcomeBenchmarkSerializer}, tags=["Outcomes"]),
)
class OutcomeBenchmarkViewSet(OwnedHealthcareQuerysetMixin, viewsets.ModelViewSet):
    queryset = OutcomeBenchmark.objects.select_related("profile").all()
    serializer_class = OutcomeBenchmarkSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["profile", "metric_name"]
    ordering_fields = ["period_start"]

    def owner_filter(self, user):
        return owned_profile_filter(user)


@extend_schema_view(
    list=extend_schema(summary="List Patient Satisfaction Scores", responses={200: PatientSatisfactionScoreSerializer(many=True)}, tags=["Satisfaction"]),
    create=extend_schema(summary="Record Patient Satisfaction Score", request=PatientSatisfactionScoreSerializer, responses={201: PatientSatisfactionScoreSerializer}, tags=["Satisfaction"]),
)
class PatientSatisfactionScoreViewSet(OwnedHealthcareQuerysetMixin, viewsets.ModelViewSet):
    queryset = PatientSatisfactionScore.objects.select_related("patient", "profile").all()
    serializer_class = PatientSatisfactionScoreSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["patient", "channel"]
    ordering_fields = ["recorded_at"]

    def owner_filter(self, user):
        return owned_patient_filter(user) | owned_profile_filter(user)


@extend_schema_view(
    list=extend_schema(summary="List Outreach Campaigns", responses={200: OutreachCampaignSerializer(many=True)}, tags=["Outreach"]),
    create=extend_schema(summary="Create Outreach Campaign", request=OutreachCampaignSerializer, responses={201: OutreachCampaignSerializer}, tags=["Outreach"]),
)
class OutreachCampaignViewSet(OwnedHealthcareQuerysetMixin, viewsets.ModelViewSet):
    queryset = OutreachCampaign.objects.select_related("profile").all()
    serializer_class = OutreachCampaignSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["profile", "status"]
    ordering_fields = ["launched_at", "created_at"]

    def owner_filter(self, user):
        return owned_profile_filter(user)

    @extend_schema(summary="Update campaign status", request=None, responses={200: OpenApiResponse(description="updated")}, tags=["Outreach"])
    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def set_status(self, request, pk=None):
        campaign = self.get_object()
        status_value = request.data.get("status")
        if status_value in dict(OutreachCampaign.STATUS_CHOICES):
            campaign.status = status_value
            campaign.save(update_fields=["status"])
        return Response({"detail": "status updated"})


@extend_schema_view(
    list=extend_schema(summary="List Wellness Challenges", responses={200: WellnessChallengeSerializer(many=True)}, tags=["Wellness"]),
    create=extend_schema(summary="Create Wellness Challenge", request=WellnessChallengeSerializer, responses={201: WellnessChallengeSerializer}, tags=["Wellness"]),
)
class WellnessChallengeViewSet(OwnedHealthcareQuerysetMixin, viewsets.ModelViewSet):
    queryset = WellnessChallenge.objects.select_related("profile").all()
    serializer_class = WellnessChallengeSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["profile", "is_active"]
    ordering_fields = ["start_date"]

    def owner_filter(self, user):
        return owned_profile_filter(user)


@extend_schema_view(
    list=extend_schema(summary="List Habit Tracking Entries", responses={200: HabitTrackingEntrySerializer(many=True)}, tags=["Habits"]),
    create=extend_schema(summary="Log Habit Tracking Entry", request=HabitTrackingEntrySerializer, responses={201: HabitTrackingEntrySerializer}, tags=["Habits"]),
)
class HabitTrackingEntryViewSet(OwnedHealthcareQuerysetMixin, viewsets.ModelViewSet):
    queryset = HabitTrackingEntry.objects.select_related("challenge", "patient").all()
    serializer_class = HabitTrackingEntrySerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["challenge", "patient"]
    ordering_fields = ["logged_at"]

    def owner_filter(self, user):
        return Q(challenge__profile__organization__owner=user) | owned_patient_filter(user)
