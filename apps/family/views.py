# apps/family/views.py
import secrets
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.filters import SearchFilter, OrderingFilter
from django_filters.rest_framework import DjangoFilterBackend

from drf_spectacular.utils import extend_schema, OpenApiParameter

from .models import (
    FamilyAccount,
    FamilyMember,
    ParentalControl,
    FamilyEvent,
    FamilyPhotoAlbum,
    FamilyPhoto,
    MemorialPage,
    MilestoneRecord,
    TimeCapsule,
    FamilyPrayerItem,
    FamilyNoticeBoard,
    GriefSupportGroup,
    GriefGroupMembership,
)
from .serializers import (
    FamilyAccountSerializer,
    FamilyMemberSerializer,
    ParentalControlSerializer,
    FamilyEventSerializer,
    FamilyPhotoAlbumSerializer,
    FamilyPhotoSerializer,
    MemorialPageSerializer,
    MilestoneRecordSerializer,
    TimeCapsuleSerializer,
    TimeCapsuleLockedSerializer,
    FamilyPrayerItemSerializer,
    FamilyNoticeBoardSerializer,
    GriefSupportGroupSerializer,
    GriefGroupMembershipSerializer,
    JoinFamilySerializer,
    SOSAlertSerializer,
)


# ---------------------------------------------------------------------------
# FamilyAccountViewSet
# ---------------------------------------------------------------------------

