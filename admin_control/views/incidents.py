"""Security incident tracking - see SecurityIncident's docstring
(admin_control/models.py) for what this is and isn't a substitute for.
"""
from django.core.paginator import Paginator
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from admin_control.audit.logging import AuditLogger
from admin_control.models import SecurityIncident
from admin_control.permissions import IsAdminControlUser
from admin_control.serializers import (
    SecurityIncidentCreateSerializer,
    SecurityIncidentSerializer,
    SecurityIncidentUpdateSerializer,
)


def _safe_int(val, default, lo=1, hi=250):
    try:
        return max(lo, min(int(val), hi))
    except (TypeError, ValueError):
        return default


class AdminIncidentListView(APIView):
    """
    GET  /control/admin/incidents/ - list, filterable by status/severity.
    POST /control/admin/incidents/ - log a new incident.
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "incidents.manage"

    def get(self, request):
        qs = SecurityIncident.objects.all()
        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        severity = request.query_params.get("severity")
        if severity:
            qs = qs.filter(severity=severity)

        page_num = _safe_int(request.query_params.get("page", 1), 1, lo=1, hi=10000)
        per_page = _safe_int(request.query_params.get("per_page", 25), 25, lo=1, hi=100)
        paginator = Paginator(qs, per_page)
        page_obj = paginator.get_page(page_num)

        return Response({
            "incidents": SecurityIncidentSerializer(page_obj.object_list, many=True).data,
            "pagination": {
                "page": page_obj.number,
                "per_page": per_page,
                "total_pages": paginator.num_pages,
                "total_items": paginator.count,
            },
        })

    def post(self, request):
        serializer = SecurityIncidentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        incident = SecurityIncident.objects.create(
            reported_by=request.user,
            **serializer.validated_data,
        )
        AuditLogger.log(
            actor=request.user,
            action_type="incident.reported",
            target_app="admin_control",
            target_model="SecurityIncident",
            target_pk=str(incident.id),
            severity="warning",
            metadata={"title": incident.title, "severity": incident.severity},
        )
        return Response(SecurityIncidentSerializer(incident).data, status=201)


class AdminIncidentDetailView(APIView):
    """GET/PATCH /control/admin/incidents/<incident_id>/"""
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "incidents.manage"

    def _get_incident(self, incident_id):
        try:
            return SecurityIncident.objects.get(pk=incident_id)
        except (SecurityIncident.DoesNotExist, ValueError, TypeError):
            return None

    def get(self, request, incident_id):
        incident = self._get_incident(incident_id)
        if incident is None:
            return Response({"detail": "Not found."}, status=404)
        return Response(SecurityIncidentSerializer(incident).data)

    def patch(self, request, incident_id):
        incident = self._get_incident(incident_id)
        if incident is None:
            return Response({"detail": "Not found."}, status=404)

        serializer = SecurityIncidentUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        previous_status = incident.status
        for field, value in data.items():
            setattr(incident, field, value)
        # Stamp resolved_at the moment status actually transitions into
        # resolved/closed - not just whenever the field happens to be
        # patched, and not overwritten on a later unrelated edit.
        newly_resolved = (
            data.get("status") in (SecurityIncident.Status.RESOLVED, SecurityIncident.Status.CLOSED)
            and previous_status not in (SecurityIncident.Status.RESOLVED, SecurityIncident.Status.CLOSED)
        )
        if newly_resolved and not incident.resolved_at:
            incident.resolved_at = timezone.now()
        incident.save()

        AuditLogger.log(
            actor=request.user,
            action_type="incident.updated",
            target_app="admin_control",
            target_model="SecurityIncident",
            target_pk=str(incident.id),
            severity="warning" if incident.severity in (SecurityIncident.Severity.HIGH, SecurityIncident.Severity.CRITICAL) else "info",
            metadata={"fields_changed": list(data.keys()), "status": incident.status},
        )
        return Response(SecurityIncidentSerializer(incident).data)


class AdminIncidentSummaryView(APIView):
    """GET /control/admin/incidents/summary/ - counts by status/severity,
    plus how many open/investigating incidents still owe an undetermined
    or unsent regulatory notification decision."""
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "incidents.manage"

    def get(self, request):
        from django.db.models import Count

        by_status = list(SecurityIncident.objects.values("status").annotate(count=Count("id")))
        by_severity = list(
            SecurityIncident.objects.exclude(status=SecurityIncident.Status.CLOSED)
            .values("severity").annotate(count=Count("id"))
        )
        open_statuses = [SecurityIncident.Status.OPEN, SecurityIncident.Status.INVESTIGATING, SecurityIncident.Status.CONTAINED]
        pending_notification_decision = SecurityIncident.objects.filter(
            status__in=open_statuses, regulatory_notification_required__isnull=True,
        ).count()
        notification_owed_not_sent = SecurityIncident.objects.filter(
            regulatory_notification_required=True, regulatory_notification_sent_at__isnull=True,
        ).count()
        return Response({
            "by_status": by_status,
            "by_severity": by_severity,
            "pending_notification_decision": pending_notification_decision,
            "notification_owed_not_sent": notification_owed_not_sent,
        })
