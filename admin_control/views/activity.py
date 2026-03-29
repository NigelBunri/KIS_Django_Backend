"""Activity monitoring views for admin control."""
from django.core.paginator import Paginator
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from admin_control.models import AdminUserActivity
from admin_control.permissions import IsAdminControlUser
from admin_control.serializers import ActivityStreamSerializer


class ActivityStreamView(APIView):
    """Paginated return of admin activity log entries."""

    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "activity.view"
    required_app_label = "admin_control"

    def get(self, request):
        actor_id = request.query_params.get("actor_id")
        status_code = request.query_params.get("status_code")
        try:
            page = max(1, int(request.query_params.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        try:
            per_page = max(1, int(request.query_params.get("per_page", 25)))
        except (TypeError, ValueError):
            per_page = 25
        queryset = AdminUserActivity.objects.all()
        if actor_id:
            queryset = queryset.filter(actor_id=actor_id)
        if status_code:
            queryset = queryset.filter(status_code=status_code)
        queryset = queryset.order_by("-created_at")
        paginator = Paginator(queryset, per_page)
        page_obj = paginator.get_page(page)
        serializer = ActivityStreamSerializer(page_obj.object_list, many=True)
        return Response(
            {
                "stream": serializer.data,
                "pagination": {
                    "page": page_obj.number,
                    "per_page": per_page,
                    "total_pages": paginator.num_pages,
                    "total_items": paginator.count,
                },
            }
        )