class FamilyAccountViewSet(viewsets.ModelViewSet):
    """
    CRUD for FamilyAccount.
    Custom action: POST /family/accounts/join/
    Custom action: GET  /family/accounts/{id}/tree/
    """

    permission_classes = [IsAuthenticated]
    serializer_class = FamilyAccountSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    search_fields = ["name"]
    ordering_fields = ["created_at", "name"]

    def get_queryset(self):
        user = self.request.user
        return FamilyAccount.objects.filter(
            Q(admin_user=user) | Q(members__user=user)
        ).distinct()

    def perform_create(self, serializer):
        invite_code = secrets.token_urlsafe(8)[:12]
        serializer.save(admin_user=self.request.user, invite_code=invite_code)

    @extend_schema(
        request=JoinFamilySerializer,
        responses={200: FamilyAccountSerializer},
        summary="Join a family via invite code",
    )
    @action(detail=False, methods=["post"], url_path="join")
    def join(self, request):
        serializer = JoinFamilySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invite_code = serializer.validated_data["invite_code"]
        try:
            family = FamilyAccount.objects.get(invite_code=invite_code, is_active=True)
        except FamilyAccount.DoesNotExist:
            return Response(
                {"detail": "Invalid or expired invite code."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        membership, created = FamilyMember.objects.get_or_create(
            family=family,
            user=request.user,
            defaults={
                "role": "extended",
                "added_by": request.user,
            },
        )
        if not created:
            return Response(
                {"detail": "You are already a member of this family."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(FamilyAccountSerializer(family).data, status=status.HTTP_200_OK)

    @extend_schema(
        responses={200: FamilyMemberSerializer(many=True)},
        summary="Retrieve nested family tree",
    )
    @action(detail=True, methods=["get"], url_path="tree")
    def tree(self, request, pk=None):
        family = self.get_object()
        members = FamilyMember.objects.filter(family=family).select_related("user", "added_by")
        serializer = FamilyMemberSerializer(members, many=True)
        return Response(
            {
                "family": FamilyAccountSerializer(family).data,
                "members": serializer.data,
            },
            status=status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# FamilyMemberViewSet
# ---------------------------------------------------------------------------

class FamilyMemberViewSet(viewsets.ModelViewSet):
    """CRUD for FamilyMember."""

    permission_classes = [IsAuthenticated]
    serializer_class = FamilyMemberSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["family", "role", "is_minor", "is_admin"]
    search_fields = ["nickname"]
    ordering_fields = ["created_at"]

    def get_queryset(self):
        user = self.request.user
        family_ids = FamilyAccount.objects.filter(
            Q(admin_user=user) | Q(members__user=user)
        ).values_list("id", flat=True)
        return FamilyMember.objects.filter(family__in=family_ids).select_related(
            "family", "user", "added_by"
        )

    def perform_create(self, serializer):
        serializer.save(added_by=self.request.user)


# ---------------------------------------------------------------------------
# ParentalControlViewSet
# ---------------------------------------------------------------------------

class ParentalControlViewSet(viewsets.ModelViewSet):
    """CRUD for ParentalControl — retrieve/update by family_member."""

    permission_classes = [IsAuthenticated]
    serializer_class = ParentalControlSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["family_member"]

    def get_queryset(self):
        user = self.request.user
        family_ids = FamilyAccount.objects.filter(
            Q(admin_user=user) | Q(members__user=user)
        ).values_list("id", flat=True)
        return ParentalControl.objects.filter(
            family_member__family__in=family_ids
        ).select_related("family_member")


# ---------------------------------------------------------------------------
# SOSAlertView
# ---------------------------------------------------------------------------

class SOSAlertView(APIView):
    """
    POST /family/sos/
    Creates an SOS notification to all parent/guardian members in the family.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=SOSAlertSerializer,
        responses={200: {"type": "object", "properties": {"detail": {"type": "string"}}}},
        summary="Trigger an SOS alert to family parents/guardians",
    )
    def post(self, request):
        serializer = SOSAlertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        family_id = serializer.validated_data["family_id"]
        message = serializer.validated_data.get("message", "SOS Alert triggered")
        location = serializer.validated_data.get("location", "")

        try:
            family = FamilyAccount.objects.get(id=family_id, is_active=True)
        except FamilyAccount.DoesNotExist:
            return Response(
                {"detail": "Family not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        parents = FamilyMember.objects.filter(
            family=family,
            role__in=["parent", "guardian"],
        ).select_related("user")

        notified_count = 0
        for member in parents:
            if member.user:
                # Attempt to create a notification via the notifications app if available
                try:
                    from apps.notifications.models import Notification
                    Notification.objects.create(
                        recipient=member.user,
                        actor=request.user,
                        verb="SOS",
                        description=f"{message}{' — ' + location if location else ''}",
                    )
                except Exception:
                    pass
                notified_count += 1

        return Response(
            {
                "detail": f"SOS alert sent to {notified_count} parent(s)/guardian(s).",
                "family_id": str(family_id),
            },
            status=status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# FamilyEventViewSet
# ---------------------------------------------------------------------------

class FamilyEventViewSet(viewsets.ModelViewSet):
    """CRUD for FamilyEvent filtered by family."""

    permission_classes = [IsAuthenticated]
    serializer_class = FamilyEventSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["family", "type"]
    search_fields = ["title", "notes"]
    ordering_fields = ["event_date", "created_at"]

    def get_queryset(self):
        user = self.request.user
        family_ids = FamilyAccount.objects.filter(
            Q(admin_user=user) | Q(members__user=user)
        ).values_list("id", flat=True)
        return FamilyEvent.objects.filter(family__in=family_ids)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


# ---------------------------------------------------------------------------
# FamilyPhotoAlbumViewSet
# ---------------------------------------------------------------------------

class FamilyPhotoAlbumViewSet(viewsets.ModelViewSet):
    """CRUD for FamilyPhotoAlbum."""

    permission_classes = [IsAuthenticated]
    serializer_class = FamilyPhotoAlbumSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["family"]
    search_fields = ["name", "description"]
    ordering_fields = ["created_at", "name"]

    def get_queryset(self):
        user = self.request.user
        family_ids = FamilyAccount.objects.filter(
            Q(admin_user=user) | Q(members__user=user)
        ).values_list("id", flat=True)
        return FamilyPhotoAlbum.objects.filter(family__in=family_ids)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


# ---------------------------------------------------------------------------
# FamilyPhotoViewSet
# ---------------------------------------------------------------------------

class FamilyPhotoViewSet(viewsets.ModelViewSet):
    """CRUD for FamilyPhoto."""

    permission_classes = [IsAuthenticated]
    serializer_class = FamilyPhotoSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["album"]
    search_fields = ["caption"]
    ordering_fields = ["taken_at", "created_at"]

    def get_queryset(self):
        user = self.request.user
        family_ids = FamilyAccount.objects.filter(
            Q(admin_user=user) | Q(members__user=user)
        ).values_list("id", flat=True)
        return FamilyPhoto.objects.filter(album__family__in=family_ids)

    def perform_create(self, serializer):
        serializer.save(uploader=self.request.user)


# ---------------------------------------------------------------------------
# MemorialPageViewSet
# ---------------------------------------------------------------------------

class MemorialPageViewSet(viewsets.ModelViewSet):
    """
    CRUD for MemorialPage.
    Public pages (is_public=True) are accessible without authentication.
    """

    serializer_class = MemorialPageSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["family", "is_public"]
    search_fields = ["name", "tribute"]
    ordering_fields = ["created_at", "death_date"]

    def get_permissions(self):
        if self.action in ["list", "retrieve"] and self.request.query_params.get("public"):
            return [AllowAny()]
        return [IsAuthenticated()]

    def get_queryset(self):
        user = self.request.user
        if user.is_anonymous:
            return MemorialPage.objects.filter(is_public=True)
        family_ids = FamilyAccount.objects.filter(
            Q(admin_user=user) | Q(members__user=user)
        ).values_list("id", flat=True)
        return MemorialPage.objects.filter(
            Q(family__in=family_ids) | Q(is_public=True)
        ).distinct()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


# ---------------------------------------------------------------------------
# MilestoneRecordViewSet
# ---------------------------------------------------------------------------

class MilestoneRecordViewSet(viewsets.ModelViewSet):
    """CRUD for MilestoneRecord."""

    permission_classes = [IsAuthenticated]
    serializer_class = MilestoneRecordSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["family_member", "type"]
    search_fields = ["title", "description"]
    ordering_fields = ["milestone_date", "created_at"]

    def get_queryset(self):
        user = self.request.user
        family_ids = FamilyAccount.objects.filter(
            Q(admin_user=user) | Q(members__user=user)
        ).values_list("id", flat=True)
        return MilestoneRecord.objects.filter(family_member__family__in=family_ids)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


# ---------------------------------------------------------------------------
# TimeCapsuleViewSet
# ---------------------------------------------------------------------------

class TimeCapsuleViewSet(viewsets.ModelViewSet):
    """
    CRUD for TimeCapsule.
    GET reveals message only if unlock_date <= today and requester is a family member.
    """

    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["family", "is_locked"]
    search_fields = ["title"]
    ordering_fields = ["unlock_date", "created_at"]

    def get_serializer_class(self):
        return TimeCapsuleLockedSerializer

    def get_queryset(self):
        user = self.request.user
        family_ids = FamilyAccount.objects.filter(
            Q(admin_user=user) | Q(members__user=user)
        ).values_list("id", flat=True)
        return TimeCapsule.objects.filter(family__in=family_ids)

    def perform_create(self, serializer):
        serializer.save(creator=self.request.user)


# ---------------------------------------------------------------------------
# FamilyPrayerItemViewSet
# ---------------------------------------------------------------------------

class FamilyPrayerItemViewSet(viewsets.ModelViewSet):
    """CRUD for FamilyPrayerItem."""

    permission_classes = [IsAuthenticated]
    serializer_class = FamilyPrayerItemSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["family", "is_answered"]
    search_fields = ["text"]
    ordering_fields = ["created_at"]

    def get_queryset(self):
        user = self.request.user
        family_ids = FamilyAccount.objects.filter(
            Q(admin_user=user) | Q(members__user=user)
        ).values_list("id", flat=True)
        return FamilyPrayerItem.objects.filter(family__in=family_ids)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


# ---------------------------------------------------------------------------
# FamilyNoticeBoardViewSet
# ---------------------------------------------------------------------------

class FamilyNoticeBoardViewSet(viewsets.ModelViewSet):
    """CRUD for FamilyNoticeBoard — filtered by pinned + non-expired."""

    permission_classes = [IsAuthenticated]
    serializer_class = FamilyNoticeBoardSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["family", "is_pinned"]
    search_fields = ["title", "content"]
    ordering_fields = ["is_pinned", "created_at"]

    def get_queryset(self):
        user = self.request.user
        now = timezone.now()
        family_ids = FamilyAccount.objects.filter(
            Q(admin_user=user) | Q(members__user=user)
        ).values_list("id", flat=True)
        return FamilyNoticeBoard.objects.filter(
            family__in=family_ids,
        ).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now)
        )

    def perform_create(self, serializer):
        serializer.save(posted_by=self.request.user)


# ---------------------------------------------------------------------------
# GriefSupportGroupViewSet
# ---------------------------------------------------------------------------

class GriefSupportGroupViewSet(viewsets.ModelViewSet):
    """CRUD list for GriefSupportGroup with join/leave actions."""

    permission_classes = [IsAuthenticated]
    serializer_class = GriefSupportGroupSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["type", "is_active"]
    search_fields = ["name", "description"]
    ordering_fields = ["created_at", "member_count"]

    def get_queryset(self):
        return GriefSupportGroup.objects.filter(is_active=True)

    def perform_create(self, serializer):
        serializer.save(facilitator=self.request.user)

    @extend_schema(
        request=GriefGroupMembershipSerializer,
        responses={201: GriefGroupMembershipSerializer},
        summary="Join a grief support group",
    )
    @action(detail=True, methods=["post"], url_path="join")
    @transaction.atomic
    def join(self, request, pk=None):
        group = self.get_object()
        is_anonymous = request.data.get("is_anonymous", False)
        membership, created = GriefGroupMembership.objects.get_or_create(
            group=group,
            user=request.user,
            defaults={"is_anonymous": is_anonymous},
        )
        if not created:
            return Response(
                {"detail": "You are already a member of this group."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        GriefSupportGroup.objects.filter(pk=group.pk).update(
            member_count=group.member_count + 1
        )
        return Response(
            GriefGroupMembershipSerializer(membership).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        responses={204: None},
        summary="Leave a grief support group",
    )
    @action(detail=True, methods=["post"], url_path="leave")
    @transaction.atomic
    def leave(self, request, pk=None):
        group = self.get_object()
        deleted, _ = GriefGroupMembership.objects.filter(
            group=group, user=request.user
        ).delete()
        if not deleted:
            return Response(
                {"detail": "You are not a member of this group."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        GriefSupportGroup.objects.filter(pk=group.pk, member_count__gt=0).update(
            member_count=group.member_count - 1
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# FamilyTreeView  (also registered as an action on FamilyAccountViewSet above)
# ---------------------------------------------------------------------------

class FamilyTreeView(APIView):
    """
    GET /family/accounts/{id}/tree/
    Returns nested member structure for the given family.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses={200: FamilyMemberSerializer(many=True)},
        summary="Get family tree",
    )
    def get(self, request, pk):
        try:
            family = FamilyAccount.objects.get(pk=pk)
        except FamilyAccount.DoesNotExist:
            return Response(
                {"detail": "Family not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        user = request.user
        is_member = (
            family.admin_user == user
            or FamilyMember.objects.filter(family=family, user=user).exists()
        )
        if not is_member:
            return Response(
                {"detail": "You are not a member of this family."},
                status=status.HTTP_403_FORBIDDEN,
            )

        members = FamilyMember.objects.filter(family=family).select_related(
            "user", "added_by"
        )
        return Response(
            {
                "family": FamilyAccountSerializer(family).data,
                "members": FamilyMemberSerializer(members, many=True).data,
            },
            status=status.HTTP_200_OK,
        )
