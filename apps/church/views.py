from django.db.models import Sum, Count, Q
from django.utils import timezone
from rest_framework import status, filters
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, OpenApiParameter
from drf_spectacular.types import OpenApiTypes

from .models import (
    ChurchGiving,
    TithePledge,
    OfferingCampaign,
    ChurchMembership,
    AttendanceRecord,
    ChurchLifeEvent,
    MemberBirthdayAnniversary,
    SmallGroup,
    SmallGroupMembership,
    SmallGroupAttendance,
    DiscipleshipJourney,
    SpiritualGiftsAssessment,
    AccountabilityPartner,
    PrayerRequest,
    PrayerWallEntry,
    FastingRecord,
    PrayerChainSlot,
    Song,
    SetList,
    MinistryDepartment,
    VolunteerSignup,
    OutreachCampaign,
    EvangelismRecord,
)
from .serializers import (
    ChurchGivingSerializer,
    TithePledgeSerializer,
    OfferingCampaignSerializer,
    ChurchMembershipSerializer,
    AttendanceRecordSerializer,
    ChurchLifeEventSerializer,
    MemberBirthdayAnniversarySerializer,
    SmallGroupSerializer,
    SmallGroupMembershipSerializer,
    SmallGroupAttendanceSerializer,
    DiscipleshipJourneySerializer,
    SpiritualGiftsAssessmentSerializer,
    AccountabilityPartnerSerializer,
    PrayerRequestSerializer,
    PrayerWallEntrySerializer,
    FastingRecordSerializer,
    PrayerChainSlotSerializer,
    SongSerializer,
    SetListSerializer,
    MinistryDepartmentSerializer,
    VolunteerSignupSerializer,
    OutreachCampaignSerializer,
    EvangelismRecordSerializer,
)


# ─── GIVING ───────────────────────────────────────────────────────────────────

@extend_schema(tags=["Church - Giving"])
class ChurchGivingViewSet(ModelViewSet):
    serializer_class = ChurchGivingSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["type", "currency", "campaign", "is_anonymous"]
    ordering_fields = ["created_at", "amount"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return ChurchGiving.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


@extend_schema(tags=["Church - Giving"])
class GivingStatsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Aggregated giving totals by type and period",
        parameters=[
            OpenApiParameter("year", OpenApiTypes.INT, description="Filter by year"),
            OpenApiParameter("currency", OpenApiTypes.STR, description="Filter by currency"),
        ],
    )
    def get(self, request):
        qs = ChurchGiving.objects.filter(user=request.user)
        year = request.query_params.get("year")
        currency = request.query_params.get("currency")
        if year:
            qs = qs.filter(created_at__year=year)
        if currency:
            qs = qs.filter(currency=currency)

        by_type = list(
            qs.values("type").annotate(total=Sum("amount"), count=Count("id"))
        )
        grand_total = qs.aggregate(total=Sum("amount"))["total"] or 0

        return Response(
            {
                "grand_total": str(grand_total),
                "by_type": by_type,
                "currency": currency or "ALL",
                "year": year or "ALL",
            },
            status=status.HTTP_200_OK,
        )


