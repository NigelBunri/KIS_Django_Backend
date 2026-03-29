"""Views for admin dashboards."""
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from admin_control.dashboards import collect_global_metrics
from admin_control.permissions import IsAdminControlUser
from admin_control.serializers import DashboardSummarySerializer


class DashboardOverviewView(APIView):
    """Entrypoint for the main admin control dashboard."""

    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "dashboard.view"
    required_app_label = "admin_control"

    def get(self, request):
        payload = collect_global_metrics()
        serializer = DashboardSummarySerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data)
