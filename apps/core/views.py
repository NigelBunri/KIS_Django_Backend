# core/views.py
from django.shortcuts import get_object_or_404
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from decimal import Decimal
from typing import Any, Dict

from rest_framework import viewsets, status, mixins
from rest_framework.decorators import action
from rest_framework.permissions import BasePermission, IsAuthenticated, IsAdminUser, AllowAny
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import OrderingFilter
from drf_spectacular.utils import extend_schema, OpenApiParameter

from drf_spectacular.utils import extend_schema, OpenApiParameter

from . import models, serializers

from apps.notifications import services as notification_services

# Convenience user model
from django.apps import apps
from django.conf import settings
UserModel = apps.get_model(settings.AUTH_USER_MODEL)


def _user_org_ids(user):
    if getattr(user, "is_superuser", False):
        return None
    if not getattr(user, "is_authenticated", False):
        return set()
    return set(
        user.medical_staff_profiles.filter(profile__organization_id__isnull=False).values_list(
            "profile__organization_id", flat=True
        )
    )


def _log_staff_action(staff, actor, action, metadata=None, note=""):
    models.StaffAuditLog.objects.create(
        staff=staff,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        metadata=metadata or {},
        note=note,
    )


def _record_clinical_event(
    patient,
    event_type: str,
    description: str,
    user=None,
    task=None,
    escalation=None,
    triage=None,
    referral=None,
    metadata=None,
):
    models.ClinicalEventLog.objects.create(
        patient=patient,
        event_type=event_type,
        description=description,
        triggered_by=user if getattr(user, "is_authenticated", False) else None,
        task=task,
        escalation=escalation,
        triage=triage,
        referral=referral,
        metadata=metadata or {},
    )


# ---------------------------
# Custom DRF permission wrappers
# ---------------------------
class IsSuperuserOrAdmin(IsAdminUser):
    """
    Slight wrapper: admin users or Django superusers.
    """
    def has_permission(self, request, view):
        if getattr(request.user, "is_superuser", False):
            return True
        return super().has_permission(request, view)


