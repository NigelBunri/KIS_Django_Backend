"""App-level micro analytics views."""
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from admin_control.analytics.micro import MicroAppInsightService
from admin_control.permissions import IsAdminControlUser


class MicroAnalyticsView(APIView):
    """Returns per-app micro analytics payloads."""

    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "micro.view"
    required_app_label = "admin_control"

    def get(self, request):
        data = MicroAppInsightService().collect()
        return Response({"micro_apps": data})
