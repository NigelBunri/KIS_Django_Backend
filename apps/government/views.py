import secrets
from django.db import IntegrityError
from django.db.models import Count
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    Petition,
    PetitionSignature,
    CivicPoll,
    PollVote,
    LegalAidEntry,
    LegalDocumentTemplate,
    DiasporaCommunity,
    DiasporaCommunityMembership,
    NGOProfile,
    GrantApplication,
    ComplianceDeadline,
    WhistleblowerReport,
    BoardElection,
    ElectionVote,
    BoardResolution,
)
from .serializers import (
    PetitionSerializer,
    PetitionSignatureSerializer,
    CivicPollSerializer,
    PollVoteSerializer,
    LegalAidEntrySerializer,
    LegalDocumentTemplateSerializer,
    DiasporaCommunitySerializer,
    DiasporaCommunityMembershipSerializer,
    NGOProfileSerializer,
    GrantApplicationSerializer,
    ComplianceDeadlineSerializer,
    WhistleblowerSubmitSerializer,
    WhistleblowerStatusSerializer,
    BoardElectionSerializer,
    ElectionVoteSerializer,
    BoardResolutionSerializer,
)


# ---------------------------------------------------------------------------
# Petitions
# ---------------------------------------------------------------------------

@extend_schema(tags=["Government - Petitions"])
class PetitionViewSet(viewsets.ModelViewSet):
    queryset = Petition.objects.all()
    serializer_class = PetitionSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["status", "category", "country"]
    search_fields = ["title", "description", "target"]
    ordering_fields = ["created_at", "signatures_count", "deadline"]

    def perform_create(self, serializer):
        serializer.save(creator=self.request.user)

    @extend_schema(
        request=PetitionSignatureSerializer,
        responses={201: PetitionSignatureSerializer},
        description="Sign a petition. Returns the signature object.",
    )
    @action(detail=True, methods=["post"], url_path="sign")
    def sign(self, request, pk=None):
        petition = self.get_object()
        if petition.status != "active":
            return Response(
                {"detail": "Petition is not active."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = PetitionSignatureSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            signature = PetitionSignature.objects.create(
                petition=petition,
                user=request.user,
                is_anonymous=serializer.validated_data.get("is_anonymous", False),
                comment=serializer.validated_data.get("comment", ""),
            )
        except IntegrityError:
            return Response(
                {"detail": "You have already signed this petition."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        petition.signatures_count = PetitionSignature.objects.filter(petition=petition).count()
        petition.save(update_fields=["signatures_count"])
        out = PetitionSignatureSerializer(signature)
        return Response(out.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        responses={200: PetitionSignatureSerializer(many=True)},
        description="List all signatures for a petition.",
    )
    @action(detail=True, methods=["get"], url_path="signatures")
    def signatures(self, request, pk=None):
        petition = self.get_object()
        qs = PetitionSignature.objects.filter(petition=petition).select_related("user")
        serializer = PetitionSignatureSerializer(qs, many=True)
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# Civic Polls
# ---------------------------------------------------------------------------

@extend_schema(tags=["Government - Polls"])
class CivicPollViewSet(viewsets.ModelViewSet):
    queryset = CivicPoll.objects.all()
    serializer_class = CivicPollSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["scope", "is_public", "church_id", "org_id"]
    search_fields = ["question"]
    ordering_fields = ["created_at", "start_date", "end_date"]

    def perform_create(self, serializer):
        serializer.save(creator=self.request.user)

    @extend_schema(
        request=PollVoteSerializer,
        responses={201: PollVoteSerializer},
        description="Cast a vote on a poll.",
    )
    @action(detail=True, methods=["post"], url_path="vote")
    def vote(self, request, pk=None):
        poll = self.get_object()
        serializer = PollVoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        option_key = serializer.validated_data.get("option_key")
        valid_keys = [opt.get("key") for opt in (poll.options or [])]
        if option_key not in valid_keys:
            return Response(
                {"detail": f"Invalid option_key. Valid options: {valid_keys}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            vote_obj = PollVote.objects.create(
                poll=poll, user=request.user, option_key=option_key
            )
        except IntegrityError:
            return Response(
                {"detail": "You have already voted on this poll."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        out = PollVoteSerializer(vote_obj)
        return Response(out.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        responses={200: {"type": "object"}},
        description="Retrieve aggregated vote results for a poll.",
    )
    @action(detail=True, methods=["get"], url_path="results")
    def results(self, request, pk=None):
        poll = self.get_object()
        counts = (
            PollVote.objects.filter(poll=poll)
            .values("option_key")
            .annotate(count=Count("id"))
        )
        tally = {item["option_key"]: item["count"] for item in counts}
        options_with_counts = [
            {**opt, "count": tally.get(opt.get("key"), 0)}
            for opt in (poll.options or [])
        ]
        return Response(
            {
                "poll_id": str(poll.id),
                "question": poll.question,
                "total_votes": sum(tally.values()),
                "results": options_with_counts,
            }
        )


# ---------------------------------------------------------------------------
# Legal Aid
# ---------------------------------------------------------------------------

@extend_schema(tags=["Government - Legal Aid"])
class LegalAidEntryViewSet(viewsets.ModelViewSet):
    queryset = LegalAidEntry.objects.filter(is_active=True)
    serializer_class = LegalAidEntrySerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["country", "specialty", "is_pro_bono", "state", "city"]
    search_fields = ["name", "specialty", "description"]
    ordering_fields = ["name", "country", "created_at"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [AllowAny()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


@extend_schema(tags=["Government - Legal Documents"])
class LegalDocumentTemplateViewSet(viewsets.ModelViewSet):
    queryset = LegalDocumentTemplate.objects.filter(is_public=True)
    serializer_class = LegalDocumentTemplateSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["country", "type", "is_public"]
    search_fields = ["title", "description", "type"]
    ordering_fields = ["title", "created_at"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [AllowAny()]
        return [IsAuthenticated()]

    def get_queryset(self):
        if self.request.user and self.request.user.is_authenticated:
            return LegalDocumentTemplate.objects.all()
        return LegalDocumentTemplate.objects.filter(is_public=True)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


# ---------------------------------------------------------------------------
# Diaspora
# ---------------------------------------------------------------------------

@extend_schema(tags=["Government - Diaspora"])
class DiasporaCommunityViewSet(viewsets.ModelViewSet):
    queryset = DiasporaCommunity.objects.filter(is_active=True)
    serializer_class = DiasporaCommunitySerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["country_of_origin", "host_country", "is_active"]
    search_fields = ["name", "description", "country_of_origin", "host_country"]
    ordering_fields = ["name", "member_count", "created_at"]

    def perform_create(self, serializer):
        serializer.save(founder=self.request.user)

    @extend_schema(
        responses={201: DiasporaCommunityMembershipSerializer},
        description="Join a diaspora community.",
    )
    @action(detail=True, methods=["post"], url_path="join")
    def join(self, request, pk=None):
        community = self.get_object()
        try:
            membership = DiasporaCommunityMembership.objects.create(
                community=community, user=request.user
            )
        except IntegrityError:
            return Response(
                {"detail": "You are already a member of this community."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        community.member_count = DiasporaCommunityMembership.objects.filter(
            community=community
        ).count()
        community.save(update_fields=["member_count"])
        out = DiasporaCommunityMembershipSerializer(membership)
        return Response(out.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        responses={204: None},
        description="Leave a diaspora community.",
    )
    @action(detail=True, methods=["post"], url_path="leave")
    def leave(self, request, pk=None):
        community = self.get_object()
        deleted, _ = DiasporaCommunityMembership.objects.filter(
            community=community, user=request.user
        ).delete()
        if not deleted:
            return Response(
                {"detail": "You are not a member of this community."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        community.member_count = DiasporaCommunityMembership.objects.filter(
            community=community
        ).count()
        community.save(update_fields=["member_count"])
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# NGO Profiles
# ---------------------------------------------------------------------------

@extend_schema(tags=["Government - NGO"])
class NGOProfileViewSet(viewsets.ModelViewSet):
    queryset = NGOProfile.objects.all()
    serializer_class = NGOProfileSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["country", "is_verified"]
    search_fields = ["name", "description", "reg_number"]
    ordering_fields = ["name", "country", "created_at"]

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


# ---------------------------------------------------------------------------
# Grant Applications
# ---------------------------------------------------------------------------

@extend_schema(tags=["Government - Grants"])
class GrantApplicationViewSet(viewsets.ModelViewSet):
    serializer_class = GrantApplicationSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["status", "ngo", "currency"]
    search_fields = ["title", "description", "grant_source"]
    ordering_fields = ["created_at", "submission_date", "amount_requested"]

    def get_queryset(self):
        return GrantApplication.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


# ---------------------------------------------------------------------------
# Compliance Deadlines
# ---------------------------------------------------------------------------

@extend_schema(tags=["Government - Compliance"])
class ComplianceDeadlineViewSet(viewsets.ModelViewSet):
    serializer_class = ComplianceDeadlineSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["org_id", "status", "type"]
    search_fields = ["title", "notes"]
    ordering_fields = ["due_date", "created_at", "status"]

    def get_queryset(self):
        return ComplianceDeadline.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


# ---------------------------------------------------------------------------
# Whistleblower
# ---------------------------------------------------------------------------

def _generate_case_ref() -> str:
    """Generate a unique 12-character alphanumeric case reference."""
    return secrets.token_hex(6).upper()


@extend_schema(tags=["Government - Whistleblower"])
class WhistleblowerSubmitView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=WhistleblowerSubmitSerializer,
        responses={201: {"type": "object", "properties": {"case_ref": {"type": "string"}}}},
        description=(
            "Submit an anonymous whistleblower report. "
            "Returns only the case_ref for future status checks."
        ),
    )
    def post(self, request):
        serializer = WhistleblowerSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Generate a unique case reference
        for _ in range(10):
            case_ref = _generate_case_ref()
            if not WhistleblowerReport.objects.filter(case_ref=case_ref).exists():
                break
        else:
            return Response(
                {"detail": "Could not generate unique case reference. Please try again."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        WhistleblowerReport.objects.create(
            encrypted_content=serializer.validated_data["content"],
            category=serializer.validated_data["category"],
            country=serializer.validated_data.get("country", ""),
            case_ref=case_ref,
        )
        return Response({"case_ref": case_ref}, status=status.HTTP_201_CREATED)


@extend_schema(tags=["Government - Whistleblower"])
class WhistleblowerStatusView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="case_ref",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Case reference returned at submission time.",
                required=True,
            )
        ],
        responses={200: WhistleblowerStatusSerializer},
        description="Check the status of a whistleblower report by case_ref.",
    )
    def get(self, request):
        case_ref = request.query_params.get("case_ref")
        if not case_ref:
            return Response(
                {"detail": "case_ref query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            report = WhistleblowerReport.objects.get(case_ref=case_ref.upper())
        except WhistleblowerReport.DoesNotExist:
            return Response(
                {"detail": "No report found with the given case_ref."},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = WhistleblowerStatusSerializer(report)
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# Board Elections
# ---------------------------------------------------------------------------

@extend_schema(tags=["Government - Elections"])
class BoardElectionViewSet(viewsets.ModelViewSet):
    queryset = BoardElection.objects.all()
    serializer_class = BoardElectionSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["org_id", "is_closed"]
    search_fields = ["title"]
    ordering_fields = ["created_at", "start_date", "end_date"]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @extend_schema(
        request=ElectionVoteSerializer,
        responses={201: ElectionVoteSerializer},
        description="Cast a vote for a candidate in a board election.",
    )
    @action(detail=True, methods=["post"], url_path="vote")
    def vote(self, request, pk=None):
        election = self.get_object()
        if election.is_closed:
            return Response(
                {"detail": "This election is closed."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = ElectionVoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        candidate_index = serializer.validated_data.get("candidate_index")
        if candidate_index is None or candidate_index < 0 or candidate_index >= len(
            election.candidates or []
        ):
            return Response(
                {"detail": "Invalid candidate_index."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            vote_obj = ElectionVote.objects.create(
                election=election, user=request.user, candidate_index=candidate_index
            )
        except IntegrityError:
            return Response(
                {"detail": "You have already voted in this election."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        out = ElectionVoteSerializer(vote_obj)
        return Response(out.data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Board Resolutions
# ---------------------------------------------------------------------------

@extend_schema(tags=["Government - Resolutions"])
class BoardResolutionViewSet(viewsets.ModelViewSet):
    queryset = BoardResolution.objects.all()
    serializer_class = BoardResolutionSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["org_id", "passed", "meeting_date"]
    search_fields = ["title", "content"]
    ordering_fields = ["meeting_date", "created_at"]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
