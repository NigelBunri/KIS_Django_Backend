"""Clinical domain views: Encounter, ClinicalReferral, Laboratory.

Kept separate from views.py (7000+ lines) and extended_views.py — this is a
new, cohesive domain with its own authorization module
(clinical_authorization.py) and deserves its own file rather than growing
either existing one further.
"""
from __future__ import annotations

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User

from .clinical_authorization import (
    can_create_encounter,
    can_write_clinical_content,
    verified_practitioner_for_user,
)
from .extended_models import (
    Allergy,
    ClinicalRecordSource,
    ClinicalReferral,
    Condition,
    EMedication,
    Encounter,
    EncounterStatus,
    HealthPractitioner,
    Immunization,
    LabOrder,
    LabOrderStatus,
    LabResult,
    LabResultFlag,
    LabTestCatalogItem,
    ReferralStatus,
    Specimen,
)
from .extended_serializers import (
    AllergySerializer,
    ClinicalReferralActionSerializer,
    ClinicalReferralCreateSerializer,
    ClinicalReferralSerializer,
    ClinicianAllergyCreateSerializer,
    ClinicianConditionCreateSerializer,
    ClinicianImmunizationCreateSerializer,
    ClinicianPrescriptionCreateSerializer,
    ConditionSerializer,
    EMedicationSerializer,
    EncounterClinicalContentSerializer,
    EncounterCreateSerializer,
    EncounterSerializer,
    ImmunizationSerializer,
    LabOrderCreateSerializer,
    LabOrderSerializer,
    LabResultEntrySerializer,
    LabTestCatalogItemSerializer,
    SpecimenCollectSerializer,
)
from .models import HealthInstitution
from .views import _can_manage_institution, _is_institution_member


# ---------------------------------------------------------------------------
# Encounter
# ---------------------------------------------------------------------------

class EncounterListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        practitioner = HealthPractitioner.objects.filter(user=user).first()
        q = Encounter.objects.none()
        if practitioner:
            q = Encounter.objects.filter(practitioner=practitioner)
        q = q | Encounter.objects.filter(patient=user)
        encounters = q.distinct().order_by("-created_at")[:100]
        return Response({"results": EncounterSerializer(encounters, many=True).data})

    @transaction.atomic
    def post(self, request):
        serializer = EncounterCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        patient = get_object_or_404(User, id=data["patient"])
        institution = get_object_or_404(HealthInstitution, id=data["institution"])

        practitioner, error = can_create_encounter(request.user, patient=patient, institution=institution)
        if error:
            return Response({"detail": error}, status=status.HTTP_403_FORBIDDEN)

        encounter = Encounter.objects.create(
            patient=patient,
            practitioner=practitioner,
            institution=institution,
            encounter_type=data["encounter_type"],
            reason=data.get("reason", ""),
            scheduled_at=data.get("scheduled_at"),
        )
        return Response(EncounterSerializer(encounter).data, status=status.HTTP_201_CREATED)


def _can_view_encounter(user, encounter: Encounter) -> bool:
    if encounter.patient_id == user.id:
        return True
    if encounter.practitioner.user_id == user.id:
        return True
    return _can_manage_institution(user, encounter.institution)


class EncounterDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, encounter_id):
        encounter = get_object_or_404(Encounter.objects.select_related("practitioner", "institution"), id=encounter_id)
        if not _can_view_encounter(request.user, encounter):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        return Response(EncounterSerializer(encounter).data)

    @transaction.atomic
    def patch(self, request, encounter_id):
        encounter = get_object_or_404(Encounter.objects.select_related("practitioner"), id=encounter_id)
        if not can_write_clinical_content(request.user, encounter):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if encounter.status not in (EncounterStatus.IN_PROGRESS, EncounterStatus.COMPLETED):
            return Response(
                {"detail": "Clinical content can only be recorded while the encounter is in progress or completed."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = EncounterClinicalContentSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updates = []
        for field in ("notes", "assessment", "treatment_plan"):
            if field in serializer.validated_data:
                setattr(encounter, field, serializer.validated_data[field])
                updates.append(field)
        if updates:
            encounter.save(update_fields=[*updates, "updated_at"])
        return Response(EncounterSerializer(encounter).data)


_ENCOUNTER_TRANSITIONS = {
    "check_in": {"from": {EncounterStatus.SCHEDULED}, "to": EncounterStatus.CHECKED_IN},
    "start": {"from": {EncounterStatus.SCHEDULED, EncounterStatus.CHECKED_IN}, "to": EncounterStatus.IN_PROGRESS},
    "complete": {"from": {EncounterStatus.IN_PROGRESS}, "to": EncounterStatus.COMPLETED},
    "cancel": {"from": {EncounterStatus.SCHEDULED, EncounterStatus.CHECKED_IN}, "to": EncounterStatus.CANCELLED},
    "no_show": {"from": {EncounterStatus.SCHEDULED, EncounterStatus.CHECKED_IN}, "to": EncounterStatus.NO_SHOW},
    "close": {
        "from": {EncounterStatus.COMPLETED, EncounterStatus.CANCELLED, EncounterStatus.NO_SHOW},
        "to": EncounterStatus.CLOSED,
    },
}


class EncounterTransitionView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, encounter_id, action):
        encounter = get_object_or_404(Encounter.objects.select_related("practitioner"), id=encounter_id)
        if encounter.practitioner.user_id != request.user.id:
            return Response({"detail": "Only the encounter's own practitioner may change its status."}, status=status.HTTP_403_FORBIDDEN)
        rule = _ENCOUNTER_TRANSITIONS.get(action)
        if not rule:
            return Response({"detail": "Unknown transition."}, status=status.HTTP_400_BAD_REQUEST)
        if encounter.status not in rule["from"]:
            return Response(
                {"detail": f"Cannot {action} an encounter in status {encounter.status}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        now = timezone.now()
        encounter.status = rule["to"]
        update_fields = ["status", "updated_at"]
        if action == "start":
            encounter.started_at = now
            update_fields.append("started_at")
        elif action in ("complete", "cancel", "no_show"):
            encounter.ended_at = now
            update_fields.append("ended_at")
        elif action == "close":
            encounter.closed_at = now
            update_fields.append("closed_at")
        encounter.save(update_fields=update_fields)
        return Response(EncounterSerializer(encounter).data)


# ---------------------------------------------------------------------------
# Clinician-authored patient record writes — anchored to an Encounter.
#
# These are deliberately separate endpoints from the patient's own
# self-service ConditionViewSet/AllergyViewSet/ImmunizationViewSet
# (extended_views.py): that path always forces patient=request.user and
# source=self_reported and can never be used by a practitioner. This path
# always forces source=clinician_recorded, recorded_by/administered_by=the
# encounter's own practitioner, and requires the encounter to actually be
# open — a patient can never make their own entry masquerade as this, and
# a practitioner can never write into a patient's record without a real,
# data-backed encounter authorizing it.
# ---------------------------------------------------------------------------

def _encounter_clinical_write_guard(user, encounter: Encounter) -> str | None:
    if encounter.practitioner.user_id != user.id:
        return "Only the encounter's own practitioner may record clinical information."
    if encounter.status not in (EncounterStatus.IN_PROGRESS, EncounterStatus.COMPLETED):
        return "Clinical records can only be added while the encounter is in progress or completed."
    return None


class EncounterConditionListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, encounter_id):
        encounter = get_object_or_404(Encounter.objects.select_related("practitioner"), id=encounter_id)
        if not _can_view_encounter(request.user, encounter):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        rows = Condition.objects.filter(patient=encounter.patient).order_by("-created_at")
        return Response({"results": ConditionSerializer(rows, many=True).data})

    @transaction.atomic
    def post(self, request, encounter_id):
        encounter = get_object_or_404(Encounter.objects.select_related("practitioner"), id=encounter_id)
        error = _encounter_clinical_write_guard(request.user, encounter)
        if error:
            return Response({"detail": error}, status=status.HTTP_403_FORBIDDEN)
        serializer = ClinicianConditionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        condition = Condition.objects.create(
            patient=encounter.patient,
            recorded_by=encounter.practitioner,
            institution=encounter.institution,
            source=ClinicalRecordSource.CLINICIAN_RECORDED,
            name=data["name"],
            icd_code=data.get("icd_code", ""),
            status=data.get("status") or Condition._meta.get_field("status").default,
            onset_date=data.get("onset_date"),
            notes=data.get("notes", ""),
        )
        return Response(ConditionSerializer(condition).data, status=status.HTTP_201_CREATED)


class EncounterAllergyListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, encounter_id):
        encounter = get_object_or_404(Encounter.objects.select_related("practitioner"), id=encounter_id)
        if not _can_view_encounter(request.user, encounter):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        rows = Allergy.objects.filter(patient=encounter.patient).order_by("-created_at")
        return Response({"results": AllergySerializer(rows, many=True).data})

    @transaction.atomic
    def post(self, request, encounter_id):
        encounter = get_object_or_404(Encounter.objects.select_related("practitioner"), id=encounter_id)
        error = _encounter_clinical_write_guard(request.user, encounter)
        if error:
            return Response({"detail": error}, status=status.HTTP_403_FORBIDDEN)
        serializer = ClinicianAllergyCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        allergy = Allergy.objects.create(
            patient=encounter.patient,
            recorded_by=encounter.practitioner,
            institution=encounter.institution,
            source=ClinicalRecordSource.CLINICIAN_RECORDED,
            allergen=data["allergen"],
            reaction=data.get("reaction", ""),
            severity=data.get("severity") or Allergy._meta.get_field("severity").default,
        )
        return Response(AllergySerializer(allergy).data, status=status.HTTP_201_CREATED)


class EncounterImmunizationListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, encounter_id):
        encounter = get_object_or_404(Encounter.objects.select_related("practitioner"), id=encounter_id)
        if not _can_view_encounter(request.user, encounter):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        rows = Immunization.objects.filter(patient=encounter.patient).order_by("-created_at")
        return Response({"results": ImmunizationSerializer(rows, many=True).data})

    @transaction.atomic
    def post(self, request, encounter_id):
        encounter = get_object_or_404(Encounter.objects.select_related("practitioner"), id=encounter_id)
        error = _encounter_clinical_write_guard(request.user, encounter)
        if error:
            return Response({"detail": error}, status=status.HTTP_403_FORBIDDEN)
        serializer = ClinicianImmunizationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        immunization = Immunization.objects.create(
            patient=encounter.patient,
            administered_by=encounter.practitioner,
            institution=encounter.institution,
            source=ClinicalRecordSource.CLINICIAN_RECORDED,
            vaccine_name=data["vaccine_name"],
            dose_number=data.get("dose_number", 1),
            administered_date=data.get("administered_date"),
            lot_number=data.get("lot_number", ""),
            next_dose_due=data.get("next_dose_due"),
        )
        return Response(ImmunizationSerializer(immunization).data, status=status.HTTP_201_CREATED)


class EncounterPrescriptionListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, encounter_id):
        encounter = get_object_or_404(Encounter.objects.select_related("practitioner"), id=encounter_id)
        if not _can_view_encounter(request.user, encounter):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        rows = EMedication.objects.filter(patient=encounter.patient).order_by("-created_at")
        return Response({"results": EMedicationSerializer(rows, many=True).data})

    @transaction.atomic
    def post(self, request, encounter_id):
        encounter = get_object_or_404(Encounter.objects.select_related("practitioner"), id=encounter_id)
        error = _encounter_clinical_write_guard(request.user, encounter)
        if error:
            return Response({"detail": error}, status=status.HTTP_403_FORBIDDEN)
        serializer = ClinicianPrescriptionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        prescription = EMedication.objects.create(
            patient=encounter.patient,
            prescribing_practitioner=encounter.practitioner,
            encounter=encounter,
            name=data["name"],
            dosage=data["dosage"],
            frequency=data["frequency"],
            start_date=data.get("start_date"),
            refill_due=data.get("refill_due"),
            notes=data.get("notes", ""),
        )
        return Response(EMedicationSerializer(prescription).data, status=status.HTTP_201_CREATED)


class PrescriptionRevokeView(APIView):
    """Only the prescribing practitioner (or someone who can manage the
    institution it was written under) may revoke a prescription — a patient
    revoking their own prescription to dodge a flagged interaction, or a
    random practitioner revoking someone else's order, are both excluded."""

    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, prescription_id):
        prescription = get_object_or_404(
            EMedication.objects.select_related("prescribing_practitioner", "encounter__institution"), id=prescription_id,
        )
        if not prescription.prescribing_practitioner_id:
            return Response({"detail": "This entry has no prescriber of record to revoke on behalf of."}, status=status.HTTP_400_BAD_REQUEST)
        is_prescriber = prescription.prescribing_practitioner.user_id == request.user.id
        is_institution_manager = bool(
            prescription.encounter_id and prescription.encounter.institution_id
        ) and _can_manage_institution(request.user, prescription.encounter.institution)
        if not (is_prescriber or is_institution_manager):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if prescription.is_revoked:
            return Response({"detail": "Already revoked."}, status=status.HTTP_400_BAD_REQUEST)
        prescription.is_revoked = True
        prescription.is_active = False
        prescription.revoked_at = timezone.now()
        prescription.save(update_fields=["is_revoked", "is_active", "revoked_at", "updated_at"])
        return Response(EMedicationSerializer(prescription).data)


