"""Performance endpoints for the admin UI."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from admin_control.monitoring.performance import PerformanceInsightService
from admin_control.permissions import IsAdminControlUser


class PerformanceInsightsView(APIView):
    """Return curated performance KPIs for Phase 12 dashboards."""

    permission_classes = [IsAuthenticated, IsAdminControlUser]

    def get(self, request):
        return Response({"insights": PerformanceInsightService().collect()})
