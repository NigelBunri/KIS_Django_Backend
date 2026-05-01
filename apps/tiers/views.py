from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import SAFE_METHODS, BasePermission, IsAdminUser
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from .models import (
    User, Organization, BillingPlan, Subscription, Entitlement, UsageQuota, BillingInvoice,
    FeatureFlag, PlanFeature, PartnerSettings, ImpactAnalyticsSettings, DonationCampaign,
    EventTicketing, HolographicRoom, QuantumEncryptionSetting,
)
from .serializers import (
    UserSerializer, OrganizationSerializer, BillingPlanSerializer, SubscriptionSerializer, EntitlementSerializer,
    UsageQuotaSerializer, BillingInvoiceSerializer, FeatureFlagSerializer, PlanFeatureSerializer, PartnerSettingsSerializer,
    ImpactAnalyticsSettingsSerializer, DonationCampaignSerializer, EventTicketingSerializer, HolographicRoomSerializer,
    QuantumEncryptionSettingSerializer,
)
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiResponse, OpenApiParameter, OpenApiTypes, OpenApiExample
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from .tasks import reconcile_subscription, generate_invoice


class StaffOrAuthenticatedReadOnly(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return True
        return request.user.is_staff or request.user.is_superuser


class UserOwnedQuerysetMixin:
    owner_id_field = "owner_id"

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_staff or user.is_superuser:
            return qs
        return qs.filter(owner_type="user", **{self.owner_id_field: user.id})

    def perform_create(self, serializer):
        user = self.request.user
        if user.is_staff or user.is_superuser:
            serializer.save()
            return
        serializer.save(owner_type="user", owner_id=user.id)

# Common query params
OWNER_FILTER_PARAMS = [
    OpenApiParameter(name='owner_type', required=False, location=OpenApiParameter.QUERY, type=OpenApiTypes.STR),
    OpenApiParameter(name='owner_id', required=False, location=OpenApiParameter.QUERY, type=OpenApiTypes.UUID),
]

@extend_schema_view(
    list=extend_schema(summary='List Users', responses={200: UserSerializer(many=True)}, tags=['Users']),
    retrieve=extend_schema(summary='Retrieve User', responses={200: UserSerializer}, tags=['Users']),
    create=extend_schema(summary='Create User', request=UserSerializer, responses={201: UserSerializer}, tags=['Users']),
)
class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [IsAdminUser]
    filter_backends = [DjangoFilterBackend, SearchFilter]
    search_fields = ['email','username','display_name']

@extend_schema_view(
    list=extend_schema(summary='List Organizations', responses={200: OrganizationSerializer(many=True)}, tags=['Organizations']),
    retrieve=extend_schema(summary='Retrieve Organization', responses={200: OrganizationSerializer}, tags=['Organizations']),
    create=extend_schema(summary='Create Organization', request=OrganizationSerializer, responses={201: OrganizationSerializer}, tags=['Organizations']),
)
class OrganizationViewSet(viewsets.ModelViewSet):
    queryset = Organization.objects.all()
    serializer_class = OrganizationSerializer
    permission_classes = [IsAdminUser]
    filter_backends = [SearchFilter]
    search_fields = ['name','domain']

@extend_schema_view(
    list=extend_schema(summary='List Billing Plans', responses={200: BillingPlanSerializer(many=True)}, tags=['Billing']),
    retrieve=extend_schema(summary='Retrieve Billing Plan', responses={200: BillingPlanSerializer}, tags=['Billing']),
    create=extend_schema(summary='Create Billing Plan', request=BillingPlanSerializer, responses={201: BillingPlanSerializer}, tags=['Billing']),
)
class BillingPlanViewSet(viewsets.ModelViewSet):
    queryset = BillingPlan.objects.all()
    serializer_class = BillingPlanSerializer
    permission_classes = [StaffOrAuthenticatedReadOnly]

@extend_schema_view(
    list=extend_schema(summary='List Subscriptions', responses={200: SubscriptionSerializer(many=True)}, tags=['Subscriptions']),
    retrieve=extend_schema(summary='Retrieve Subscription', responses={200: SubscriptionSerializer}, tags=['Subscriptions']),
    create=extend_schema(summary='Create Subscription', request=SubscriptionSerializer, responses={201: SubscriptionSerializer}, tags=['Subscriptions']),
)
class SubscriptionViewSet(UserOwnedQuerysetMixin, viewsets.ModelViewSet):
    queryset = Subscription.objects.all()
    serializer_class = SubscriptionSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['owner_type','owner_id','plan']

    @extend_schema(summary='Reconcile subscription (webhook/refresh)', responses={200: OpenApiResponse(description='reconciled')}, tags=['Subscriptions'])
    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated])
    def reconcile(self, request, pk=None):
        sub = self.get_object()
        reconcile_subscription.delay(str(sub.id))
        return Response({'detail':'reconciliation started'})