# ---------------------------------------------------------------------------
# ClinicalReferral
# ---------------------------------------------------------------------------

class ClinicalReferralListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        practitioner = HealthPractitioner.objects.filter(user=user).first()
        q = ClinicalReferral.objects.filter(patient=user)
        if practitioner:
            q = q | ClinicalReferral.objects.filter(referring_practitioner=practitioner)
            q = q | ClinicalReferral.objects.filter(receiving_practitioner=practitioner)
        referrals = q.distinct().order_by("-created_at")[:100]
        return Response({"results": ClinicalReferralSerializer(referrals, many=True).data})

    @transaction.atomic
    def post(self, request):
        serializer = ClinicalReferralCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        encounter = get_object_or_404(Encounter.objects.select_related("practitioner", "institution"), id=data["encounter"])

        if encounter.practitioner.user_id != request.user.id:
            return Response(
                {"detail": "Only the encounter's own practitioner may create a referral from it."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if encounter.status not in (EncounterStatus.IN_PROGRESS, EncounterStatus.COMPLETED):
            return Response(
                {"detail": "A referral requires an in-progress or completed encounter."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        receiving_practitioner = None
        if data.get("receiving_practitioner"):
            receiving_practitioner = get_object_or_404(HealthPractitioner, id=data["receiving_practitioner"])
        receiving_institution = None
        if data.get("receiving_institution"):
            receiving_institution = get_object_or_404(HealthInstitution, id=data["receiving_institution"])
        if not receiving_practitioner and not receiving_institution:
            return Response(
                {"detail": "A referral needs at least a receiving practitioner or receiving institution."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        referral = ClinicalReferral.objects.create(
            encounter=encounter,
            patient=encounter.patient,
            referring_practitioner=encounter.practitioner,
            referring_institution=encounter.institution,
            receiving_practitioner=receiving_practitioner,
            receiving_institution=receiving_institution,
            reason=data["reason"],
            priority=data.get("priority") or ClinicalReferral._meta.get_field("priority").default,
            notes=data.get("notes", ""),
        )
        return Response(ClinicalReferralSerializer(referral).data, status=status.HTTP_201_CREATED)


def _can_view_referral(user, referral: ClinicalReferral) -> bool:
    if referral.patient_id == user.id:
        return True
    if referral.referring_practitioner.user_id == user.id:
        return True
    if referral.receiving_practitioner and referral.receiving_practitioner.user_id == user.id:
        return True
    if referral.receiving_institution and _can_manage_institution(user, referral.receiving_institution):
        return True
    if referral.referring_institution and _can_manage_institution(user, referral.referring_institution):
        return True
    return False


class ClinicalReferralDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, referral_id):
        referral = get_object_or_404(
            ClinicalReferral.objects.select_related(
                "referring_practitioner", "receiving_practitioner", "referring_institution", "receiving_institution",
            ),
            id=referral_id,
        )
        if not _can_view_referral(request.user, referral):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        return Response(ClinicalReferralSerializer(referral).data)


def _is_receiving_side(user, referral: ClinicalReferral) -> bool:
    if referral.receiving_practitioner and referral.receiving_practitioner.user_id == user.id:
        return True
    if referral.receiving_institution and _can_manage_institution(user, referral.receiving_institution):
        return True
    return False


class ClinicalReferralActionView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, referral_id, action):
        referral = get_object_or_404(
            ClinicalReferral.objects.select_related("referring_practitioner", "receiving_practitioner", "receiving_institution"),
            id=referral_id,
        )
        serializer = ClinicalReferralActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        notes = serializer.validated_data.get("notes", "")
        now = timezone.now()

        if action in ("accept", "decline"):
            if not _is_receiving_side(request.user, referral):
                return Response({"detail": "Only the receiving practitioner/institution may respond."}, status=status.HTTP_403_FORBIDDEN)
            if referral.status != ReferralStatus.PENDING:
                return Response({"detail": f"Cannot {action} a referral in status {referral.status}."}, status=status.HTTP_400_BAD_REQUEST)
            referral.status = ReferralStatus.ACCEPTED if action == "accept" else ReferralStatus.DECLINED
            referral.responded_at = now
            if notes:
                referral.notes = notes
            referral.save(update_fields=["status", "responded_at", "notes", "updated_at"])
        elif action == "complete":
            if not _is_receiving_side(request.user, referral):
                return Response({"detail": "Only the receiving practitioner/institution may complete this referral."}, status=status.HTTP_403_FORBIDDEN)
            if referral.status != ReferralStatus.ACCEPTED:
                return Response({"detail": "Only an accepted referral can be completed."}, status=status.HTTP_400_BAD_REQUEST)
            referral.status = ReferralStatus.COMPLETED
            referral.completed_at = now
            referral.save(update_fields=["status", "completed_at", "updated_at"])
        elif action == "cancel":
            if referral.referring_practitioner.user_id != request.user.id:
                return Response({"detail": "Only the referring practitioner may cancel this referral."}, status=status.HTTP_403_FORBIDDEN)
            if referral.status not in (ReferralStatus.PENDING, ReferralStatus.ACCEPTED):
                return Response({"detail": f"Cannot cancel a referral in status {referral.status}."}, status=status.HTTP_400_BAD_REQUEST)
            referral.status = ReferralStatus.CANCELLED
            referral.save(update_fields=["status", "updated_at"])
        else:
            return Response({"detail": "Unknown action."}, status=status.HTTP_400_BAD_REQUEST)

        return Response(ClinicalReferralSerializer(referral).data)


# ---------------------------------------------------------------------------
# Laboratory
# ---------------------------------------------------------------------------

class LabTestCatalogView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        institution_id = request.query_params.get("institution")
        q = LabTestCatalogItem.objects.filter(is_active=True)
        if institution_id:
            q = q.filter(institution_id=institution_id) | q.filter(institution__isnull=True)
        else:
            q = q.filter(institution__isnull=True)
        return Response({"results": LabTestCatalogItemSerializer(q.order_by("name"), many=True).data})

    @transaction.atomic
    def post(self, request):
        institution_id = request.data.get("institution")
        if institution_id:
            institution = get_object_or_404(HealthInstitution, id=institution_id)
            if not _can_manage_institution(request.user, institution):
                return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        elif not request.user.is_staff:
            return Response({"detail": "Only platform staff may add a global catalog item."}, status=status.HTTP_403_FORBIDDEN)
        serializer = LabTestCatalogItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = serializer.save()
        return Response(LabTestCatalogItemSerializer(item).data, status=status.HTTP_201_CREATED)


class LabOrderListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        practitioner = HealthPractitioner.objects.filter(user=user).first()
        q = LabOrder.objects.filter(patient=user)
        if practitioner:
            q = q | LabOrder.objects.filter(ordering_practitioner=practitioner)
        orders = q.distinct().select_related("test", "specimen", "result").order_by("-created_at")[:100]
        return Response({"results": [_serialize_lab_order(o, user) for o in orders]})

    @transaction.atomic
    def post(self, request):
        serializer = LabOrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        encounter = get_object_or_404(Encounter.objects.select_related("practitioner", "institution"), id=data["encounter"])
        if encounter.practitioner.user_id != request.user.id:
            return Response({"detail": "Only the encounter's own practitioner may order a lab test."}, status=status.HTTP_403_FORBIDDEN)
        if encounter.status not in (EncounterStatus.IN_PROGRESS, EncounterStatus.COMPLETED):
            return Response({"detail": "A lab order requires an in-progress or completed encounter."}, status=status.HTTP_400_BAD_REQUEST)
        test = get_object_or_404(LabTestCatalogItem, id=data["test"])
        order = LabOrder.objects.create(
            encounter=encounter,
            patient=encounter.patient,
            ordering_practitioner=encounter.practitioner,
            institution=encounter.institution,
            test=test,
            clinical_notes=data.get("clinical_notes", ""),
        )
        return Response(_serialize_lab_order(order, request.user), status=status.HTTP_201_CREATED)


def _can_view_lab_order(user, order: LabOrder) -> bool:
    if order.patient_id == user.id:
        return True
    if order.ordering_practitioner.user_id == user.id:
        return True
    if order.institution and _is_institution_member(user, order.institution):
        return True
    return False


def _serialize_lab_order(order: LabOrder, viewer: User) -> dict:
    data = LabOrderSerializer(order).data
    is_patient_viewer = order.patient_id == viewer.id and not (
        order.ordering_practitioner.user_id == viewer.id
        or (order.institution and _is_institution_member(viewer, order.institution))
    )
    if is_patient_viewer and order.status != LabOrderStatus.RESULTED:
        # A patient never sees an unverified/in-flight result — only the
        # final, lab-verified value. Staff/ordering practitioner always see
        # the live state so they can track progress.
        data["result"] = None
    return data


class LabOrderDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, order_id):
        order = get_object_or_404(
            LabOrder.objects.select_related("test", "specimen", "result", "institution", "ordering_practitioner"),
            id=order_id,
        )
        if not _can_view_lab_order(request.user, order):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        data = _serialize_lab_order(order, request.user)
        if order.patient_id == request.user.id and order.status == LabOrderStatus.RESULTED and hasattr(order, "result"):
            if order.result.delivered_at is None:
                order.result.delivered_at = timezone.now()
                order.result.save(update_fields=["delivered_at", "updated_at"])
        return Response(data)


class LabSpecimenCollectView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, order_id):
        order = get_object_or_404(LabOrder, id=order_id)
        if not order.institution or not _is_institution_member(request.user, order.institution):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if order.status != LabOrderStatus.ORDERED:
            return Response({"detail": f"Cannot collect a specimen for an order in status {order.status}."}, status=status.HTTP_400_BAD_REQUEST)
        serializer = SpecimenCollectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        collector = HealthPractitioner.objects.filter(user=request.user).first()
        Specimen.objects.create(
            lab_order=order,
            specimen_type=serializer.validated_data.get("specimen_type", ""),
            barcode=serializer.validated_data.get("barcode", ""),
            collected_at=timezone.now(),
            collected_by=collector,
        )
        order.status = LabOrderStatus.SPECIMEN_COLLECTED
        order.save(update_fields=["status", "updated_at"])
        return Response(_serialize_lab_order(order, request.user), status=status.HTTP_201_CREATED)


class LabResultEntryView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, order_id):
        order = get_object_or_404(LabOrder, id=order_id)
        if not order.institution or not _is_institution_member(request.user, order.institution):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        entrant = verified_practitioner_for_user(request.user)
        if not entrant:
            return Response(
                {"detail": "Only a verified practitioner may enter a lab result."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if order.status not in (LabOrderStatus.SPECIMEN_COLLECTED, LabOrderStatus.PROCESSING):
            return Response({"detail": f"Cannot enter a result for an order in status {order.status}."}, status=status.HTTP_400_BAD_REQUEST)
        serializer = LabResultEntrySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        LabResult.objects.update_or_create(
            lab_order=order,
            defaults={
                "value": serializer.validated_data["value"],
                "unit": serializer.validated_data.get("unit", ""),
                "reference_range": serializer.validated_data.get("reference_range", ""),
                "flag": serializer.validated_data.get("flag") or LabResultFlag.NORMAL,
                "entered_by": entrant,
                "verified_by": None,
                "verified_at": None,
            },
        )
        order.status = LabOrderStatus.RESULT_PENDING_VERIFICATION
        order.save(update_fields=["status", "updated_at"])
        return Response(_serialize_lab_order(order, request.user))


class LabResultVerifyView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, order_id):
        order = get_object_or_404(LabOrder.objects.select_related("result"), id=order_id)
        if not order.institution or not _is_institution_member(request.user, order.institution):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        verifier = verified_practitioner_for_user(request.user)
        if not verifier:
            return Response(
                {"detail": "Only a verified practitioner may verify a lab result."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if order.status != LabOrderStatus.RESULT_PENDING_VERIFICATION or not hasattr(order, "result"):
            return Response({"detail": "No pending result to verify for this order."}, status=status.HTTP_400_BAD_REQUEST)
        result = order.result
        if verifier and result.entered_by_id and verifier.id == result.entered_by_id:
            return Response(
                {"detail": "The practitioner who entered a result cannot also verify it."},
                status=status.HTTP_403_FORBIDDEN,
            )
        result.verified_by = verifier
        result.verified_at = timezone.now()
        result.save(update_fields=["verified_by", "verified_at", "updated_at"])
        order.status = LabOrderStatus.RESULTED
        order.save(update_fields=["status", "updated_at"])
        return Response(_serialize_lab_order(order, request.user))
