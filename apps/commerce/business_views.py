"""
Views for Business & Economy features.

Endpoints:
  CrowdfundCampaignViewSet   — CRUD + contribute + contributors
  SavingsGroupViewSet        — CRUD + join + members
  JobListingViewSet          — CRUD, filterable/searchable
  JobApplicationViewSet      — CRUD + my-applications
  BusinessMentorshipViewSet  — CRUD + match
  CoWorkingSpaceViewSet      — CRUD (list AllowAny), filterable
  KingdomCertificationViewSet— CRUD + verify
  ImpactReportViewSet        — CRUD + generate
  BusinessDashboardView      — aggregated stats
"""

from django.db import transaction
from django.db.models import Q, Sum, Count
from django.utils import timezone

from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from django_filters.rest_framework import DjangoFilterBackend

from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiParameter

from .business_models import (
    CrowdfundCampaign,
    CrowdfundContribution,
    SavingsGroup,
    SavingsGroupMembership,
    JobListing,
    JobApplication,
    BusinessMentorship,
    CoWorkingSpace,
    KingdomCertification,
    ImpactReport,
)
from .business_serializers import (
    CrowdfundCampaignSerializer,
    CrowdfundContributionSerializer,
    SavingsGroupSerializer,
    SavingsGroupMembershipSerializer,
    JobListingSerializer,
    JobApplicationSerializer,
    BusinessMentorshipSerializer,
    CoWorkingSpaceSerializer,
    KingdomCertificationSerializer,
    ImpactReportSerializer,
)


# ---------------------------------------------------------------------------
# Crowdfunding
# ---------------------------------------------------------------------------

