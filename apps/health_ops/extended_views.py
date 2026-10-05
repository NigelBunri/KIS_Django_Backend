from datetime import date, timedelta

from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import filters, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAdminUser

from apps.verification.constants import VerificationSubjectType
from apps.verification.models import VerificationCase
from apps.verification.services import (
    current_practitioner_verification_status,
    review_practitioner_case,
    serialize_case_status,
    start_practitioner_verification_case,
)

from .views import _can_manage_institution, _is_institution_member
from .hospital_proximity import get_hospital_proximity_provider, is_hospital_proximity_configured

from .extended_models import (
    AddictionRecoveryGroup,
    Allergy,
    BabyMilestone,
    BloodTypeRegistry,
    Condition,
    ConsultReview,
    EMedication,
    EmergencyAlert,
    HealthGoal,
    HealthPractitioner,
    Immunization,
    MentalHealthJournal,
    MentalHealthSession,
    MoodEntry,
    PregnancyTracker,
    RecoveryMembership,
    RecoveryMilestone,
    TelemedicineConsult,
)
from .extended_serializers import (
    AddictionRecoveryGroupSerializer,
    AllergySerializer,
    BabyMilestoneSerializer,
    BloodTypeRegistrySerializer,
    ConditionSerializer,
    ConsultReviewSerializer,
    EMedicationSerializer,
    EmergencyAlertSerializer,
    HealthGoalProgressSerializer,
    HealthGoalSerializer,
    HealthPractitionerSerializer,
    ImmunizationSerializer,
    MentalHealthJournalSerializer,
    MentalHealthSessionSerializer,
    MoodEntrySerializer,
    PractitionerDirectoryEntrySerializer,
    PregnancyTrackerSerializer,
    RecoveryMembershipSerializer,
    RecoveryMilestoneSerializer,
    ReviewPractitionerCaseSerializer,
    SOSAlertSerializer,
    StartPractitionerVerificationSerializer,
    SymptomsCheckSerializer,
    TelemedicineConsultSerializer,
)

# ---------------------------------------------------------------------------
# Static data helpers
# ---------------------------------------------------------------------------

CRISIS_HOTLINES = {
    "US": [
        {"name": "National Suicide Prevention Lifeline", "number": "988", "hours": "24/7"},
        {"name": "Crisis Text Line", "number": "Text HOME to 741741", "hours": "24/7"},
    ],
    # Keyed under both the real ISO-3166 code ("GB", what the RN country
    # picker actually sends) and the common colloquial "UK" — previously
    # only "UK" existed, so selecting GB silently fell through to the
    # generic DEFAULT entry instead of these real, detailed hotlines.
    "GB": [
        {"name": "Samaritans", "number": "116 123", "hours": "24/7"},
        {"name": "PAPYRUS", "number": "0800 068 4141", "hours": "9am-midnight"},
    ],
    "UK": [
        {"name": "Samaritans", "number": "116 123", "hours": "24/7"},
        {"name": "PAPYRUS", "number": "0800 068 4141", "hours": "9am-midnight"},
    ],
    "AU": [
        {"name": "Lifeline Australia", "number": "13 11 14", "hours": "24/7"},
        {"name": "Beyond Blue", "number": "1300 22 4636", "hours": "24/7"},
    ],
    "NG": [
        {"name": "Mentally Aware Nigeria Initiative (MANI)", "number": "08091116264", "hours": "24/7"},
    ],
    "ZA": [
        {"name": "South African Depression and Anxiety Group (SADAG)", "number": "0800 567 567", "hours": "24/7"},
    ],
    "DEFAULT": [
        {"name": "International Association for Suicide Prevention", "url": "https://www.iasp.info/resources/Crisis_Centres/", "hours": "See directory"},
    ],
}

_TRIAGE_SEEK_CARE_LABEL = {
    "immediate": "Immediately",
    "within_24h": "Within 24 hours",
    "routine": "At your next routine opportunity",
}