@extend_schema_view(
    list=extend_schema(summary='List Entitlements', responses={200: EntitlementSerializer(many=True)}, tags=['Entitlements']),
    create=extend_schema(summary='Create Entitlement', request=EntitlementSerializer, responses={201: EntitlementSerializer}, tags=['Entitlements']),
)
class EntitlementViewSet(viewsets.ModelViewSet):
    queryset = Entitlement.objects.all()
    serializer_class = EntitlementSerializer
    permission_classes = [StaffOrAuthenticatedReadOnly]

@extend_schema_view(
    list=extend_schema(summary='List Usage Quotas', responses={200: UsageQuotaSerializer(many=True)}, tags=['Usage']),
    create=extend_schema(summary='Create Usage Quota', request=UsageQuotaSerializer, responses={201: UsageQuotaSerializer}, tags=['Usage']),
)
class UsageQuotaViewSet(UserOwnedQuerysetMixin, viewsets.ModelViewSet):
    queryset = UsageQuota.objects.all()
    serializer_class = UsageQuotaSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['owner_type','owner_id','feature_key','period_start']

@extend_schema_view(
    list=extend_schema(summary='List Invoices', responses={200: BillingInvoiceSerializer(many=True)}, tags=['Billing']),
    create=extend_schema(summary='Generate Invoice', request=BillingInvoiceSerializer, responses={201: BillingInvoiceSerializer}, tags=['Billing']),
)
class BillingInvoiceViewSet(viewsets.ModelViewSet):
    queryset = BillingInvoice.objects.all()
    serializer_class = BillingInvoiceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset().select_related("subscription", "subscription__plan")
        user = self.request.user
        if user.is_staff or user.is_superuser:
            return qs
        return qs.filter(subscription__owner_type="user", subscription__owner_id=user.id)

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy", "generate"):
            return [IsAdminUser()]
        return super().get_permissions()

    @extend_schema(summary='Generate invoice (async)', responses={200: OpenApiResponse(description='invoice queued')}, tags=['Billing'])
    @action(detail=False, methods=['post'], permission_classes=[IsAuthenticated])
    def generate(self, request):
        # request should contain subscription ids or owner filters
        generate_invoice.delay(request.data)
        return Response({'detail':'invoice generation queued'})

@extend_schema_view(
    list=extend_schema(summary='List Feature Flags', responses={200: FeatureFlagSerializer(many=True)}, tags=['Flags']),
    create=extend_schema(summary='Create Feature Flag', request=FeatureFlagSerializer, responses={201: FeatureFlagSerializer}, tags=['Flags']),
)
class FeatureFlagViewSet(viewsets.ModelViewSet):
    queryset = FeatureFlag.objects.all()
    serializer_class = FeatureFlagSerializer
    permission_classes = [StaffOrAuthenticatedReadOnly]