@extend_schema_view(
    list=extend_schema(tags=["Business - Crowdfunding"]),
    retrieve=extend_schema(tags=["Business - Crowdfunding"]),
    create=extend_schema(tags=["Business - Crowdfunding"]),
    update=extend_schema(tags=["Business - Crowdfunding"]),
    partial_update=extend_schema(tags=["Business - Crowdfunding"]),
    destroy=extend_schema(tags=["Business - Crowdfunding"]),
)
class CrowdfundCampaignViewSet(viewsets.ModelViewSet):
    serializer_class = CrowdfundCampaignSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["status", "category", "currency"]
    search_fields = ["title", "description"]
    ordering_fields = ["created_at", "deadline", "target_amount", "raised_amount"]

    def get_queryset(self):
        return CrowdfundCampaign.objects.select_related("creator").all()

    def perform_create(self, serializer):
        serializer.save(creator=self.request.user)

    @extend_schema(
        tags=["Business - Crowdfunding"],
        summary="Contribute to a campaign",
        request=CrowdfundContributionSerializer,
        responses={201: CrowdfundContributionSerializer},
    )
    @action(detail=True, methods=["post"], url_path="contribute")
    def contribute(self, request, pk=None):
        campaign = self.get_object()
        if campaign.status != CrowdfundCampaign.Status.ACTIVE:
            return Response(
                {"detail": "Campaign is not accepting contributions."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = CrowdfundContributionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            contribution = serializer.save(
                campaign=campaign,
                contributor=request.user,
            )
            campaign.raised_amount = (
                CrowdfundContribution.objects.filter(campaign=campaign)
                .aggregate(total=Sum("amount"))["total"]
                or 0
            )
            if campaign.raised_amount >= campaign.target_amount:
                campaign.status = CrowdfundCampaign.Status.FUNDED
            campaign.save(update_fields=["raised_amount", "status"])
        return Response(
            CrowdfundContributionSerializer(contribution).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        tags=["Business - Crowdfunding"],
        summary="List contributors for a campaign",
        responses={200: CrowdfundContributionSerializer(many=True)},
    )
    @action(detail=True, methods=["get"], url_path="contributors")
    def contributors(self, request, pk=None):
        campaign = self.get_object()
        qs = CrowdfundContribution.objects.filter(campaign=campaign).select_related(
            "contributor"
        )
        serializer = CrowdfundContributionSerializer(qs, many=True)
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# Savings Groups
# ---------------------------------------------------------------------------

@extend_schema_view(
    list=extend_schema(tags=["Business - Savings Groups"]),
    retrieve=extend_schema(tags=["Business - Savings Groups"]),
    create=extend_schema(tags=["Business - Savings Groups"]),
    update=extend_schema(tags=["Business - Savings Groups"]),
    partial_update=extend_schema(tags=["Business - Savings Groups"]),
    destroy=extend_schema(tags=["Business - Savings Groups"]),
)
class SavingsGroupViewSet(viewsets.ModelViewSet):
    serializer_class = SavingsGroupSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["type", "currency", "is_active"]
    search_fields = ["name"]
    ordering_fields = ["created_at", "contribution_amount"]

    def get_queryset(self):
        return SavingsGroup.objects.prefetch_related("memberships").all()

    def perform_create(self, serializer):
        serializer.save(creator=self.request.user)

    @extend_schema(
        tags=["Business - Savings Groups"],
        summary="Join a savings group",
        responses={201: SavingsGroupMembershipSerializer, 400: None},
    )
    @action(detail=True, methods=["post"], url_path="join")
    def join(self, request, pk=None):
        group = self.get_object()
        if not group.is_active:
            return Response(
                {"detail": "This savings group is no longer active."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        current_count = group.memberships.count()
        if current_count >= group.max_members:
            return Response(
                {"detail": "Savings group is full."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if SavingsGroupMembership.objects.filter(group=group, user=request.user).exists():
            return Response(
                {"detail": "You are already a member of this group."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        payout_position = current_count + 1
        membership = SavingsGroupMembership.objects.create(
            group=group,
            user=request.user,
            payout_position=payout_position,
        )
        return Response(
            SavingsGroupMembershipSerializer(membership).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        tags=["Business - Savings Groups"],
        summary="List members of a savings group",
        responses={200: SavingsGroupMembershipSerializer(many=True)},
    )
    @action(detail=True, methods=["get"], url_path="members")
    def members(self, request, pk=None):
        group = self.get_object()
        qs = SavingsGroupMembership.objects.filter(group=group).select_related("user")
        serializer = SavingsGroupMembershipSerializer(qs, many=True)
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# Job Board
# ---------------------------------------------------------------------------

@extend_schema_view(
    list=extend_schema(tags=["Business - Jobs"]),
    retrieve=extend_schema(tags=["Business - Jobs"]),
    create=extend_schema(tags=["Business - Jobs"]),
    update=extend_schema(tags=["Business - Jobs"]),
    partial_update=extend_schema(tags=["Business - Jobs"]),
    destroy=extend_schema(tags=["Business - Jobs"]),
)
class JobListingViewSet(viewsets.ModelViewSet):
    serializer_class = JobListingSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["job_type", "country", "industry", "remote_allowed", "is_active", "is_kingdom_certified"]
    search_fields = ["title", "description", "required_skills"]
    ordering_fields = ["created_at", "deadline", "salary_min", "salary_max"]

    def get_queryset(self):
        return JobListing.objects.select_related("poster").all()

    def perform_create(self, serializer):
        serializer.save(poster=self.request.user)


# ---------------------------------------------------------------------------
# Job Applications
# ---------------------------------------------------------------------------

@extend_schema_view(
    list=extend_schema(tags=["Business - Job Applications"]),
    retrieve=extend_schema(tags=["Business - Job Applications"]),
    create=extend_schema(tags=["Business - Job Applications"]),
    update=extend_schema(tags=["Business - Job Applications"]),
    partial_update=extend_schema(tags=["Business - Job Applications"]),
    destroy=extend_schema(tags=["Business - Job Applications"]),
)
class JobApplicationViewSet(viewsets.ModelViewSet):
    serializer_class = JobApplicationSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["status", "listing"]
    ordering_fields = ["created_at"]

    def get_queryset(self):
        return JobApplication.objects.select_related("listing", "applicant").filter(
            applicant=self.request.user
        )

    def perform_create(self, serializer):
        listing = serializer.validated_data.get("listing")
        if listing:
            with transaction.atomic():
                application = serializer.save(applicant=self.request.user)
                JobListing.objects.filter(pk=listing.pk).update(
                    application_count=listing.application_count + 1
                )
        else:
            serializer.save(applicant=self.request.user)

    @extend_schema(
        tags=["Business - Job Applications"],
        summary="List the current user's job applications",
        responses={200: JobApplicationSerializer(many=True)},
    )
    @action(detail=False, methods=["get"], url_path="my-applications")
    def my_applications(self, request):
        qs = JobApplication.objects.filter(applicant=request.user).select_related("listing")
        serializer = JobApplicationSerializer(qs, many=True)
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# Business Mentorship
# ---------------------------------------------------------------------------

@extend_schema_view(
    list=extend_schema(tags=["Business - Mentorship"]),
    retrieve=extend_schema(tags=["Business - Mentorship"]),
    create=extend_schema(tags=["Business - Mentorship"]),
    update=extend_schema(tags=["Business - Mentorship"]),
    partial_update=extend_schema(tags=["Business - Mentorship"]),
    destroy=extend_schema(tags=["Business - Mentorship"]),
)
class BusinessMentorshipViewSet(viewsets.ModelViewSet):
    serializer_class = BusinessMentorshipSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["status", "domain"]
    search_fields = ["domain", "description"]
    ordering_fields = ["created_at"]

    def get_queryset(self):
        return BusinessMentorship.objects.select_related("mentor", "mentee").all()

    def perform_create(self, serializer):
        serializer.save(mentor=self.request.user)

    @extend_schema(
        tags=["Business - Mentorship"],
        summary="Request to be matched as mentee for this mentorship",
        responses={200: BusinessMentorshipSerializer, 400: None},
    )
    @action(detail=True, methods=["post"], url_path="match")
    def match(self, request, pk=None):
        mentorship = self.get_object()
        if mentorship.status != BusinessMentorship.Status.OPEN:
            return Response(
                {"detail": "This mentorship is not open for matching."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if mentorship.mentor == request.user:
            return Response(
                {"detail": "You cannot match with your own mentorship listing."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        mentorship.mentee = request.user
        mentorship.status = BusinessMentorship.Status.MATCHED
        mentorship.save(update_fields=["mentee", "status"])
        return Response(BusinessMentorshipSerializer(mentorship).data, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Co-Working Spaces
# ---------------------------------------------------------------------------

@extend_schema_view(
    list=extend_schema(tags=["Business - Co-Working"]),
    retrieve=extend_schema(tags=["Business - Co-Working"]),
    create=extend_schema(tags=["Business - Co-Working"]),
    update=extend_schema(tags=["Business - Co-Working"]),
    partial_update=extend_schema(tags=["Business - Co-Working"]),
    destroy=extend_schema(tags=["Business - Co-Working"]),
)
class CoWorkingSpaceViewSet(viewsets.ModelViewSet):
    serializer_class = CoWorkingSpaceSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["city", "country", "is_active"]
    search_fields = ["name", "description", "city", "country"]
    ordering_fields = ["created_at", "price_per_day"]

    def get_queryset(self):
        return CoWorkingSpace.objects.all()

    def get_permissions(self):
        # CoWorkingSpace has no owner/created_by field - it's a curated
        # directory, not user-submitted listings - so there is no notion of
        # "your own" space to scope a self-service update/delete to. Without
        # this, IsAuthenticated alone let ANY authenticated user modify or
        # delete ANY other business's listing (full BOLA on write actions).
        # Listing/detail views stay public; writes are restricted to staff.
        if self.action in ("list", "retrieve"):
            return [AllowAny()]
        return [IsAdminUser()]


# ---------------------------------------------------------------------------
# Kingdom Certifications
# ---------------------------------------------------------------------------

@extend_schema_view(
    list=extend_schema(tags=["Business - Certifications"]),
    retrieve=extend_schema(tags=["Business - Certifications"]),
    create=extend_schema(tags=["Business - Certifications"]),
    update=extend_schema(tags=["Business - Certifications"]),
    partial_update=extend_schema(tags=["Business - Certifications"]),
    destroy=extend_schema(tags=["Business - Certifications"]),
)
class KingdomCertificationViewSet(viewsets.ModelViewSet):
    serializer_class = KingdomCertificationSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["type", "is_active"]
    search_fields = ["business_name"]
    ordering_fields = ["created_at", "issued_at", "valid_until", "score"]

    def get_queryset(self):
        return KingdomCertification.objects.select_related("owner").all()

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    @extend_schema(
        tags=["Business - Certifications"],
        summary="Verify a business certification by name",
        parameters=[
            OpenApiParameter(
                name="business_name",
                location=OpenApiParameter.QUERY,
                description="Business name to look up",
                required=True,
                type=str,
            )
        ],
        responses={200: KingdomCertificationSerializer(many=True)},
    )
    @action(detail=False, methods=["get"], url_path="verify")
    def verify(self, request):
        business_name = request.query_params.get("business_name", "").strip()
        if not business_name:
            return Response(
                {"detail": "business_name query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        qs = KingdomCertification.objects.filter(
            business_name__icontains=business_name, is_active=True
        )
        serializer = KingdomCertificationSerializer(qs, many=True)
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# Impact Reports
# ---------------------------------------------------------------------------

@extend_schema_view(
    list=extend_schema(tags=["Business - Impact Reports"]),
    retrieve=extend_schema(tags=["Business - Impact Reports"]),
    create=extend_schema(tags=["Business - Impact Reports"]),
    update=extend_schema(tags=["Business - Impact Reports"]),
    partial_update=extend_schema(tags=["Business - Impact Reports"]),
    destroy=extend_schema(tags=["Business - Impact Reports"]),
)
class ImpactReportViewSet(viewsets.ModelViewSet):
    serializer_class = ImpactReportSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["year", "is_public"]
    search_fields = ["title"]
    ordering_fields = ["year", "created_at"]

    def get_queryset(self):
        return ImpactReport.objects.select_related("user").all()

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @extend_schema(
        tags=["Business - Impact Reports"],
        summary="Generate a mock impact report structure",
        responses={200: ImpactReportSerializer},
    )
    @action(detail=False, methods=["post"], url_path="generate")
    def generate(self, request):
        """Return a mock impact report JSON payload for the caller to review/save."""
        year = request.data.get("year", timezone.now().year)
        org_id = request.data.get("org_id")
        title = request.data.get("title", f"Impact Report {year}")

        mock_content = {
            "summary": "This report was auto-generated.",
            "jobs_created": 0,
            "funds_raised": "0.00",
            "communities_served": 0,
            "certifications_issued": 0,
            "savings_groups_active": 0,
            "mentorships_completed": 0,
        }

        return Response(
            {
                "org_id": org_id,
                "title": title,
                "year": year,
                "content": mock_content,
                "note": "Review and save via POST /api/v1/business/impact/",
            },
            status=status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# Business Dashboard
# ---------------------------------------------------------------------------

class BusinessDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Business - Dashboard"],
        summary="Aggregated business & economy stats for the current user",
        responses={200: None},
    )
    def get(self, request):
        user = request.user

        # Crowdfunding
        campaigns = CrowdfundCampaign.objects.filter(creator=user)
        campaign_stats = campaigns.aggregate(
            total=Count("id"),
            active=Count("id", filter=Q(status=CrowdfundCampaign.Status.ACTIVE)),
            funded=Count("id", filter=Q(status=CrowdfundCampaign.Status.FUNDED)),
            total_raised=Sum("raised_amount"),
        )

        # Jobs
        job_listings = JobListing.objects.filter(poster=user)
        job_stats = {
            "posted": job_listings.count(),
            "active": job_listings.filter(is_active=True).count(),
            "total_applications_received": job_listings.aggregate(
                total=Sum("application_count")
            )["total"] or 0,
        }
        applications_sent = JobApplication.objects.filter(applicant=user).count()

        # Savings groups
        savings_memberships = SavingsGroupMembership.objects.filter(user=user)
        savings_stats = {
            "groups_joined": savings_memberships.count(),
            "total_contributed": float(
                savings_memberships.aggregate(total=Sum("total_contributed"))["total"] or 0
            ),
        }

        # Mentorship
        mentoring = BusinessMentorship.objects.filter(mentor=user).count()
        being_mentored = BusinessMentorship.objects.filter(mentee=user).count()

        data = {
            "crowdfunding": {
                "campaigns_created": campaign_stats["total"] or 0,
                "active_campaigns": campaign_stats["active"] or 0,
                "funded_campaigns": campaign_stats["funded"] or 0,
                "total_raised": float(campaign_stats["total_raised"] or 0),
            },
            "jobs": {
                **job_stats,
                "applications_sent": applications_sent,
            },
            "savings": savings_stats,
            "mentorship": {
                "mentoring_count": mentoring,
                "being_mentored_count": being_mentored,
            },
        }
        return Response(data, status=status.HTTP_200_OK)