# `triage_level`/`recommendations` match the RN app's CheckResult contract
# (SymptomCheckerScreen.tsx) exactly — this previously used different key
# names (level/recommendation) and a disjoint set of level values, which the
# screen could not read at all and crashed on (TRIAGE_LABELS[level] falls
# through to `level.toUpperCase()`, which throws when level is undefined).
MOCK_TRIAGE_RULES = [
    {
        "keywords": ["chest pain", "heart attack", "can't breathe", "difficulty breathing"],
        "triage_level": "emergency",
        "recommendations": ["Call emergency services (911/999) immediately. Do not drive yourself."],
        "urgency": "immediate",
    },
    {
        "keywords": ["fever", "temperature", "flu", "cough", "cold", "sore throat"],
        "triage_level": "moderate",
        "recommendations": ["Rest, stay hydrated.", "Consult a doctor if symptoms worsen or persist beyond 3 days."],
        "urgency": "within_24h",
    },
    {
        "keywords": ["headache", "migraine", "nausea", "vomiting", "dizziness"],
        "triage_level": "moderate",
        "recommendations": ["Monitor symptoms.", "Visit a clinic if they persist or are severe."],
        "urgency": "within_24h",
    },
    {
        "keywords": ["rash", "itch", "skin", "allergy"],
        "triage_level": "mild",
        "recommendations": ["Consider an antihistamine.", "Schedule a dermatology or GP appointment."],
        "urgency": "routine",
    },
]


def _triage_symptoms(symptoms: list[str]) -> dict:
    lowered = [s.lower() for s in symptoms]
    for rule in MOCK_TRIAGE_RULES:
        if any(kw in " ".join(lowered) for kw in rule["keywords"]):
            matched = rule
            break
    else:
        matched = {
            "triage_level": "moderate",
            "recommendations": ["Unable to match symptoms to a known pattern. Please consult a doctor."],
            "urgency": "routine",
        }
    return {
        "triage_level": matched["triage_level"],
        "recommendations": matched["recommendations"],
        "seek_care_within": _TRIAGE_SEEK_CARE_LABEL.get(matched["urgency"], "Routine"),
        "matched_symptoms": symptoms,
        "disclaimer": (
            "This triage is rule-based keyword matching, not a real AI model or a "
            "clinical diagnosis. Always consult a qualified healthcare professional."
        ),
    }


# ---------------------------------------------------------------------------
# Telemedicine
# ---------------------------------------------------------------------------

