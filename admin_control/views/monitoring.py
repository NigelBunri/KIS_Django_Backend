"""Endpoints for advanced monitoring alerts."""
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from admin_control.monitoring.alerts import MonitoringAlertService
from admin_control.permissions import IsAdminControlUser


class MonitoringAlertView(APIView):
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "monitoring.view"
    required_app_label = "admin_control"

    def get(self, request):
        alerts = MonitoringAlertService().detect()
        return Response({"alerts": alerts})