class CanManageRolesPermission(IsAuthenticated):
    """
    Placeholder permission: restrict role/permission/ace management to staff or superusers.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if getattr(request.user, "is_superuser", False):
            return True
        return bool(getattr(request.user, "is_staff", False))


class CanAccessComplianceData(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)


# ---------------------------
# Simple ModelViewSets for Permission & Role
# ---------------------------
@extend_schema(tags=["Permissions"])
class PermissionViewSet(viewsets.ModelViewSet):
    queryset = models.Permission.objects.all()
    serializer_class = serializers.PermissionSerializer
    permission_classes = [IsSuperuserOrAdmin]


@extend_schema(tags=["Roles"])
class RoleViewSet(viewsets.ModelViewSet):
    queryset = models.Role.objects.prefetch_related("permissions").all()
    serializer_class = serializers.RoleShortSerializer
    permission_classes = [IsSuperuserOrAdmin]

    @extend_schema(
        request=serializers.PermissionSerializer,
        responses=serializers.RoleShortSerializer,
        description="Add a permission to a role",
    )
    @action(detail=True, methods=["post"], permission_classes=[IsSuperuserOrAdmin])
    def add_permission(self, request, pk=None):
        role = self.get_object()
        codename = request.data.get("codename")
        if not codename:
            return Response({"detail": "codename required"}, status=status.HTTP_400_BAD_REQUEST)
        perm, _ = models.Permission.objects.get_or_create(codename=codename)
        role.permissions.add(perm)
        role.save()
        return Response(self.get_serializer(role).data)


# ---------------------------
# RoleAssignment & ACE management
# ---------------------------
@extend_schema(tags=["RoleAssignments"])
class RoleAssignmentViewSet(viewsets.ModelViewSet):
    queryset = models.RoleAssignment.objects.select_related("role").all()
    serializer_class = serializers.RoleAssignmentSerializer
    permission_classes = [CanManageRolesPermission]

    @extend_schema(description="Assign a role to a principal")
    def perform_create(self, serializer):
        serializer.save()


@extend_schema(tags=["AccessControlEntries"])
class AccessControlEntryViewSet(viewsets.ModelViewSet):
    queryset = models.AccessControlEntry.objects.all()
    serializer_class = serializers.AccessControlEntrySerializer
    permission_classes = [CanManageRolesPermission]

    @extend_schema(description="Create an Access Control Entry (ACE)")
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)


@extend_schema(tags=["Healthcare"])
class HealthcareOrganizationViewSet(viewsets.ModelViewSet):
    queryset = models.HealthcareOrganization.objects.all()
    serializer_class = serializers.HealthcareOrganizationSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["org_type", "status", "tenant_id", "onboarding_status"]
    search_fields = ["name", "slug", "region"]

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    @action(detail=True, methods=["post"])
    def suspend(self, request, pk=None):
        org = self.get_object()
        org.status = models.HealthcareOrganization.STATUS_SUSPENDED
        org.is_active = False
        org.save(update_fields=["status", "is_active"])
        return Response(self.get_serializer(org).data)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        org = self.get_object()
        org.status = models.HealthcareOrganization.STATUS_ACTIVE
        org.is_active = True
        org.save(update_fields=["status", "is_active"])
        return Response(self.get_serializer(org).data)


@extend_schema(tags=["Healthcare"])
class LocationViewSet(viewsets.ModelViewSet):
    queryset = models.Location.objects.select_related("organization", "profile").all()
    serializer_class = serializers.LocationSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["organization", "profile", "is_primary"]
    search_fields = ["label"]


@extend_schema(tags=["Healthcare"])
class WardViewSet(viewsets.ModelViewSet):
    queryset = models.Ward.objects.select_related("location").all()
    serializer_class = serializers.WardSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["location", "is_isolation"]
    search_fields = ["name"]


@extend_schema(tags=["Healthcare"])
class ServiceViewSet(viewsets.ModelViewSet):
    queryset = models.Service.objects.select_related("profile", "department").all()
    serializer_class = serializers.ServiceSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["profile", "department", "category"]
    search_fields = ["name"]


@extend_schema(tags=["Healthcare"])
class EquipmentViewSet(viewsets.ModelViewSet):
    queryset = models.Equipment.objects.select_related("profile", "ward").all()
    serializer_class = serializers.EquipmentSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["profile", "ward", "status"]
    search_fields = ["name"]


@extend_schema(tags=["Healthcare"])
class MedicalProfileViewSet(viewsets.ModelViewSet):
    queryset = models.MedicalProfile.objects.select_related("organization").all()
    serializer_class = serializers.MedicalProfileSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["profile_type", "status", "organization__id", "onboarding_status"]
    search_fields = ["name", "slug"]

    def get_serializer_class(self):
        if self.action in ("retrieve",):
            return serializers.MedicalProfileDetailSerializer
        return serializers.MedicalProfileSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=["post"])
    def suspend(self, request, pk=None):
        profile = self.get_object()
        profile.status = models.MedicalProfile.STATUS_PAUSED
        profile.save(update_fields=["status"])
        return Response(self.get_serializer(profile).data)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        profile = self.get_object()
        profile.status = models.MedicalProfile.STATUS_ACTIVE
        profile.save(update_fields=["status"])
        return Response(self.get_serializer(profile).data)

    @action(detail=True, methods=["post"])
    def set_active(self, request, pk=None):
        profile = self.get_object()
        prefs = dict(getattr(request.user, "preferences", {}) or {})
        prefs["active_medical_profile"] = str(profile.id)
        prefs["active_medical_organization"] = str(profile.organization_id) if profile.organization_id else None
        request.user.preferences = prefs
        request.user.save(update_fields=["preferences"])
        return Response(serializers.MedicalProfileDetailSerializer(profile).data)


class MedicalContextView(APIView):
    permission_classes = [IsAuthenticated]

    def _serialize_organization(self, org: models.HealthcareOrganization) -> Dict[str, Any]:
        profiles = [serializers.MedicalProfileSerializer(profile).data for profile in org.profiles.all()]
        locations = [serializers.LocationSerializer(location).data for location in org.locations.all()]
        return {
            "id": str(org.id),
            "name": org.name,
            "org_type": org.org_type,
            "status": org.status,
            "region": org.region,
            "metadata": org.metadata,
            "profiles": profiles,
            "locations": locations,
        }

    def get(self, request):
        prefs = dict(getattr(request.user, "preferences", {}) or {})
        active_profile_id = prefs.get("active_medical_profile")
        active_profile = None
        if active_profile_id:
            try:
                active_profile = models.MedicalProfile.objects.get(id=active_profile_id)
            except models.MedicalProfile.DoesNotExist:
                active_profile = None

        organizations = (
            models.HealthcareOrganization.objects.filter(is_active=True)
            .prefetch_related("profiles", "locations")
            .all()
        )

        payload = {
            "organizations": [self._serialize_organization(org) for org in organizations],
            "active_profile_id": str(active_profile.id) if active_profile else None,
            "active_organization_id": str(active_profile.organization_id) if active_profile and active_profile.organization_id else None,
            "active_profile": serializers.MedicalProfileSerializer(active_profile).data if active_profile else None,
        }
        return Response(payload)


@extend_schema(tags=["Healthcare"])
class StaffProfileViewSet(viewsets.ModelViewSet):
    queryset = models.StaffProfile.objects.select_related("profile", "user").all()
    serializer_class = serializers.StaffProfileSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["profile", "role", "is_on_call"]
    search_fields = ["role"]

    def _allowed_org_ids(self):
        return _user_org_ids(self.request.user)

    def get_queryset(self):
        queryset = super().get_queryset()
        allowed = self._allowed_org_ids()
        if allowed is None:
            return queryset
        return queryset.filter(profile__organization_id__in=allowed)

    def perform_create(self, serializer):
        profile = serializer.validated_data.get("profile")
        allowed = self._allowed_org_ids()
        if allowed is not None and profile and profile.organization_id not in allowed:
            raise PermissionDenied("You cannot manage staff outside your organization.")
        staff = serializer.save()
        _log_staff_action(staff, self.request.user, "create", metadata={"profile": str(staff.profile_id)})

    @action(detail=True, methods=["post"])
    def assign_role(self, request, pk=None):
        staff = self.get_object()
        allowed = self._allowed_org_ids()
        if allowed is not None and staff.profile.organization_id not in allowed:
            raise PermissionDenied("Access denied.")
        updates = {}
        for field in ("role", "scope", "permissions", "licenses"):
            if field in request.data:
                updates[field] = request.data.get(field)
        if not updates:
            raise ValidationError({"detail": "Provide at least one field to update."})
        for key, value in updates.items():
            setattr(staff, key, value)
        staff.save(update_fields=list(updates.keys()) + ["updated_at"])
        _log_staff_action(staff, request.user, "assign_role", metadata=updates)
        return Response(self.get_serializer(staff).data)


@extend_schema(tags=["Healthcare"])
class StaffAuditViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = models.StaffAuditLog.objects.select_related("staff", "actor").all()
    serializer_class = serializers.StaffAuditSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["staff", "actor", "action"]

    @action(detail=True, methods=["post"])
    def assign_shift(self, request, pk=None):
        staff = self.get_object()
        allowed = self._allowed_org_ids()
        if allowed is not None and staff.profile.organization_id not in allowed:
            raise PermissionDenied("Access denied.")
        shifts = request.data.get("shifts")
        if not isinstance(shifts, list):
            raise ValidationError({"shifts": "Provide a list of shift objects."})
        staff.shifts = shifts
        staff.save(update_fields=["shifts", "updated_at"])
        _log_staff_action(staff, request.user, "assign_shift", metadata={"shifts": shifts})
        return Response(self.get_serializer(staff).data)

    @action(detail=True, methods=["post"])
    def refresh_shifts(self, request, pk=None):
        shifts = request.data.get("shifts")
        if isinstance(shifts, list):
            staff = self.get_object()
            staff.shifts = shifts
            staff.save(update_fields=["shifts", "updated_at"])
            _log_staff_action(staff, request.user, "refresh_shifts", metadata={"shifts": shifts})
            return Response(self.get_serializer(staff).data)
        return Response({"detail": "Provide 'shifts' array in payload."}, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(tags=["Compliance"])
class ComplianceAuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = models.ComplianceAuditLog.objects.select_related("actor").all()
    serializer_class = serializers.ComplianceAuditLogSerializer
    permission_classes = [CanAccessComplianceData]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["severity", "action", "target_type"]
    ordering_fields = ["created_at"]


@extend_schema(tags=["Compliance"])
class CredentialVerificationViewSet(viewsets.ModelViewSet):
    queryset = models.CredentialVerification.objects.select_related("staff_profile", "verified_by").all()
    serializer_class = serializers.CredentialVerificationSerializer
    permission_classes = [CanAccessComplianceData]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["staff_profile", "status"]
    ordering_fields = ["expires_at"]

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        credential = self.get_object()
        credential.mark_verified(request.user)
        models.ComplianceAuditLog.log(
            actor=request.user,
            action="compliance.credential.verify",
            target_type="CredentialVerification",
            target_id=str(credential.id),
            severity=models.ComplianceAuditLog.SEVERITY_MEDIUM,
            metadata={
                "credential_type": credential.credential_type,
                "license_number": credential.license_number,
            },
        )
        return Response(self.get_serializer(credential).data)


@extend_schema(tags=["Compliance"])
class RegulatoryReportViewSet(viewsets.ModelViewSet):
    queryset = models.RegulatoryReport.objects.select_related("profile", "organization").all()
    serializer_class = serializers.RegulatoryReportSerializer
    permission_classes = [CanAccessComplianceData]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["profile", "organization", "report_type", "status"]
    ordering_fields = ["period_start"]

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        report = self.get_object()
        report.submit()
        models.ComplianceAuditLog.log(
            actor=request.user,
            action="compliance.regulatory_report.submit",
            target_type="RegulatoryReport",
            target_id=str(report.id),
            severity=models.ComplianceAuditLog.SEVERITY_HIGH,
            metadata={
                "report_type": report.report_type,
                "period_start": str(report.period_start),
                "period_end": str(report.period_end),
            },
        )
        return Response(self.get_serializer(report).data)


@extend_schema(tags=["Compliance"])
class ComplianceDocumentViewSet(viewsets.ModelViewSet):
    queryset = models.ComplianceDocument.objects.select_related("profile", "organization", "signed_by").all()
    serializer_class = serializers.ComplianceDocumentSerializer
    permission_classes = [CanAccessComplianceData]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["profile", "organization", "status", "is_signed"]
    ordering_fields = ["created_at"]

    @action(detail=True, methods=["post"])
    def sign(self, request, pk=None):
        document = self.get_object()
        document.mark_signed(request.user)
        models.ComplianceAuditLog.log(
            actor=request.user,
            action="compliance.document.sign",
            target_type="ComplianceDocument",
            target_id=str(document.id),
            severity=models.ComplianceAuditLog.SEVERITY_MEDIUM,
            metadata={
                "document_name": document.document_name,
                "profile": document.profile_id,
            },
        )
        return Response(self.get_serializer(document).data)


@extend_schema(tags=["Compliance"])
class DataAccessConsentViewSet(viewsets.ModelViewSet):
    queryset = models.DataAccessConsent.objects.select_related("patient").all()
    serializer_class = serializers.DataAccessConsentSerializer
    permission_classes = [CanAccessComplianceData]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["patient", "granted_to", "status"]
    ordering_fields = ["created_at"]

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        consent = self.get_object()
        consent.revoke()
        models.ComplianceAuditLog.log(
            actor=request.user,
            action="compliance.data_access.revoke",
            target_type="DataAccessConsent",
            target_id=str(consent.id),
            severity=models.ComplianceAuditLog.SEVERITY_HIGH,
            metadata={
                "patient": consent.patient_id,
                "granted_to": consent.granted_to,
            },
        )
        return Response(self.get_serializer(consent).data)


@extend_schema(tags=["Patients"])
class PatientMasterRecordViewSet(viewsets.ModelViewSet):
    queryset = (
        models.PatientMasterRecord.objects.select_related("organization")
        .prefetch_related(
            "family_profiles",
            "consents",
            "encounters",
            "appointments",
        )
        .all()
    )
    serializer_class = serializers.PatientMasterRecordSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["tenant_id", "mrn", "status", "organization"]
    search_fields = ["first_name", "last_name", "mrn"]

    def get_serializer_class(self):
        if self.action in ("retrieve",):
            return serializers.PatientMasterRecordDetailSerializer
        return serializers.PatientMasterRecordSerializer

    def perform_create(self, serializer):
        patient = serializer.save()
        self._annotate_patient_intake(patient)

    def _annotate_patient_intake(self, patient: models.PatientMasterRecord):
        if getattr(self.request.user, "is_authenticated", False):
            notification_services.create_notification(
                user_id=self.request.user.id,
                type="patient.intake.created",
                title="New patient intake ready",
                body=f"{patient.first_name} {patient.last_name} ({patient.mrn}) was added to the master index.",
                context={"patient_id": str(patient.id)},
                dedup_key=f"patient:intake:{patient.id}",
            )

    @extend_schema(
        summary="Get patient profile summary with family and consent detail"
    )
    @action(detail=True, methods=["get"])
    def summary(self, request, pk=None):
        patient = self.get_object()
        serializer = serializers.PatientMasterRecordDetailSerializer(patient)
        return Response(serializer.data)


@extend_schema(tags=["Patients"])
class FamilyProfileViewSet(viewsets.ModelViewSet):
    queryset = models.PatientFamilyProfile.objects.select_related("patient").all()
    serializer_class = serializers.FamilyProfileSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "relationship"]


@extend_schema(tags=["Patients"])
class ConsentRecordViewSet(viewsets.ModelViewSet):
    queryset = models.ConsentRecord.objects.select_related("patient").all()
    serializer_class = serializers.ConsentRecordSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "purpose", "granted_by"]
    search_fields = ["purpose"]

    def perform_create(self, serializer):
        granted_by = serializer.validated_data.get("granted_by")
        if granted_by is None and getattr(self.request.user, "is_authenticated", False):
            serializer.save(granted_by=self.request.user)
            return
        serializer.save()


@extend_schema(tags=["Patients"])
class EncounterViewSet(viewsets.ModelViewSet):
    queryset = models.EncounterNote.objects.select_related("patient", "organization", "clinician").all()
    serializer_class = serializers.EncounterSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "organization", "encounter_type"]

    def perform_create(self, serializer):
        encounter = serializer.save(clinician=self.request.user)
        if not encounter:
            return


@extend_schema(tags=["Patients"])
class AppointmentViewSet(viewsets.ModelViewSet):
    queryset = models.Appointment.objects.select_related("patient", "profile").all()
    serializer_class = serializers.AppointmentSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "profile", "status"]


@extend_schema(tags=["Patients"])
class MedicationOrderViewSet(viewsets.ModelViewSet):
    queryset = models.MedicationOrder.objects.select_related("patient", "clinician", "profile").all()
    serializer_class = serializers.MedicationOrderSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "profile", "status", "drug_name"]
    search_fields = ["drug_name"]

    def perform_create(self, serializer):
        patient = serializer.validated_data.get("patient")
        order = serializer.save(clinician=self.request.user)
        order.fhir_resource = {
            "resourceType": "MedicationRequest",
            "status": order.status,
            "medicationCodeableConcept": {"text": order.drug_name},
            "subject": {"reference": f"Patient/{patient.mrn}"},
            "dosageInstruction": [
                {
                    "text": f"{order.route} {order.dosage} {order.frequency}".strip(),
                    "route": {"text": order.route},
                }
            ],
        }
        order.save(update_fields=["fhir_resource"])


@extend_schema(tags=["Patients"])
class AllergyRecordViewSet(viewsets.ModelViewSet):
    queryset = models.AllergyRecord.objects.select_related("patient").all()
    serializer_class = serializers.AllergyRecordSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "status", "severity"]
    search_fields = ["agent"]

    def perform_create(self, serializer):
        allergy = serializer.save()
        if not allergy:
            return


@extend_schema(tags=["Patients"])
class VitalSignViewSet(viewsets.ModelViewSet):
    queryset = models.VitalSign.objects.select_related("patient", "profile", "clinician").all()
    serializer_class = serializers.VitalSignSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "vital_type"]
    search_fields = ["notes"]

    def perform_create(self, serializer):
        vital = serializer.save(clinician=self.request.user)
        if not vital:
            return


@extend_schema(tags=["Clinical"])
class ClinicalTaskViewSet(viewsets.ModelViewSet):
    queryset = models.ClinicalTask.objects.select_related("patient", "assigned_to", "created_by").all()
    serializer_class = serializers.ClinicalTaskSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "assigned_to", "status", "priority"]
    search_fields = ["title", "description"]

    def perform_create(self, serializer):
        task = serializer.save(created_by=self.request.user)
        if task.assigned_to_id:
            notification_services.create_notification(
                user_id=task.assigned_to_id,
                type="clinical.task.created",
                title="New clinical task assigned",
                body=f"{task.title} · {task.description[:120]}",
                context={"task_id": str(task.id), "patient_id": str(task.patient_id)},
                dedup_key=f"clinical:task:{task.id}:assigned",
            )
        _record_clinical_event(
            patient=task.patient,
            event_type=models.ClinicalEventLog.EVENT_TASK_CREATED,
            description=f"Task '{task.title}' created ({task.priority}).",
            user=self.request.user,
            task=task,
        )

    def perform_update(self, serializer):
        existing = self.get_object()
        prior_status = existing.status
        task = serializer.save()
        if prior_status != task.status and task.status == models.ClinicalTask.STATUS_COMPLETED:
            if not task.completed_at:
                task.completed_at = timezone.now()
                task.save(update_fields=["completed_at"])
            _record_clinical_event(
                patient=task.patient,
                event_type=models.ClinicalEventLog.EVENT_TASK_COMPLETED,
                description=f"Task '{task.title}' completed.",
                user=self.request.user,
                task=task,
            )


@extend_schema(tags=["Clinical"])
class EmergencyEscalationViewSet(viewsets.ModelViewSet):
    queryset = models.EmergencyEscalation.objects.select_related("patient", "reported_by").all()
    serializer_class = serializers.EmergencyEscalationSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "severity", "status"]

    def perform_create(self, serializer):
        escalation = serializer.save(reported_by=self.request.user)
        notified_users = []
        if escalation.patient and escalation.patient.organization_id:
            staff = models.StaffProfile.objects.filter(
                profile__organization_id=escalation.patient.organization_id, is_on_call=True
            )
            for member in staff:
                user = member.user
                if user and user.id:
                    notified_users.append(str(user.id))
                    notification_services.create_notification(
                        user_id=user.id,
                        type="clinical.escalation",
                        title="Emergency escalation recorded",
                        body=f"{escalation.severity.title()} escalation for {escalation.patient}.",
                        context={"escalation_id": str(escalation.id)},
                        dedup_key=f"clinical:escalation:{escalation.id}",
                    )
        _record_clinical_event(
            patient=escalation.patient,
            event_type=models.ClinicalEventLog.EVENT_ESCALATION,
            description=f"Escalation ({escalation.severity}) recorded and notified {len(notified_users)} staff.",
            user=self.request.user,
            escalation=escalation,
            metadata={"notified": notified_users},
        )

    def perform_update(self, serializer):
        prior_status = self.get_object().status
        escalation = serializer.save()
        if prior_status != escalation.status and escalation.status == models.EmergencyEscalation.STATUS_RESOLVED:
            escalation.resolved_at = timezone.now()
            escalation.save(update_fields=["resolved_at"])
            _record_clinical_event(
                patient=escalation.patient,
                event_type=models.ClinicalEventLog.EVENT_ESCALATION,
                description="Escalation resolved.",
                user=self.request.user,
                escalation=escalation,
            )


@extend_schema(tags=["Clinical"])
class TriageRecordViewSet(viewsets.ModelViewSet):
    queryset = models.TriageRecord.objects.select_related("patient", "created_by").all()
    serializer_class = serializers.TriageRecordSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "acuity_level"]

    def perform_create(self, serializer):
        record = serializer.save(created_by=self.request.user)
        new_level = record.acuity_level
        _record_clinical_event(
            patient=record.patient,
            event_type=models.ClinicalEventLog.EVENT_TRIAGE,
            description=f"Triage completed ({new_level}).",
            user=self.request.user,
            triage=record,
        )


@extend_schema(tags=["Clinical"])
class ReferralRouteViewSet(viewsets.ModelViewSet):
    queryset = models.ReferralRoute.objects.select_related("patient", "from_organization", "to_organization").all()
    serializer_class = serializers.ReferralRouteSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "from_organization", "to_organization", "status"]

    def perform_create(self, serializer):
        referral = serializer.save(created_by=self.request.user)
        _record_clinical_event(
            patient=referral.patient,
            event_type=models.ClinicalEventLog.EVENT_REFERRAL,
            description=f"Referral to {referral.to_organization or referral.metadata.get('to_org_name')}.",
            user=self.request.user,
            referral=referral,
        )


@extend_schema(tags=["Clinical"])
class ClinicalEventLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = models.ClinicalEventLog.objects.select_related("patient", "triggered_by").all()
    serializer_class = serializers.ClinicalEventLogSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "event_type"]

    @action(detail=False, methods=["post"])
    def acknowledge(self, request):
        return Response({"detail": "Use specialized routes to mark events acknowledged."})


class CommandCenterOverview(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        pending_tasks = models.ClinicalTask.objects.filter(
            status__in=[models.ClinicalTask.STATUS_PENDING, models.ClinicalTask.STATUS_IN_PROGRESS]
        ).count()
        active_escalations = models.EmergencyEscalation.objects.exclude(status=models.EmergencyEscalation.STATUS_RESOLVED).count()
        open_referrals = models.ReferralRoute.objects.filter(status=models.ReferralRoute.STATUS_PENDING).count()
        latest_events = models.ClinicalEventLog.objects.order_by("-created_at")[:5]
        latest_triage = models.TriageRecord.objects.order_by("-created_at")[:3]
        return Response(
            {
                "pending_tasks": pending_tasks,
                "active_escalations": active_escalations,
                "open_referrals": open_referrals,
                "recent_events": serializers.ClinicalEventLogSerializer(latest_events, many=True).data,
                "recent_triage": serializers.TriageRecordSerializer(latest_triage, many=True).data,
            }
        )


@extend_schema(tags=["Telemedicine"])
class TelemedicineSessionViewSet(viewsets.ModelViewSet):
    queryset = models.TelemedicineSession.objects.select_related("patient", "clinician", "profile", "appointment").all()
    serializer_class = serializers.TelemedicineSessionSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["status", "patient", "clinician", "profile"]
    reminder_window_hours = 24

    def perform_create(self, serializer):
        clinician = serializer.validated_data.get("clinician")
        if clinician is None and getattr(self.request.user, "is_authenticated", False):
            clinician = self.request.user
        session = serializer.save(clinician=clinician)
        self._notify_session_created(session)

    def _notify_session_created(self, session: models.TelemedicineSession):
        if not session.clinician_id:
            return
        scheduled_at = (
            session.appointment.scheduled_at
            if session.appointment and session.appointment.scheduled_at
            else session.started_at
        )
        scheduled_label = scheduled_at.isoformat() if scheduled_at else "soon"
        notification_services.create_notification(
            user_id=session.clinician_id,
            type="telemedicine.session.created",
            title="Telemedicine session scheduled",
            body=(
                f"Session for {session.patient or 'a patient'} "
                f"is booked for {scheduled_label}."
            ),
            context={
                "session_id": str(session.id),
                "patient_id": str(session.patient_id) if session.patient_id else None,
                "appointment_id": str(session.appointment_id) if session.appointment_id else None,
            },
            dedup_key=f"telemedicine:session:{session.id}:created",
        )

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        session = self.get_object()
        session.status = models.TelemedicineSession.STATUS_ACTIVE
        session.started_at = timezone.now()
        session.save(update_fields=["status", "started_at"])
        return Response(self.get_serializer(session).data)

    @action(detail=True, methods=["post"])
    def end(self, request, pk=None):
        session = self.get_object()
        session.status = models.TelemedicineSession.STATUS_ENDED
        session.ended_at = timezone.now()
        session.save(update_fields=["status", "ended_at"])
        return Response(self.get_serializer(session).data)


@extend_schema(tags=["Telemedicine"])
class TelemedicineDeviceViewSet(viewsets.ModelViewSet):
    queryset = models.TelemedicineDevice.objects.select_related("session").all()
    serializer_class = serializers.TelemedicineDeviceSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["session", "device_type"]


@extend_schema(tags=["Telemedicine"])
class VoiceDictationViewSet(viewsets.ModelViewSet):
    queryset = models.VoiceDictation.objects.select_related("session", "clinician").all()
    serializer_class = serializers.VoiceDictationSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["session", "clinician"]

    def perform_create(self, serializer):
        dictation = serializer.save()
        transcript = (
            dictation.audio_metadata.get("transcription")
            or dictation.audio_metadata.get("transcription_prompt")
            or dictation.audio_metadata.get("notes")
            or ""
        )
        dictation.transcript = transcript
        dictation.save(update_fields=["transcript"])


@extend_schema(tags=["Medical Resources"])
class InventoryItemViewSet(viewsets.ModelViewSet):
    queryset = models.InventoryItem.objects.select_related("profile", "organization").all()
    serializer_class = serializers.InventoryItemSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["profile", "organization", "status", "category"]
    search_fields = ["name", "sku"]

    def perform_update(self, serializer):
        # ensure quantity doesn't go negative
        item = serializer.save()
        if item.quantity_on_hand < Decimal("0.00"):
            item.quantity_on_hand = Decimal("0.00")
            item.save(update_fields=["quantity_on_hand"])


@extend_schema(tags=["Medical Resources"])
class DiagnosticOrderViewSet(viewsets.ModelViewSet):
    queryset = models.DiagnosticOrder.objects.select_related("patient", "profile", "requested_by").all()
    serializer_class = serializers.DiagnosticOrderSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "profile", "status"]
    search_fields = ["test_name"]


@extend_schema(tags=["Medical Resources"])
class ImagingStudyViewSet(viewsets.ModelViewSet):
    queryset = models.ImagingStudy.objects.select_related("patient", "profile").all()
    serializer_class = serializers.ImagingStudySerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "profile", "status", "modality"]
    search_fields = ["body_region"]


@extend_schema(tags=["Medical Resources"])
class MedicationAdherenceReminderViewSet(viewsets.ModelViewSet):
    queryset = models.MedicationAdherenceReminder.objects.select_related("patient", "medication_order").all()
    serializer_class = serializers.MedicationAdherenceReminderSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "status", "channel"]

    @action(detail=True, methods=["post"])
    def mark_sent(self, request, pk=None):
        reminder = self.get_object()
        reminder.status = models.MedicationAdherenceReminder.STATUS_SENT
        reminder.save(update_fields=["status"])
        return Response(self.get_serializer(reminder).data)

    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        reminder = self.get_object()
        reminder.status = models.MedicationAdherenceReminder.STATUS_ACKNOWLEDGED
        reminder.save(update_fields=["status"])
        return Response(self.get_serializer(reminder).data)


@extend_schema(tags=["Medical Resources"])
class SupplyForecastViewSet(viewsets.ModelViewSet):
    queryset = models.SupplyForecast.objects.select_related("profile").all()
    serializer_class = serializers.SupplyForecastSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["profile", "category"]
# ---------------------------
# Community / Group / Channel ViewSets
# ---------------------------
@extend_schema(tags=["Communities"])
class CommunityViewSet(viewsets.ModelViewSet):
    queryset = models.Community.objects.all()
    serializer_class = serializers.CommunitySerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [AllowAny()]
        return [IsAuthenticated()]
    
    def perform_create(self, serializer):
        # set owner to request.user by default if not provided
        owner = serializer.validated_data.get("owner", None)
        if owner is None:
            # set owner generic fields to request.user
            user_ct = ContentType.objects.get_for_model(self.request.user.__class__)
            serializer.validated_data["owner"] = self.request.user
        serializer.save()

    @extend_schema(
        request=serializers.AccessControlEntrySerializer,
        responses=serializers.AccessControlEntrySerializer,
        description="Grant an ACE to a principal on this community",
    )
    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def grant(self, request, pk=None):
        community = self.get_object()
        data = request.data.copy()
        data["target"] = {"type": f"{community._meta.app_label}.{community._meta.model_name}", "id": str(community.pk)}
        serializer = serializers.AccessControlEntrySerializer(data=data)
        serializer.is_valid(raise_exception=True)
        ace = serializer.save()
        return Response(serializers.AccessControlEntrySerializer(ace).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["Groups"])
class GroupViewSet(viewsets.ModelViewSet):
    queryset = models.Group.objects.select_related("community").all()
    serializer_class = serializers.GroupSerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [AllowAny()]
        return [IsAuthenticated()]
    
    def perform_create(self, serializer):
        # Create group and default settings handled by serializer.create
        serializer.save()

    @extend_schema(description="Join a group respecting its join policy")
    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def join(self, request, pk=None):
        group = self.get_object()
        user = request.user
        settings_obj = getattr(group, "settings", None)
        join_policy = settings_obj.join_policy if settings_obj else "OPEN"
        invite_token = request.data.get("invite_token")

        if join_policy == "INVITE" and not invite_token:
            return Response({"detail": "Invite token required"}, status=status.HTTP_400_BAD_REQUEST)

        if invite_token:
            try:
                invite = models.MembershipInvite.objects.get(token=invite_token, group=group)
            except models.MembershipInvite.DoesNotExist:
                return Response({"detail": "Invalid invite token"}, status=status.HTTP_400_BAD_REQUEST)
            if not invite.is_valid():
                return Response({"detail": "Invite expired/used"}, status=status.HTTP_400_BAD_REQUEST)
            invite.use()

        user_ct = ContentType.objects.get_for_model(user.__class__)
        with transaction.atomic():
            mem, created = models.Membership.objects.update_or_create(
                group=group,
                user_content_type=user_ct,
                user_object_id=str(user.pk),
                defaults={"status": models.Membership.STATUS_ACTIVE, "joined_at": timezone.now()},
            )
            group.recalc_member_count()
        return Response(serializers.MembershipSerializer(mem).data, status=status.HTTP_200_OK)

    @extend_schema(description="Leave a group")
    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def leave(self, request, pk=None):
        group = self.get_object()
        user = request.user
        user_ct = ContentType.objects.get_for_model(user.__class__)
        try:
            mem = models.Membership.objects.get(group=group, user_content_type=user_ct, user_object_id=str(user.pk))
        except models.Membership.DoesNotExist:
            return Response({"detail": "Not a member"}, status=status.HTTP_400_BAD_REQUEST)
        mem.status = models.Membership.STATUS_LEFT
        mem.save()
        group.recalc_member_count()
        return Response({"detail": "Left group"}, status=status.HTTP_200_OK)

    @extend_schema(
        request=serializers.MembershipInviteSerializer,
        responses=serializers.MembershipInviteSerializer,
        description="Create an invite for the group",
    )
    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def invite(self, request, pk=None):
        group = self.get_object()
        user = request.user
        if not (group.can_user(user, "group.invite") or group.is_member(user) and group.memberships.filter(
                user_content_type=ContentType.objects.get_for_model(user.__class__),
                user_object_id=str(user.pk),
                is_moderator=True).exists()):
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        serializer = serializers.MembershipInviteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invite = serializer.save(group=group, created_by=user)
        return Response(serializers.MembershipInviteSerializer(invite).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        request={"type": "object", "properties": {"user": {}, "role": {"type": "string"}}},
        responses=serializers.MembershipSerializer,
        description="Promote a member to a role",
    )
    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def promote(self, request, pk=None):
        group = self.get_object()
        user = request.user
        if not group.can_user(user, "group.manage_members"):
            if not group.memberships.filter(user_content_type=ContentType.objects.get_for_model(user.__class__),
                                            user_object_id=str(user.pk),
                                            is_moderator=True).exists():
                return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        target_user_data = request.data.get("user")
        role_id = request.data.get("role")
        if not target_user_data or not role_id:
            return Response({"detail": "user and role required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            target = serializers.GenericRelatedField().to_internal_value(target_user_data)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        role = get_object_or_404(models.Role, pk=role_id)
        user_ct = ContentType.objects.get_for_model(target.__class__)
        try:
            mem = models.Membership.objects.get(group=group, user_content_type=user_ct, user_object_id=str(target.pk))
        except models.Membership.DoesNotExist:
            return Response({"detail": "Target is not a member"}, status=status.HTTP_400_BAD_REQUEST)

        mem.promote_to_role(role)
        return Response(serializers.MembershipSerializer(mem).data, status=status.HTTP_200_OK)


@extend_schema(tags=["Channels"])
class ChannelViewSet(viewsets.ModelViewSet):
    queryset = models.Channel.objects.prefetch_related("communities", "groups").all()
    serializer_class = serializers.ChannelSerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [AllowAny()]
        return [IsAuthenticated()]

    @extend_schema(description="Join a channel if allowed by group/community membership or ACEs")
    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def join(self, request, pk=None):
        channel = self.get_object()
        user = request.user

        if channel.is_public:
            for g in channel.groups.all():
                if g.is_member(user):
                    return Response({"detail": "Allowed via group membership"}, status=status.HTTP_200_OK)
            for c in channel.communities.all():
                if models.CommunityPermissionHelper.can_user_on_community(user, c, "community.member"):
                    return Response({"detail": "Allowed via community membership"}, status=status.HTTP_200_OK)

        if channel.can_user(user, "channel.join"):
            return Response({"detail": "Allowed"}, status=status.HTTP_200_OK)

        return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)


# ---------------------------
# Membership & Invite ViewSets
# ---------------------------
@extend_schema(tags=["Memberships"])
class MembershipViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet,
                        mixins.UpdateModelMixin, mixins.DestroyModelMixin):
    queryset = models.Membership.objects.select_related("group").all()
    serializer_class = serializers.MembershipSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if getattr(user, "is_superuser", False) or user.is_staff:
            return super().get_queryset()
        user_ct = ContentType.objects.get_for_model(user.__class__)
        return super().get_queryset().filter(user_content_type=user_ct, user_object_id=str(user.pk))


@extend_schema(tags=["MembershipInvites"])
class MembershipInviteViewSet(viewsets.ModelViewSet):
    queryset = models.MembershipInvite.objects.all()
    serializer_class = serializers.MembershipInviteSerializer
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request={"type": "object", "properties": {"token": {"type": "string"}}},
        responses=serializers.MembershipSerializer,
        description="Redeem an invite token to join a group/community",
    )
    @action(detail=False, methods=["post"], permission_classes=[AllowAny])
    def redeem(self, request):
        token = request.data.get("token")
        if not token:
            return Response({"detail": "token required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            invite = models.MembershipInvite.objects.get(token=token)
        except models.MembershipInvite.DoesNotExist:
            return Response({"detail": "Invalid token"}, status=status.HTTP_400_BAD_REQUEST)

        if not invite.is_valid():
            return Response({"detail": "Invite expired or used"}, status=status.HTTP_400_BAD_REQUEST)

        if not request.user or not request.user.is_authenticated:
            return Response({"detail": "Authentication required to redeem"}, status=status.HTTP_401_UNAUTHORIZED)

        user = request.user
        user_ct = ContentType.objects.get_for_model(user.__class__)

        with transaction.atomic():
            if invite.group:
                mem, created = models.Membership.objects.update_or_create(
                    group=invite.group,
                    user_content_type=user_ct,
                    user_object_id=str(user.pk),
                    defaults={"status": models.Membership.STATUS_ACTIVE, "joined_at": timezone.now()}
                )
                invite.use()
                invite.save()
                invite.group.recalc_member_count()
                return Response(serializers.MembershipSerializer(mem).data, status=status.HTTP_201_CREATED)
            elif invite.community:
                invite.use()
                return Response({"detail": "Invite redeemed for community (application-defined)"},
                                status=status.HTTP_200_OK)
            else:
                return Response({"detail": "Invite has no target"}, status=status.HTTP_400_BAD_REQUEST)


# ---------------------------
# ModerationAction ViewSet
# ---------------------------
@extend_schema(tags=["ModerationActions"])
class ModerationActionViewSet(viewsets.ModelViewSet):
    queryset = models.ModerationAction.objects.all()
    serializer_class = serializers.ModerationActionSerializer
    permission_classes = [IsAuthenticated]

    @extend_schema(description="Create a moderation action with permission checks")
    def create(self, request, *args, **kwargs):
        data = request.data.copy()
        data.setdefault("performed_by", {"type": f"{request.user._meta.app_label}.{request.user._meta.model_name}", "id": str(request.user.pk)})
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)

        target = serializer.validated_data["target"]
        if not getattr(request.user, "is_superuser", False):
            if hasattr(target, "can_user"):
                if not target.can_user(request.user, "moderation.action"):
                    return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)


# ---------------------------
# Settings viewsets - update only
# ---------------------------
@extend_schema(tags=["GroupSettings"])
class GroupSettingsViewSet(viewsets.GenericViewSet, mixins.RetrieveModelMixin, mixins.UpdateModelMixin):
    queryset = models.GroupSettings.objects.select_related("group").all()
    serializer_class = serializers.GroupSettingsUpdateSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return super().get_object()


@extend_schema(tags=["ChannelSettings"])
class ChannelSettingsViewSet(viewsets.GenericViewSet, mixins.RetrieveModelMixin, mixins.UpdateModelMixin):
    queryset = models.ChannelSettings.objects.select_related("channel").all()
    serializer_class = serializers.ChannelSettingsUpdateSerializer
    permission_classes = [IsAuthenticated]
