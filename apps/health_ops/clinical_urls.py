from django.urls import path

from .clinical_views import (
    ClinicalReferralActionView,
    ClinicalReferralDetailView,
    ClinicalReferralListCreateView,
    EncounterAllergyListCreateView,
    EncounterConditionListCreateView,
    EncounterDetailView,
    EncounterImmunizationListCreateView,
    EncounterListCreateView,
    EncounterPrescriptionListCreateView,
    EncounterTransitionView,
    LabOrderDetailView,
    LabOrderListCreateView,
    LabResultEntryView,
    LabResultVerifyView,
    LabSpecimenCollectView,
    LabTestCatalogView,
    PrescriptionRevokeView,
)

urlpatterns = [
    path("encounters/", EncounterListCreateView.as_view(), name="health-encounter-list-create"),
    path(
        "encounters/<uuid:encounter_id>/conditions/",
        EncounterConditionListCreateView.as_view(),
        name="health-encounter-conditions",
    ),
    path(
        "encounters/<uuid:encounter_id>/allergies/",
        EncounterAllergyListCreateView.as_view(),
        name="health-encounter-allergies",
    ),
    path(
        "encounters/<uuid:encounter_id>/immunizations/",
        EncounterImmunizationListCreateView.as_view(),
        name="health-encounter-immunizations",
    ),
    path(
        "encounters/<uuid:encounter_id>/prescriptions/",
        EncounterPrescriptionListCreateView.as_view(),
        name="health-encounter-prescriptions",
    ),
    path("prescriptions/<uuid:prescription_id>/revoke/", PrescriptionRevokeView.as_view(), name="health-prescription-revoke"),
    path("encounters/<uuid:encounter_id>/", EncounterDetailView.as_view(), name="health-encounter-detail"),
    path(
        "encounters/<uuid:encounter_id>/<str:action>/",
        EncounterTransitionView.as_view(),
        name="health-encounter-transition",
    ),
    path("referrals/", ClinicalReferralListCreateView.as_view(), name="health-referral-list-create"),
    path("referrals/<uuid:referral_id>/", ClinicalReferralDetailView.as_view(), name="health-referral-detail"),
    path(
        "referrals/<uuid:referral_id>/<str:action>/",
        ClinicalReferralActionView.as_view(),
        name="health-referral-action",
    ),
    path("lab/catalog/", LabTestCatalogView.as_view(), name="health-lab-catalog"),
    path("lab/orders/", LabOrderListCreateView.as_view(), name="health-lab-order-list-create"),
    path("lab/orders/<uuid:order_id>/", LabOrderDetailView.as_view(), name="health-lab-order-detail"),
    path("lab/orders/<uuid:order_id>/collect-specimen/", LabSpecimenCollectView.as_view(), name="health-lab-order-collect"),
    path("lab/orders/<uuid:order_id>/enter-result/", LabResultEntryView.as_view(), name="health-lab-order-enter-result"),
    path("lab/orders/<uuid:order_id>/verify-result/", LabResultVerifyView.as_view(), name="health-lab-order-verify-result"),
]
