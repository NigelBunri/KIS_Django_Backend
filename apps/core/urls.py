# core/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    PermissionViewSet,
    RoleViewSet,
    RoleAssignmentViewSet,
    AccessControlEntryViewSet,
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
    WellnessMetricViewSet,
    ProblemRecordViewSet,
    ImmunizationRecordViewSet,
    ProcedureRecordViewSet,
    HealthDocumentViewSet,
    HealthRecordExchangeLogViewSet,
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
    HealthDataAccessGrantViewSet,
    AIAssistanceSafetyPolicyView,
    CommandCenterOverview,
    MedicalContextView,
    MonetizationSafetySummaryView,
    PerformanceOfflinePolicyView,
    SecurityPrivacyLaunchGateView,
    LaunchOperationsReadinessView,
    StaffSafetyCommandCenterView,
    SocialRecommendationFoundationView,
    UnifiedPlatformDashboardSummaryView,
    UnifiedSearchView,
    LinkPreviewView,
    TranslateView,
)

app_name = "core"

router = DefaultRouter()
router.register(r"permissions", PermissionViewSet, basename="permission")
router.register(r"roles", RoleViewSet, basename="role")
router.register(r"role-assignments", RoleAssignmentViewSet, basename="roleassignment")
router.register(r"aces", AccessControlEntryViewSet, basename="ace")
# apps.core's generic Community/Group/Channel/Membership/Invite/
# ModerationAction/*Settings viewsets (and the RBAC Permission/Role/
# RoleAssignment/ACE system's Group.has_permission()/Community equivalents
# that back them) are an early, superseded generic social-platform scaffold
# — apps.communities, apps.groups, apps.channels, and apps.chat's own
# moderation each replaced this with real, chat/conversation-integrated
# implementations, but these registrations were never removed.
# Deregistering them here is NOT cosmetic: apps.core.urls is included
# BEFORE apps.communities.urls in config/urls.py, so CommunityViewSet here
# was silently shadowing ALL of /api/v1/communities/ — confirmed via
# django.urls.resolve() that every request there (list/retrieve/even
# /communities/posts/) was being handled by this dead viewset instead of
# apps.communities.views.CommunityViewSet, which only became reachable
# again once this line was removed. /api/v1/groups/ and /api/v1/channels/
# had no such live collision (apps.groups/apps.channels were already
# rerouted to /api/v1/chat-groups/ and /api/v1/partner-channels/ before
# this change), so removing them here is pure dead-surface cleanup.
# Models/migrations are untouched — any stray existing rows remain
# accessible via the ORM/admin, matching apps.tiers's quarantine pattern.
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
router.register(r"patients/wellness-metrics", WellnessMetricViewSet, basename="patientwellnessmetric")
router.register(r"patients/problems", ProblemRecordViewSet, basename="patientproblem")
router.register(r"patients/immunizations", ImmunizationRecordViewSet, basename="patientimmunization")
router.register(r"patients/procedures", ProcedureRecordViewSet, basename="patientprocedure")
router.register(r"patients/documents", HealthDocumentViewSet, basename="patientdocument")
router.register(r"patients/exchange-logs", HealthRecordExchangeLogViewSet, basename="patientexchangelog")
router.register(r"telemedicine/sessions", TelemedicineSessionViewSet, basename="telemedsession")
router.register(r"telemedicine/devices", TelemedicineDeviceViewSet, basename="telemeddevice")
router.register(r"telemedicine/dictations", VoiceDictationViewSet, basename="telemeddictation")
router.register(r"core/clinical/tasks", ClinicalTaskViewSet, basename="clinicaltask")
router.register(r"core/clinical/escalations", EmergencyEscalationViewSet, basename="clinicalescalation")
router.register(r"core/clinical/triage", TriageRecordViewSet, basename="clinicaltriage")
router.register(r"core/clinical/referrals", ReferralRouteViewSet, basename="clinicalreferral")
router.register(r"core/clinical/events", ClinicalEventLogViewSet, basename="clinicalevent")
# compliance / governance
router.register(r"compliance/audit-logs", ComplianceAuditLogViewSet, basename="complianceaudit")
router.register(r"compliance/credentials", CredentialVerificationViewSet, basename="compliancecredential")
router.register(r"compliance/regulatory-reports", RegulatoryReportViewSet, basename="regulatoryreport")
router.register(r"compliance/documents", ComplianceDocumentViewSet, basename="compliancedocument")
router.register(r"compliance/data-access", DataAccessConsentViewSet, basename="dataaccess")
router.register(r"patients/access-grants", HealthDataAccessGrantViewSet, basename="patientaccessgrant")
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
    path("core/search/unified/", UnifiedSearchView.as_view(), name="core-unified-search"),
    path("core/recommendations/foundation/", SocialRecommendationFoundationView.as_view(), name="social-recommendation-foundation"),
    path("core/dashboards/unified/", UnifiedPlatformDashboardSummaryView.as_view(), name="core-unified-dashboard"),
    path("core/admin/safety-command-center/", StaffSafetyCommandCenterView.as_view(), name="core-admin-safety-command-center"),
    path("core/performance/offline-policy/", PerformanceOfflinePolicyView.as_view(), name="core-performance-offline-policy"),
    path("core/admin/security-launch-gate/", SecurityPrivacyLaunchGateView.as_view(), name="core-admin-security-launch-gate"),
    path("core/admin/launch-ops-readiness/", LaunchOperationsReadinessView.as_view(), name="core-admin-launch-ops-readiness"),
    path("core/monetization/safety-summary/", MonetizationSafetySummaryView.as_view(), name="core-monetization-safety-summary"),
    path("core/ai/safety-policy/", AIAssistanceSafetyPolicyView.as_view(), name="core-ai-safety-policy"),
    path("link-preview/", LinkPreviewView.as_view(), name="link-preview"),
    path("translate/", TranslateView.as_view(), name="translate"),
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