@extend_schema(tags=["Health — Telemedicine"])
class TelemedicineConsultViewSet(ModelViewSet):
    serializer_class = TelemedicineConsultSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["status", "type", "specialty"]
    ordering_fields = ["created_at", "scheduled_at"]

    def get_queryset(self):
        user = self.request.user
        return TelemedicineConsult.objects.filter(
            patient=user
        ).select_related("patient", "doctor")

    def perform_create(self, serializer):
        serializer.save(patient=self.request.user)

    @extend_schema(
        summary="Start a telemedicine consult",
        responses={200: TelemedicineConsultSerializer},
    )
    @action(detail=True, methods=["post"], url_path="start")
    def start(self, request, pk=None):
        consult = self.get_object()
        if consult.status not in (
            TelemedicineConsult.ConsultStatus.CONFIRMED,
            TelemedicineConsult.ConsultStatus.REQUESTED,
        ):
            return Response(
                {"detail": "Consult cannot be started in its current status."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        consult.status = TelemedicineConsult.ConsultStatus.IN_PROGRESS
        consult.started_at = timezone.now()
        consult.save(update_fields=["status", "started_at", "updated_at"])
        return Response(TelemedicineConsultSerializer(consult).data)

    @extend_schema(
        summary="Complete a telemedicine consult",
        responses={200: TelemedicineConsultSerializer},
    )
    @action(detail=True, methods=["post"], url_path="complete")
    def complete(self, request, pk=None):
        consult = self.get_object()
        if consult.status != TelemedicineConsult.ConsultStatus.IN_PROGRESS:
            return Response(
                {"detail": "Only in-progress consults can be completed."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        consult.status = TelemedicineConsult.ConsultStatus.COMPLETED
        consult.ended_at = timezone.now()
        consult.save(update_fields=["status", "ended_at", "updated_at"])
        return Response(TelemedicineConsultSerializer(consult).data)


@extend_schema(tags=["Health — Telemedicine"])
class ConsultReviewViewSet(ModelViewSet):
    serializer_class = ConsultReviewSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ConsultReview.objects.filter(reviewer=self.request.user)

    def perform_create(self, serializer):
        serializer.save(reviewer=self.request.user)


@extend_schema(tags=["Health — Practitioner Verification"])
class MyPractitionerProfileView(APIView):
    """Self-service profile for the authenticated user's own HealthPractitioner
    record. A patient-facing call never reaches this view — see
    PractitionerDirectoryView / PractitionerDirectoryEntrySerializer for the
    narrow, public-safe shape."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        practitioner = HealthPractitioner.objects.filter(user=request.user).first()
        if not practitioner:
            return Response({"detail": "No practitioner profile."}, status=status.HTTP_404_NOT_FOUND)
        return Response(HealthPractitionerSerializer(practitioner).data)

    def _check_claimed_institution(self, request, serializer):
        institution = serializer.validated_data.get("institution")
        if institution is None:
            return None
        if not _is_institution_member(request.user, institution):
            return Response(
                {"institution": "You are not a member of this institution."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return None

    def post(self, request):
        if HealthPractitioner.objects.filter(user=request.user).exists():
            return Response(
                {"detail": "Practitioner profile already exists."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = HealthPractitionerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        denied = self._check_claimed_institution(request, serializer)
        if denied:
            return denied
        practitioner = serializer.save(user=request.user)
        return Response(HealthPractitionerSerializer(practitioner).data, status=status.HTTP_201_CREATED)

    def patch(self, request):
        practitioner = HealthPractitioner.objects.filter(user=request.user).first()
        if not practitioner:
            return Response({"detail": "No practitioner profile."}, status=status.HTTP_404_NOT_FOUND)
        serializer = HealthPractitionerSerializer(practitioner, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        denied = self._check_claimed_institution(request, serializer)
        if denied:
            return denied
        serializer.save()
        return Response(serializer.data)


@extend_schema(
    tags=["Health — Practitioner Verification"],
    summary="Directory of verifiable practitioners",
    parameters=[
        OpenApiParameter("specialty", str, description="Filter by specialty"),
        OpenApiParameter("profession_type", str, description="Filter by profession type"),
        OpenApiParameter("institution_id", str, description="Filter by institution"),
    ],
)
class PractitionerDirectoryView(APIView):
    """Patient-facing discovery endpoint — replaces the old is_staff-based
    DoctorDirectoryView scaffold with real HealthPractitioner rows and real
    verification badges. Never serializes license_number, registration_authority,
    or verification evidence (see PractitionerDirectoryEntrySerializer)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = HealthPractitioner.objects.filter(is_active=True).select_related("institution")
        specialty = request.query_params.get("specialty")
        profession_type = request.query_params.get("profession_type")
        institution_id = request.query_params.get("institution_id")
        if specialty:
            qs = qs.filter(specialty__icontains=specialty)
        if profession_type:
            qs = qs.filter(profession_type=profession_type)
        if institution_id:
            qs = qs.filter(institution_id=institution_id)
        practitioners = list(qs.order_by("legal_name")[:50])
        return Response({"results": PractitionerDirectoryEntrySerializer(practitioners, many=True).data})


class PractitionerVerificationStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, practitioner_id):
        practitioner = get_object_or_404(HealthPractitioner, id=practitioner_id)
        return Response(current_practitioner_verification_status(practitioner))


class PractitionerVerificationStartView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, practitioner_id):
        practitioner = get_object_or_404(HealthPractitioner, id=practitioner_id)
        is_self = practitioner.user_id == request.user.id
        can_manage_institution = bool(
            practitioner.institution_id
        ) and _can_manage_institution(request.user, practitioner.institution)
        if not (is_self or can_manage_institution):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        serializer = StartPractitionerVerificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        case = start_practitioner_verification_case(
            practitioner=practitioner,
            actor=request.user,
            evidence_metadata=serializer.validated_data.get("evidence_metadata") or {},
        )
        return Response(
            {
                "case": serialize_case_status(case),
                "status": current_practitioner_verification_status(practitioner),
            },
            status=status.HTTP_201_CREATED,
        )


class PractitionerVerificationReviewView(APIView):
    permission_classes = [IsAdminUser]

    @transaction.atomic
    def post(self, request, practitioner_id, case_id):
        practitioner = get_object_or_404(HealthPractitioner, id=practitioner_id)
        case = VerificationCase.objects.select_related("subject").filter(
            id=case_id,
            subject__subject_type=VerificationSubjectType.HEALTH_PRACTITIONER,
            subject__subject_id=practitioner.id,
        ).first()
        if not case:
            raise ValidationError({"case_id": "Invalid health practitioner verification case."})
        serializer = ReviewPractitionerCaseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        case, badges = review_practitioner_case(
            case=case,
            actor=request.user,
            action=serializer.validated_data["action"],
            notes=serializer.validated_data.get("notes", ""),
            badge_codes=serializer.validated_data.get("badge_codes") or None,
        )
        return Response(
            {
                "case": serialize_case_status(case),
                "badges": [{"code": badge.code, "label": badge.label, "level": badge.level} for badge in badges],
            },
            status=status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# Mental Health
# ---------------------------------------------------------------------------

@extend_schema(tags=["Health — Mental Health"])
class MentalHealthSessionViewSet(ModelViewSet):
    serializer_class = MentalHealthSessionSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["status", "type", "modality"]
    ordering_fields = ["created_at", "scheduled_at"]

    def get_queryset(self):
        return MentalHealthSession.objects.filter(patient=self.request.user)

    def perform_create(self, serializer):
        serializer.save(patient=self.request.user)


@extend_schema(tags=["Health — Mental Health"])
class MoodEntryViewSet(ModelViewSet):
    serializer_class = MoodEntrySerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["entry_date"]
    ordering_fields = ["entry_date", "created_at"]

    def get_queryset(self):
        return MoodEntry.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @extend_schema(
        summary="30-day mood trends",
        responses={200: {"type": "object"}},
    )
    @action(detail=False, methods=["get"], url_path="trends")
    def trends(self, request):
        since = date.today() - timedelta(days=30)
        entries = MoodEntry.objects.filter(
            user=request.user, entry_date__gte=since
        ).order_by("entry_date").values("entry_date", "mood_score", "emotion_tags")
        data = list(entries)
        scores = [e["mood_score"] for e in data]
        average = round(sum(scores) / len(scores), 2) if scores else None
        return Response(
            {
                "period_days": 30,
                "entry_count": len(data),
                "average_mood": average,
                "entries": data,
            }
        )


@extend_schema(tags=["Health — Mental Health"])
class MentalHealthJournalViewSet(ModelViewSet):
    serializer_class = MentalHealthJournalSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ["entry_date", "created_at"]

    def get_queryset(self):
        # Private — only the author ever sees their own entries
        return MentalHealthJournal.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


# ---------------------------------------------------------------------------
# Addiction Recovery
# ---------------------------------------------------------------------------

@extend_schema(tags=["Health — Addiction Recovery"])
class AddictionRecoveryGroupViewSet(ModelViewSet):
    serializer_class = AddictionRecoveryGroupSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["addiction_type", "is_active"]
    search_fields = ["name", "description"]
    ordering_fields = ["created_at", "name"]

    def get_queryset(self):
        return AddictionRecoveryGroup.objects.filter(is_active=True)

    @extend_schema(summary="Join a recovery group")
    @action(detail=True, methods=["post"], url_path="join")
    def join(self, request, pk=None):
        group = self.get_object()
        membership, created = RecoveryMembership.objects.get_or_create(
            group=group, user=request.user
        )
        serializer = RecoveryMembershipSerializer(membership)
        code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(serializer.data, status=code)

    @extend_schema(summary="Leave a recovery group")
    @action(detail=True, methods=["post"], url_path="leave")
    def leave(self, request, pk=None):
        group = self.get_object()
        deleted, _ = RecoveryMembership.objects.filter(
            group=group, user=request.user
        ).delete()
        if deleted:
            return Response({"detail": "Left the group."}, status=status.HTTP_204_NO_CONTENT)
        return Response({"detail": "You are not a member of this group."}, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(tags=["Health — Addiction Recovery"])
class RecoveryMilestoneViewSet(ModelViewSet):
    serializer_class = RecoveryMilestoneSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["milestone_type", "addiction_type"]
    ordering_fields = ["celebrated_at", "days_sober"]

    def get_queryset(self):
        return RecoveryMilestone.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @extend_schema(
        summary="Recovery streaks for the authenticated user",
        responses={200: {"type": "object"}},
    )
    @action(detail=False, methods=["get"], url_path="streaks")
    def streaks(self, request):
        milestones = RecoveryMilestone.objects.filter(
            user=request.user
        ).order_by("-days_sober")
        best = milestones.first()
        return Response(
            {
                "total_milestones": milestones.count(),
                "best_days_sober": best.days_sober if best else 0,
                "latest_milestone": RecoveryMilestoneSerializer(best).data if best else None,
                "all_milestones": RecoveryMilestoneSerializer(milestones, many=True).data,
            }
        )


# ---------------------------------------------------------------------------
# Pregnancy & Baby
# ---------------------------------------------------------------------------

@extend_schema(tags=["Health — Maternal & Child"])
class PregnancyTrackerViewSet(ModelViewSet):
    serializer_class = PregnancyTrackerSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["is_active"]
    ordering_fields = ["due_date", "created_at"]

    def get_queryset(self):
        return PregnancyTracker.objects.filter(patient=self.request.user)

    def perform_create(self, serializer):
        serializer.save(patient=self.request.user)

    @extend_schema(
        summary="Update pregnancy week",
        request={"application/json": {"type": "object", "properties": {"current_week": {"type": "integer"}}}},
        responses={200: PregnancyTrackerSerializer},
    )
    @action(detail=True, methods=["post"], url_path="update-week")
    def update_week(self, request, pk=None):
        tracker = self.get_object()
        week = request.data.get("current_week")
        if week is None:
            return Response(
                {"detail": "current_week is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            tracker.current_week = int(week)
        except (TypeError, ValueError):
            return Response(
                {"detail": "current_week must be a whole number."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        tracker.save(update_fields=["current_week", "updated_at"])
        return Response(PregnancyTrackerSerializer(tracker).data)


@extend_schema(tags=["Health — Maternal & Child"])
class BabyMilestoneViewSet(ModelViewSet):
    serializer_class = BabyMilestoneSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["milestone_type"]
    ordering_fields = ["milestone_date", "created_at"]

    def get_queryset(self):
        return BabyMilestone.objects.filter(patient=self.request.user)

    def perform_create(self, serializer):
        serializer.save(patient=self.request.user)


# ---------------------------------------------------------------------------
# Blood Type Registry
# ---------------------------------------------------------------------------

@extend_schema(tags=["Health — Blood Registry"])
class BloodTypeRegistryViewSet(ModelViewSet):
    serializer_class = BloodTypeRegistrySerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["blood_type", "is_available_to_donate", "location_country"]

    def get_queryset(self):
        return BloodTypeRegistry.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @extend_schema(
        summary="Find blood donors",
        parameters=[
            OpenApiParameter("blood_type", str, description="e.g. O_POS"),
            OpenApiParameter("country", str, description="Country name or code"),
        ],
        responses={200: {"type": "object"}},
    )
    @action(detail=False, methods=["get"], url_path="donors")
    def donors(self, request):
        blood_type = request.query_params.get("blood_type")
        country = request.query_params.get("country")
        qs = BloodTypeRegistry.objects.filter(
            is_public=True, is_available_to_donate=True
        ).select_related("user")
        if blood_type:
            qs = qs.filter(blood_type=blood_type)
        if country:
            qs = qs.filter(location_country__iexact=country)
        donors = [
            {
                "id": str(r.id),
                "blood_type": r.blood_type,
                "location_city": r.location_city,
                "location_country": r.location_country,
                "last_donation_date": str(r.last_donation_date) if r.last_donation_date else None,
            }
            for r in qs[:100]
        ]
        return Response({"count": len(donors), "results": donors})


# ---------------------------------------------------------------------------
# E-Medication
# ---------------------------------------------------------------------------

@extend_schema(tags=["Health — Medications"])
class EMedicationViewSet(ModelViewSet):
    serializer_class = EMedicationSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["is_active"]
    search_fields = ["name", "prescribed_by"]
    ordering_fields = ["refill_due", "start_date", "created_at"]

    def get_queryset(self):
        return EMedication.objects.filter(patient=self.request.user)

    def perform_create(self, serializer):
        serializer.save(patient=self.request.user)


# ---------------------------------------------------------------------------
# Patient clinical record — Condition / Allergy / Immunization
# Self-service only for now — see the scope note on these models in
# extended_models.py and KIS_HEALTH_CHECKLIST.md Section 4.
# ---------------------------------------------------------------------------

@extend_schema(tags=["Health — Patient Record"])
class ConditionViewSet(ModelViewSet):
    serializer_class = ConditionSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["status", "is_active"]
    ordering_fields = ["onset_date", "created_at"]

    def get_queryset(self):
        return Condition.objects.filter(patient=self.request.user)

    def perform_create(self, serializer):
        serializer.save(patient=self.request.user)


@extend_schema(tags=["Health — Patient Record"])
class AllergyViewSet(ModelViewSet):
    serializer_class = AllergySerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["severity", "is_active"]
    ordering_fields = ["created_at"]

    def get_queryset(self):
        return Allergy.objects.filter(patient=self.request.user)

    def perform_create(self, serializer):
        serializer.save(patient=self.request.user)


@extend_schema(tags=["Health — Patient Record"])
class ImmunizationViewSet(ModelViewSet):
    serializer_class = ImmunizationSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    ordering_fields = ["administered_date", "next_dose_due", "created_at"]

    def get_queryset(self):
        return Immunization.objects.filter(patient=self.request.user)

    def perform_create(self, serializer):
        serializer.save(patient=self.request.user)

    @extend_schema(
        summary="Medications with refill due today or overdue",
        responses={200: EMedicationSerializer(many=True)},
    )
    @action(detail=False, methods=["get"], url_path="reminders")
    def reminders(self, request):
        today = date.today()
        qs = EMedication.objects.filter(
            patient=request.user,
            is_active=True,
            refill_due__lte=today,
        ).order_by("refill_due")
        serializer = EMedicationSerializer(qs, many=True)
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# Health Goals
# ---------------------------------------------------------------------------

@extend_schema(tags=["Health — Goals"])
class HealthGoalViewSet(ModelViewSet):
    serializer_class = HealthGoalSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["category", "is_achieved"]
    ordering_fields = ["deadline", "created_at"]

    def get_queryset(self):
        return HealthGoal.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @extend_schema(
        summary="Update goal progress",
        request=HealthGoalProgressSerializer,
        responses={200: HealthGoalSerializer},
    )
    @action(detail=True, methods=["post"], url_path="update-progress")
    def update_progress(self, request, pk=None):
        goal = self.get_object()
        serializer = HealthGoalProgressSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        goal.current_value = serializer.validated_data["current_value"]
        if goal.notes and serializer.validated_data.get("notes"):
            goal.notes = serializer.validated_data["notes"]
        elif serializer.validated_data.get("notes"):
            goal.notes = serializer.validated_data["notes"]
        if goal.target_value is not None and goal.current_value >= goal.target_value:
            goal.is_achieved = True
        goal.save(update_fields=["current_value", "notes", "is_achieved", "updated_at"])
        return Response(HealthGoalSerializer(goal).data)


# ---------------------------------------------------------------------------
# Emergency Alert
# ---------------------------------------------------------------------------

@extend_schema(tags=["Health — Emergency"])
class EmergencyAlertViewSet(ModelViewSet):
    serializer_class = EmergencyAlertSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["alert_type", "status"]
    ordering_fields = ["created_at"]

    def get_queryset(self):
        return EmergencyAlert.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


@extend_schema(
    tags=["Health — Emergency"],
    summary="Create SOS alert and return nearest hospitals via the configured provider",
    request=SOSAlertSerializer,
    responses={201: {"type": "object"}},
)
class SOSCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = SOSAlertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        alert = EmergencyAlert.objects.create(
            user=request.user,
            alert_type=EmergencyAlert.AlertType.SOS,
            latitude=data.get("latitude"),
            longitude=data.get("longitude"),
            address=data.get("address", ""),
            message=data.get("message", ""),
            status=EmergencyAlert.AlertStatus.ACTIVE,
        )

        provider = get_hospital_proximity_provider()
        nearby = provider.find_nearby(data.get("latitude"), data.get("longitude"))

        return Response(
            {
                "alert": EmergencyAlertSerializer(alert).data,
                "nearest_hospitals": [
                    {
                        "name": h.name,
                        "distance_km": h.distance_km,
                        "address": h.address,
                        "phone": h.phone,
                        "latitude": h.latitude,
                        "longitude": h.longitude,
                    }
                    for h in nearby
                ],
                "hospital_lookup_available": is_hospital_proximity_configured(),
            },
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# AI Symptoms Checker
# ---------------------------------------------------------------------------

@extend_schema(
    tags=["Health — AI Tools"],
    summary="AI-powered symptom triage (mock)",
    request=SymptomsCheckSerializer,
    responses={200: {"type": "object"}},
)
class AISymptomsCheckerView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = SymptomsCheckSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        symptoms = serializer.validated_data["symptoms"]
        result = _triage_symptoms(symptoms)
        return Response(result)


# ---------------------------------------------------------------------------
# Crisis Hotlines
# ---------------------------------------------------------------------------

@extend_schema(
    tags=["Health — Mental Health"],
    summary="Crisis hotlines by country",
    parameters=[OpenApiParameter("country", str, description="Two-letter or full country code (e.g. US, UK, NG)")],
    responses={200: {"type": "object"}},
)
class CrisisHotlineView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        country = request.query_params.get("country", "").upper()
        hotlines = CRISIS_HOTLINES.get(country, CRISIS_HOTLINES["DEFAULT"])
        return Response({"country": country or "DEFAULT", "hotlines": hotlines})
