# core/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    PermissionViewSet,
    RoleViewSet,
    RoleAssignmentViewSet,
    AccessControlEntryViewSet,
    CommunityViewSet,
    GroupViewSet,
    ChannelViewSet,
    MembershipViewSet,
    MembershipInviteViewSet,
    ModerationActionViewSet,
    GroupSettingsViewSet,
    ChannelSettingsViewSet,
    HealthcareOrganizationViewSet,
    MedicalProfileViewSet,
    StaffProfileViewSet,
    LocationViewSet,
    WardViewSet,
    ServiceViewSet,
    EquipmentViewSet,
    PatientMasterRecordViewSet,
    FamilyProfileViewSet,
    ConsentRecordViewSet,
    EncounterViewSet,
    AppointmentViewSet,
    MedicationOrderViewSet,
    AllergyRecordViewSet,
    VitalSignViewSet,
    TelemedicineSessionViewSet,
    TelemedicineDeviceViewSet,
    VoiceDictationViewSet,
    StaffAuditViewSet,
    ClinicalTaskViewSet,
    EmergencyEscalationViewSet,
    TriageRecordViewSet,
    ReferralRouteViewSet,
    ClinicalEventLogViewSet,
    InventoryItemViewSet,
    DiagnosticOrderViewSet,
    ImagingStudyViewSet,
    MedicationAdherenceReminderViewSet,
    SupplyForecastViewSet,
    ComplianceAuditLogViewSet,
    CredentialVerificationViewSet,
    RegulatoryReportViewSet,
    ComplianceDocumentViewSet,
    DataAccessConsentViewSet,
    CommandCenterOverview,
    MedicalContextView,
)

app_name = "core"

router = DefaultRouter()
router.register(r"permissions", PermissionViewSet, basename="permission")
router.register(r"roles", RoleViewSet, basename="role")
router.register(r"role-assignments", RoleAssignmentViewSet, basename="roleassignment")
router.register(r"aces", AccessControlEntryViewSet, basename="ace")
router.register(r"communities", CommunityViewSet, basename="community")
router.register(r"groups", GroupViewSet, basename="group")
router.register(r"channels", ChannelViewSet, basename="channel")
router.register(r"memberships", MembershipViewSet, basename="membership")
router.register(r"invites", MembershipInviteViewSet, basename="invite")
router.register(r"moderation-actions", ModerationActionViewSet, basename="moderationaction")
router.register(r"group-settings", GroupSettingsViewSet, basename="groupsettings")
router.register(r"channel-settings", ChannelSettingsViewSet, basename="channelsettings")
router.register(r"medical/organizations", HealthcareOrganizationViewSet, basename="healthcareorganization")
router.register(r"medical/profiles", MedicalProfileViewSet, basename="medicalprofile")
router.register(r"medical/staff", StaffProfileViewSet, basename="medicalstaff")
router.register(r"medical/staff-audits", StaffAuditViewSet, basename="medicalstaffaudit")
router.register(r"medical/locations", LocationViewSet, basename="medicallocation")
router.register(r"medical/wards", WardViewSet, basename="medicalward")
router.register(r"medical/services", ServiceViewSet, basename="medicalservice")
router.register(r"medical/equipment", EquipmentViewSet, basename="medicalequipment")
router.register(r"patients/master", PatientMasterRecordViewSet, basename="patientmasterrecord")
router.register(r"patients/family", FamilyProfileViewSet, basename="patientfamily")
router.register(r"patients/consents", ConsentRecordViewSet, basename="patientconsent")
router.register(r"patients/encounters", EncounterViewSet, basename="patientencounter")
router.register(r"patients/appointments", AppointmentViewSet, basename="patientappointment")
router.register(r"patients/medications", MedicationOrderViewSet, basename="patientmedication")
router.register(r"patients/allergies", AllergyRecordViewSet, basename="patientallergy")
router.register(r"patients/vitals", VitalSignViewSet, basename="patientvital")
router.register(r"telemedicine/sessions", TelemedicineSessionViewSet, basename="telemedsession")
router.register(r"telemedicine/devices", TelemedicineDeviceViewSet, basename="telemeddevice")
router.register(r"telemedicine/dictations", VoiceDictationViewSet, basename="telemeddictation")
router.register(r"clinical/tasks", ClinicalTaskViewSet, basename="clinicaltask")
router.register(r"clinical/escalations", EmergencyEscalationViewSet, basename="clinicalescalation")
router.register(r"clinical/triage", TriageRecordViewSet, basename="clinicaltriage")
router.register(r"clinical/referrals", ReferralRouteViewSet, basename="clinicalreferral")
router.register(r"clinical/events", ClinicalEventLogViewSet, basename="clinicalevent")
# compliance / governance
router.register(r"compliance/audit-logs", ComplianceAuditLogViewSet, basename="complianceaudit")
router.register(r"compliance/credentials", CredentialVerificationViewSet, basename="compliancecredential")
router.register(r"compliance/regulatory-reports", RegulatoryReportViewSet, basename="regulatoryreport")
router.register(r"compliance/documents", ComplianceDocumentViewSet, basename="compliancedocument")
router.register(r"compliance/data-access", DataAccessConsentViewSet, basename="dataaccess")
router.register(r"medical/inventory", InventoryItemViewSet, basename="medicalinventory")
router.register(r"medical/diagnostic-orders", DiagnosticOrderViewSet, basename="medicaldiagnosticorder")
router.register(r"medical/imaging", ImagingStudyViewSet, basename="medicalimaging")
router.register(r"medical/adherence-reminders", MedicationAdherenceReminderViewSet, basename="medicaladherencereminder")
router.register(r"medical/supply-forecasts", SupplyForecastViewSet, basename="medicalsupplyforecast")

urlpatterns = [
    # Primary API routes for the core app
    path("", include((router.urls, app_name), namespace=app_name)),
    path("core/medical/context/", MedicalContextView.as_view(), name="medical-context"),
    path("core/clinical/command-center/", CommandCenterOverview.as_view(), name="clinical-command-center"),
]

# Optional: add schema / docs routes (uncomment if you use drf-yasg or drf-spectacular)
# from rest_framework.schemas import get_schema_view
# from rest_framework.documentation import include_docs_urls
#
# schema_view = get_schema_view(title="Core API")
# urlpatterns += [
#     path("api/core/schema/", schema_view, name="core-schema"),
#     path("api/core/docs/", include_docs_urls(title="Core API Docs")),
# ]
#
# If you use drf-yasg (recommended for Swagger UI), add:
# from drf_yasg.views import get_schema_view as yasg_get_schema_view
# from drf_yasg import openapi
# yasg_schema = yasg_get_schema_view(
#     openapi.Info(title="Core API", default_version="v1"),
#     public=True,
# )
# urlpatterns += [
#     path("api/core/swagger.json", yasg_schema.without_ui(cache_timeout=0), name="schema-json"),
#     path("api/core/swagger/", yasg_schema.with_ui("swagger", cache_timeout=0), name="schema-swagger-ui"),
# ]
