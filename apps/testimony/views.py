from django.utils import timezone
from django.db.models import Q
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.response import Response
from rest_framework.views import APIView
from . import models, serializers


class SeasonListCreateView(generics.ListCreateAPIView):
    """
    GET  — browse active public seasons (filtered by category).
           Authenticated users also see testimony_only seasons.
    POST — declare a new season for yourself.
    """
    permission_classes = [IsAuthenticatedOrReadOnly]
    serializer_class = serializers.UserSeasonSerializer

    def get_queryset(self):
        qs = models.UserSeason.objects.filter(is_active=True).select_related("user", "user__profile")
        if self.request.user.is_authenticated:
            qs = qs.exclude(visibility="private").exclude(user=self.request.user)
        else:
            qs = qs.filter(visibility="public")

        category = self.request.query_params.get("category", "").strip()
        if category:
            qs = qs.filter(category=category)
        return qs

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class SeasonDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = serializers.UserSeasonSerializer

    def get_queryset(self):
        return models.UserSeason.objects.filter(user=self.request.user)

    def perform_update(self, serializer):
        data = serializer.validated_data
        if not data.get("is_active", True):
            serializer.save(resolved_at=timezone.now())
        else:
            serializer.save()


class MySeasonListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = serializers.UserSeasonSerializer

    def get_queryset(self):
        return models.UserSeason.objects.filter(user=self.request.user).order_by("-created_at")


class TestimonyListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticatedOrReadOnly]
    serializer_class = serializers.UserTestimonySerializer

    def get_queryset(self):
        qs = models.UserTestimony.objects.filter(is_available=True).select_related("user", "user__profile")
        category = self.request.query_params.get("category", "").strip()
        if category:
            qs = qs.filter(category=category)
        user_id = self.request.query_params.get("user_id", "").strip()
        if user_id:
            qs = models.UserTestimony.objects.filter(user_id=user_id).select_related("user", "user__profile")
        return qs

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class TestimonyDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = serializers.UserTestimonySerializer

    def get_queryset(self):
        return models.UserTestimony.objects.filter(user=self.request.user)


class EndorseTestimonyView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        testimony = models.UserTestimony.objects.filter(pk=pk).first()
        if not testimony:
            return Response({"detail": "Not found."}, status=404)
        if testimony.user == request.user:
            return Response({"detail": "Cannot endorse your own testimony."}, status=400)
        _, created = models.TestimonyEndorsement.objects.get_or_create(
            testimony=testimony, endorsed_by=request.user
        )
        if created:
            testimony.endorsement_count = models.TestimonyEndorsement.objects.filter(testimony=testimony).count()
            testimony.save(update_fields=["endorsement_count"])
        return Response({"endorsement_count": testimony.endorsement_count})


class ReachOutView(APIView):
    """Testimony holder reaches out to someone in a season."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        reaches = models.TestimonyReach.objects.filter(
            Q(from_user=request.user) | Q(to_user=request.user)
        ).select_related(
            "from_user", "from_user__profile",
            "to_user", "to_user__profile",
            "season", "testimony",
            "season__user", "testimony__user",
        ).order_by("-created_at")
        return Response(serializers.TestimonyReachSerializer(reaches, many=True).data)

    def post(self, request):
        season_id   = request.data.get("season_id")
        testimony_id = request.data.get("testimony_id")
        message     = str(request.data.get("message", "")).strip()

        season = models.UserSeason.objects.filter(pk=season_id, is_active=True).first()
        if not season:
            return Response({"detail": "Season not found or inactive."}, status=404)
        if season.user == request.user:
            return Response({"detail": "Cannot reach out to yourself."}, status=400)

        testimony = models.UserTestimony.objects.filter(pk=testimony_id, user=request.user, is_available=True).first()
        if not testimony:
            return Response({"detail": "Testimony not found or not available."}, status=404)

        if testimony.category != season.category:
            return Response({"detail": "Testimony category does not match season."}, status=400)

        existing = models.TestimonyReach.objects.filter(from_user=request.user, season=season).first()
        if existing:
            return Response(serializers.TestimonyReachSerializer(existing).data, status=200)

        reach = models.TestimonyReach.objects.create(
            from_user=request.user,
            to_user=season.user,
            season=season,
            testimony=testimony,
            message=message,
        )

        # send notification to the person in the season
        try:
            from apps.notifications.models import Notification
            from apps.notifications.tasks import process_notification_delivery
            notif = Notification.objects.create(
                user_id=season.user_id,
                type="testimony_reach",
                title=f"{request.user.display_name or 'Someone'} has been there too",
                body=message or f"{request.user.display_name or 'Someone'} overcame {testimony.get_category_display()} and wants to connect.",
                context_data={
                    "reach_id": str(reach.id),
                    "from_user_id": str(request.user.id),
                    "testimony_id": str(testimony.id),
                    "category": testimony.category,
                },
            )
            process_notification_delivery.delay(str(notif.id))
        except Exception:
            pass

        return Response(serializers.TestimonyReachSerializer(reach).data, status=201)


class ReachRespondView(APIView):
    """Person in a season accepts or declines a reach-out."""
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        reach = models.TestimonyReach.objects.filter(pk=pk, to_user=request.user).first()
        if not reach:
            return Response({"detail": "Not found."}, status=404)
        new_status = request.data.get("status")
        if new_status not in (models.TestimonyReach.STATUS_ACCEPTED, models.TestimonyReach.STATUS_DECLINED):
            return Response({"detail": "status must be 'accepted' or 'declined'."}, status=400)
        reach.status = new_status
        reach.save(update_fields=["status", "updated_at"])

        if new_status == models.TestimonyReach.STATUS_ACCEPTED:
            try:
                from apps.notifications.models import Notification
                from apps.notifications.tasks import process_notification_delivery
                notif = Notification.objects.create(
                    user_id=reach.from_user_id,
                    type="reach_accepted",
                    title=f"{request.user.display_name or 'Someone'} accepted your reach-out",
                    body="Your offer to connect has been accepted. Start a conversation.",
                    context_data={"reach_id": str(reach.id), "to_user_id": str(reach.to_user_id)},
                )
                process_notification_delivery.delay(str(notif.id))
            except Exception:
                pass

        return Response(serializers.TestimonyReachSerializer(reach).data)
