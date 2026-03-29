from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .service import FEED_PERSONALIZATION_FEED_TYPES, log_feed_interaction


class FeedPersonalizationEventSerializer(serializers.Serializer):
    feed_type = serializers.ChoiceField(choices=FEED_PERSONALIZATION_FEED_TYPES)
    event = serializers.CharField(max_length=64)
    target_id = serializers.CharField(max_length=128, required=False, allow_blank=True)
    duration_ms = serializers.FloatField(min_value=0, required=False)
    weight = serializers.FloatField(min_value=0, max_value=1.0, required=False)
    metadata = serializers.JSONField(required=False)


class FeedPersonalizationEventView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = FeedPersonalizationEventSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        weight = data.get("weight")
        duration_ms = data.get("duration_ms")
        if duration_ms and not weight:
            weight = min(1.0, duration_ms / 20000.0)
        log_feed_interaction(
            request.user,
            data["feed_type"],
            data["event"],
            weight=weight or 0.1,
        )
        return Response({"detail": "Feed event recorded."}, status=status.HTTP_200_OK)
