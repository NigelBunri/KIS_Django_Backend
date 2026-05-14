from django.urls import path

from .views import (
    StaffVerificationAuditEventsView,
    StaffVerificationBadgeIssueView,
    StaffVerificationBadgeRevokeView,
    StaffVerificationCaseDetailView,
    StaffVerificationExpiryRemindersView,
    StaffVerificationProviderCallbacksView,
    StaffVerificationQueueView,
    StaffVerificationSuspiciousSignalsView,
    StaffUserVerificationReviewView,
    UserVerificationEvidenceView,
    UserVerificationStartView,
    UserVerificationStatusView,
    VerificationWebhookView,
)

app_name = "verification"

urlpatterns = [
    path("user/status/", UserVerificationStatusView.as_view(), name="user-status"),
    path("user/start/", UserVerificationStartView.as_view(), name="user-start"),
    path("user/cases/<uuid:case_id>/evidence/", UserVerificationEvidenceView.as_view(), name="user-evidence"),
    path("staff/user/cases/<uuid:case_id>/review/", StaffUserVerificationReviewView.as_view(), name="staff-user-review"),
    path("staff/cases/", StaffVerificationQueueView.as_view(), name="staff-cases"),
    path("staff/cases/<uuid:case_id>/", StaffVerificationCaseDetailView.as_view(), name="staff-case-detail"),
    path("staff/badges/issue/", StaffVerificationBadgeIssueView.as_view(), name="staff-badge-issue"),
    path("staff/badges/<uuid:badge_id>/revoke/", StaffVerificationBadgeRevokeView.as_view(), name="staff-badge-revoke"),
    path("staff/audit-events/", StaffVerificationAuditEventsView.as_view(), name="staff-audit-events"),
    path("staff/provider-callbacks/", StaffVerificationProviderCallbacksView.as_view(), name="staff-provider-callbacks"),
    path("staff/suspicious-signals/", StaffVerificationSuspiciousSignalsView.as_view(), name="staff-suspicious-signals"),
    path("staff/expiry-reminders/", StaffVerificationExpiryRemindersView.as_view(), name="staff-expiry-reminders"),
    path("webhooks/<str:provider>/", VerificationWebhookView.as_view(), name="webhook"),
]
