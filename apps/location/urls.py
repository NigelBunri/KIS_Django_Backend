from django.urls import path

from .views import (
    LocationAuditLogView,
    LocationAttendanceExportView,
    LocationAttendanceListView,
    LocationCheckinView,
    LocationConsentView,
    LocationEventDetailView,
    LocationEventListView,
    LocationManualCheckinView,
    LocationMyStatusView,
    LocationZoneListView,
)

app_name = "location"

urlpatterns = [
    # Events
    path("<uuid:partner_id>/location/events/", LocationEventListView.as_view(), name="event-list"),
    path("<uuid:partner_id>/location/events/<uuid:event_id>/", LocationEventDetailView.as_view(), name="event-detail"),

    # Check-in (member)
    path("<uuid:partner_id>/location/events/<uuid:event_id>/checkin/", LocationCheckinView.as_view(), name="checkin"),
    path("<uuid:partner_id>/location/events/<uuid:event_id>/my-status/", LocationMyStatusView.as_view(), name="my-status"),

    # Attendance (admin)
    path("<uuid:partner_id>/location/events/<uuid:event_id>/attendance/", LocationAttendanceListView.as_view(), name="attendance-list"),
    path("<uuid:partner_id>/location/events/<uuid:event_id>/attendance/manual-checkin/", LocationManualCheckinView.as_view(), name="manual-checkin"),
    path("<uuid:partner_id>/location/events/<uuid:event_id>/attendance/export/", LocationAttendanceExportView.as_view(), name="attendance-export"),

    # Zones (admin manage / member read)
    path("<uuid:partner_id>/location/events/<uuid:event_id>/zones/", LocationZoneListView.as_view(), name="zone-list"),

    # Consent
    path("<uuid:partner_id>/location/consent/", LocationConsentView.as_view(), name="consent"),

    # Audit
    path("<uuid:partner_id>/location/audit/", LocationAuditLogView.as_view(), name="audit-log"),
    path("<uuid:partner_id>/location/events/<uuid:event_id>/audit/", LocationAuditLogView.as_view(), name="event-audit-log"),
]
