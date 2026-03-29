"""Live dashboard metrics for streaming widgets."""
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from admin_control.analytics import AnalyticsInsightService
from admin_control.permissions import IsAdminControlUser


class LiveMetricsView(APIView):
    """Focused view returning the fast-refresh metrics for the dashboard."""

    permission_classes = [IsAuthenticated, IsAdminControlUser]

    def get(self, request):
        insights = AnalyticsInsightService().collect()
        return Response(
            {
                "generated_at": insights.get("generated_at"),
                "widgets": insights.get("widgets"),
                "graphs": insights.get("graphs"),
                "live_activity": insights.get("live_activity"),
                "api_usage_per_endpoint": insights.get("api_usage_per_endpoint"),
                "message_throughput": insights.get("message_throughput"),
                "institution_activity": insights.get("institution_activity"),
            }
        )