@extend_schema(tags=["Church - Giving"])
class TithePledgeViewSet(ModelViewSet):
    serializer_class = TithePledgeSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["period", "is_active", "currency"]
    ordering_fields = ["created_at", "start_date"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return TithePledge.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


@extend_schema(tags=["Church - Giving"])
class OfferingCampaignViewSet(ModelViewSet):
    serializer_class = OfferingCampaignSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["is_active", "type", "currency"]
    search_fields = ["name", "description"]
    ordering_fields = ["created_at", "start_date", "raised_amount"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return OfferingCampaign.objects.all()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


@extend_schema(tags=["Church - Giving"])
class TitheStatementView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Annual tithe statement (JSON; PDF scaffold available on request)",
        parameters=[
            OpenApiParameter("year", OpenApiTypes.INT, required=True, description="Statement year"),
        ],
    )
    def get(self, request):
        year = request.query_params.get("year", timezone.now().year)
        try:
            year = int(year)
        except (TypeError, ValueError):
            return Response(
                {"detail": "Invalid year parameter."}, status=status.HTTP_400_BAD_REQUEST
            )

        givings = ChurchGiving.objects.filter(user=request.user, created_at__year=year)
        by_type = list(
            givings.values("type").annotate(total=Sum("amount"), count=Count("id"))
        )
        grand_total = givings.aggregate(total=Sum("amount"))["total"] or 0

        pledges = TithePledge.objects.filter(user=request.user, is_active=True)

        return Response(
            {
                "year": year,
                "user": str(request.user.pk),
                "grand_total": str(grand_total),
                "by_type": by_type,
                "pledges": TithePledgeSerializer(pledges, many=True).data,
                "note": "PDF generation scaffold — integrate with WeasyPrint or similar.",
            },
            status=status.HTTP_200_OK,
        )


# ─── MEMBERSHIP ───────────────────────────────────────────────────────────────

@extend_schema(tags=["Church - Membership"])
class ChurchMembershipViewSet(ModelViewSet):
    serializer_class = ChurchMembershipSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["church_id", "tier", "status"]
    ordering_fields = ["created_at", "join_date"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return ChurchMembership.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


@extend_schema(tags=["Church - Membership"])
class AttendanceRecordViewSet(ModelViewSet):
    serializer_class = AttendanceRecordSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["church_id", "type", "check_in_method", "date"]
    ordering_fields = ["date", "created_at"]
    ordering = ["-date"]

    def get_queryset(self):
        return AttendanceRecord.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @extend_schema(
        summary="QR code check-in endpoint",
        request=AttendanceRecordSerializer,
    )
    @action(detail=False, methods=["post"], url_path="checkin")
    def checkin(self, request):
        data = request.data.copy()
        data["check_in_method"] = "qr"
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        serializer.save(user=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["Church - Membership"])
class ChurchLifeEventViewSet(ModelViewSet):
    serializer_class = ChurchLifeEventSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["church_id", "type"]
    ordering_fields = ["event_date", "created_at"]
    ordering = ["-event_date"]

    def get_queryset(self):
        return ChurchLifeEvent.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


@extend_schema(tags=["Church - Membership"])
class MemberBirthdayAnniversaryViewSet(ModelViewSet):
    serializer_class = MemberBirthdayAnniversarySerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["type", "notify_church"]
    ordering_fields = ["date", "created_at"]
    ordering = ["date"]

    def get_queryset(self):
        return MemberBirthdayAnniversary.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


# ─── SMALL GROUPS ─────────────────────────────────────────────────────────────

@extend_schema(tags=["Church - Small Groups"])
class SmallGroupViewSet(ModelViewSet):
    serializer_class = SmallGroupSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["church_id", "type", "is_active", "district", "zone", "region"]
    search_fields = ["name", "description"]
    ordering_fields = ["name", "created_at"]
    ordering = ["name"]

    def get_queryset(self):
        return SmallGroup.objects.all()

    def perform_create(self, serializer):
        serializer.save()

    @extend_schema(summary="Join a small group")
    @action(detail=True, methods=["post"], url_path="join")
    def join(self, request, pk=None):
        group = self.get_object()
        membership, created = SmallGroupMembership.objects.get_or_create(
            group=group,
            user=request.user,
            defaults={"role": "member"},
        )
        if not created:
            return Response(
                {"detail": "Already a member of this group."}, status=status.HTTP_200_OK
            )
        serializer = SmallGroupMembershipSerializer(membership)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(summary="Leave a small group")
    @action(detail=True, methods=["post"], url_path="leave")
    def leave(self, request, pk=None):
        group = self.get_object()
        deleted, _ = SmallGroupMembership.objects.filter(group=group, user=request.user).delete()
        if deleted:
            return Response({"detail": "Left the group."}, status=status.HTTP_200_OK)
        return Response({"detail": "Not a member of this group."}, status=status.HTTP_404_NOT_FOUND)


@extend_schema(tags=["Church - Small Groups"])
class SmallGroupMembershipViewSet(ModelViewSet):
    serializer_class = SmallGroupMembershipSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["group", "user", "role"]
    ordering_fields = ["join_date", "created_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return SmallGroupMembership.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


@extend_schema(tags=["Church - Small Groups"])
class SmallGroupAttendanceViewSet(ModelViewSet):
    serializer_class = SmallGroupAttendanceSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["group", "date"]
    ordering_fields = ["date", "created_at"]
    ordering = ["-date"]

    def get_queryset(self):
        return SmallGroupAttendance.objects.all()


# ─── DISCIPLESHIP ─────────────────────────────────────────────────────────────

@extend_schema(tags=["Church - Discipleship"])
class DiscipleshipJourneyViewSet(ModelViewSet):
    serializer_class = DiscipleshipJourneySerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["stage", "is_active"]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return DiscipleshipJourney.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


@extend_schema(tags=["Church - Discipleship"])
class SpiritualGiftsAssessmentViewSet(ModelViewSet):
    serializer_class = SpiritualGiftsAssessmentSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ["completed_at", "created_at"]
    ordering = ["-completed_at"]

    def get_queryset(self):
        return SpiritualGiftsAssessment.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @extend_schema(summary="Submit a spiritual gifts assessment")
    @action(detail=False, methods=["post"], url_path="submit")
    def submit(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(user=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["Church - Discipleship"])
class AccountabilityPartnerViewSet(ModelViewSet):
    serializer_class = AccountabilityPartnerSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["is_active"]
    ordering_fields = ["started_at", "created_at"]
    ordering = ["-started_at"]

    def get_queryset(self):
        return AccountabilityPartner.objects.filter(
            Q(user1=self.request.user) | Q(user2=self.request.user)
        )

    def perform_create(self, serializer):
        serializer.save(user1=self.request.user)


# ─── PRAYER ───────────────────────────────────────────────────────────────────

@extend_schema(tags=["Church - Prayer"])
class PrayerRequestViewSet(ModelViewSet):
    serializer_class = PrayerRequestSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["privacy", "is_answered", "category"]
    search_fields = ["text", "category"]
    ordering_fields = ["created_at", "prayer_count"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return PrayerRequest.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @extend_schema(summary="Increment prayer count for a request")
    @action(detail=True, methods=["post"], url_path="pray")
    def pray(self, request, pk=None):
        prayer = self.get_object()
        prayer.prayer_count += 1
        prayer.save(update_fields=["prayer_count"])
        return Response(
            {"detail": "Prayed.", "prayer_count": prayer.prayer_count},
            status=status.HTTP_200_OK,
        )


@extend_schema(tags=["Church - Prayer"])
class PrayerWallView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get public prayer wall entries",
        parameters=[
            OpenApiParameter("church_id", OpenApiTypes.UUID, description="Filter by church"),
        ],
    )
    def get(self, request):
        qs = PrayerWallEntry.objects.filter(is_public=True)
        church_id = request.query_params.get("church_id")
        if church_id:
            qs = qs.filter(church_id=church_id)
        serializer = PrayerWallEntrySerializer(qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(summary="Post a new prayer wall entry")
    def post(self, request):
        serializer = PrayerWallEntrySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(user=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["Church - Prayer"])
class FastingRecordViewSet(ModelViewSet):
    serializer_class = FastingRecordSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["type", "is_community", "is_active"]
    ordering_fields = ["start_date", "created_at"]
    ordering = ["-start_date"]

    def get_queryset(self):
        return FastingRecord.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


@extend_schema(tags=["Church - Prayer"])
class PrayerChainSlotViewSet(ModelViewSet):
    serializer_class = PrayerChainSlotSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["church_id", "is_filled"]
    ordering_fields = ["start_time", "created_at"]
    ordering = ["start_time"]

    def get_queryset(self):
        return PrayerChainSlot.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


# ─── WORSHIP ──────────────────────────────────────────────────────────────────

@extend_schema(tags=["Church - Worship"])
class SongViewSet(ModelViewSet):
    serializer_class = SongSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["church_id", "key"]
    search_fields = ["title", "artist", "ccli_number"]
    ordering_fields = ["title", "created_at"]
    ordering = ["title"]

    def get_queryset(self):
        return Song.objects.all()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


@extend_schema(tags=["Church - Worship"])
class SetListViewSet(ModelViewSet):
    serializer_class = SetListSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["church_id", "service_date"]
    search_fields = ["title", "notes"]
    ordering_fields = ["service_date", "created_at"]
    ordering = ["-service_date"]

    def get_queryset(self):
        return SetList.objects.all()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


# ─── MINISTRY & OUTREACH ──────────────────────────────────────────────────────

@extend_schema(tags=["Church - Ministry"])
class MinistryDepartmentViewSet(ModelViewSet):
    serializer_class = MinistryDepartmentSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["church_id", "type", "is_active"]
    search_fields = ["name", "description"]
    ordering_fields = ["name", "created_at"]
    ordering = ["name"]

    def get_queryset(self):
        return MinistryDepartment.objects.all()


@extend_schema(tags=["Church - Ministry"])
class VolunteerSignupViewSet(ModelViewSet):
    serializer_class = VolunteerSignupSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["church_id", "department", "status", "event_date"]
    ordering_fields = ["event_date", "created_at"]
    ordering = ["-event_date"]

    def get_queryset(self):
        return VolunteerSignup.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


@extend_schema(tags=["Church - Ministry"])
class OutreachCampaignViewSet(ModelViewSet):
    serializer_class = OutreachCampaignSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["church_id", "type"]
    search_fields = ["title", "description"]
    ordering_fields = ["start_date", "created_at"]
    ordering = ["-start_date"]

    def get_queryset(self):
        return OutreachCampaign.objects.all()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


@extend_schema(tags=["Church - Ministry"])
class EvangelismRecordViewSet(ModelViewSet):
    serializer_class = EvangelismRecordSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["church_id", "date"]
    ordering_fields = ["date", "created_at"]
    ordering = ["-date"]

    def get_queryset(self):
        return EvangelismRecord.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @extend_schema(summary="Aggregate evangelism impact stats")
    @action(detail=False, methods=["get"], url_path="impact")
    def impact(self, request):
        qs = EvangelismRecord.objects.filter(user=request.user)
        church_id = request.query_params.get("church_id")
        if church_id:
            qs = qs.filter(church_id=church_id)
        totals = qs.aggregate(
            total_shares=Sum("shares"),
            total_follow_ups=Sum("follow_ups"),
            total_salvations=Sum("salvations"),
            total_records=Count("id"),
        )
        return Response(
            {
                "total_shares": totals["total_shares"] or 0,
                "total_follow_ups": totals["total_follow_ups"] or 0,
                "total_salvations": totals["total_salvations"] or 0,
                "total_records": totals["total_records"] or 0,
            },
            status=status.HTTP_200_OK,
        )