@extend_schema_view(
    list=extend_schema(summary='List Plan Features', responses={200: PlanFeatureSerializer(many=True)}, tags=['Flags']),
    create=extend_schema(summary='Create Plan Feature', request=PlanFeatureSerializer, responses={201: PlanFeatureSerializer}, tags=['Flags']),
)
class PlanFeatureViewSet(viewsets.ModelViewSet):
    queryset = PlanFeature.objects.all()
    serializer_class = PlanFeatureSerializer
    permission_classes = [StaffOrAuthenticatedReadOnly]

@extend_schema_view(
    retrieve=extend_schema(summary='Retrieve Partner Settings', responses={200: PartnerSettingsSerializer}, tags=['Partner']),
    update=extend_schema(summary='Update Partner Settings', request=PartnerSettingsSerializer, responses={200: PartnerSettingsSerializer}, tags=['Partner']),
)
class PartnerSettingsViewSet(viewsets.ModelViewSet):
    queryset = PartnerSettings.objects.all()
    serializer_class = PartnerSettingsSerializer
    permission_classes = [IsAdminUser]

@extend_schema_view(
    retrieve=extend_schema(summary='Retrieve Impact Analytics Settings', responses={200: ImpactAnalyticsSettingsSerializer}, tags=['Partner']),
    update=extend_schema(summary='Update Impact Analytics Settings', request=ImpactAnalyticsSettingsSerializer, responses={200: ImpactAnalyticsSettingsSerializer}, tags=['Partner']),
)
class ImpactAnalyticsSettingsViewSet(viewsets.ModelViewSet):
    queryset = ImpactAnalyticsSettings.objects.all()
    serializer_class = ImpactAnalyticsSettingsSerializer
    permission_classes = [IsAdminUser]

@extend_schema_view(
    list=extend_schema(summary='List Donation Campaigns', responses={200: DonationCampaignSerializer(many=True)}, tags=['Partner']),
    create=extend_schema(summary='Create Donation Campaign', request=DonationCampaignSerializer, responses={201: DonationCampaignSerializer}, tags=['Partner']),
)
class DonationCampaignViewSet(viewsets.ModelViewSet):
    queryset = DonationCampaign.objects.all()
    serializer_class = DonationCampaignSerializer
    permission_classes = [IsAdminUser]

@extend_schema_view(
    list=extend_schema(summary='List Event Tickets', responses={200: EventTicketingSerializer(many=True)}, tags=['Partner']),
    create=extend_schema(summary='Create Event Ticket Type', request=EventTicketingSerializer, responses={201: EventTicketingSerializer}, tags=['Partner']),
)
class EventTicketingViewSet(viewsets.ModelViewSet):
    queryset = EventTicketing.objects.all()
    serializer_class = EventTicketingSerializer
    permission_classes = [IsAdminUser]

@extend_schema_view(
    list=extend_schema(summary='List Holographic Rooms', responses={200: HolographicRoomSerializer(many=True)}, tags=['Premium']),
    create=extend_schema(summary='Create Holographic Room', request=HolographicRoomSerializer, responses={201: HolographicRoomSerializer}, tags=['Premium']),
)
class HolographicRoomViewSet(viewsets.ModelViewSet):
    queryset = HolographicRoom.objects.all()
    serializer_class = HolographicRoomSerializer
    permission_classes = [IsAdminUser]

@extend_schema_view(
    list=extend_schema(summary='List Quantum Encryption Settings', responses={200: QuantumEncryptionSettingSerializer(many=True)}, tags=['Premium']),
    create=extend_schema(summary='Create Quantum Encryption Setting', request=QuantumEncryptionSettingSerializer, responses={201: QuantumEncryptionSettingSerializer}, tags=['Premium']),
)
class QuantumEncryptionSettingViewSet(UserOwnedQuerysetMixin, viewsets.ModelViewSet):
    queryset = QuantumEncryptionSetting.objects.all()
    serializer_class = QuantumEncryptionSettingSerializer
    permission_classes = [IsAuthenticated]
