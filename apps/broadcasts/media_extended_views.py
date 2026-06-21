from decimal import Decimal

from django.db.models import Sum, Count
from django.utils import timezone
from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework import status, viewsets, filters
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

try:
    from django_filters.rest_framework import DjangoFilterBackend
    HAS_DJANGO_FILTERS = True
except ImportError:
    HAS_DJANGO_FILTERS = False

from .media_extended_models import (
    PodcastChannel,
    PodcastEpisode,
    KingdomMusicTrack,
    MusicPlaylist,
    RoyaltyRecord,
    Ebook,
    EbookPurchase,
    PPVEvent,
    PPVTicket,
    NewsArticle,
)
from .media_extended_serializers import (
    PodcastChannelSerializer,
    PodcastEpisodeSerializer,
    KingdomMusicTrackSerializer,
    MusicPlaylistSerializer,
    RoyaltyRecordSerializer,
    EbookSerializer,
    EbookPurchaseSerializer,
    PPVEventSerializer,
    PPVTicketSerializer,
    NewsArticleSerializer,
)


# ---------------------------------------------------------------------------
# Podcast
# ---------------------------------------------------------------------------

@extend_schema(tags=["Podcasts"])
class PodcastChannelViewSet(viewsets.ModelViewSet):
    queryset = PodcastChannel.objects.all()
    serializer_class = PodcastChannelSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["title", "category"]
    ordering_fields = ["created_at", "title"]

    def perform_create(self, serializer):
        serializer.save(creator=self.request.user)


@extend_schema(tags=["Podcasts"])
class PodcastEpisodeViewSet(viewsets.ModelViewSet):
    serializer_class = PodcastEpisodeSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ["published_at", "episode_number", "play_count"]

    def get_queryset(self):
        channel_id = self.kwargs.get("channel_pk")
        if channel_id:
            return PodcastEpisode.objects.filter(channel_id=channel_id)
        return PodcastEpisode.objects.all()

    @extend_schema(
        summary="Increment episode play count",
        responses={200: PodcastEpisodeSerializer},
    )
    @action(detail=True, methods=["post"], url_path="play")
    def play(self, request, pk=None, channel_pk=None):
        episode = self.get_object()
        episode.play_count += 1
        episode.save(update_fields=["play_count"])
        serializer = self.get_serializer(episode)
        return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(
    tags=["Podcasts"],
    summary="Get RSS feed for a podcast channel",
    responses={(200, "application/rss+xml"): None},
)
class PodcastRSSView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk=None):
        try:
            channel = PodcastChannel.objects.get(pk=pk)
        except PodcastChannel.DoesNotExist:
            return Response({"detail": "Channel not found."}, status=status.HTTP_404_NOT_FOUND)

        episodes = channel.episodes.filter(published_at__isnull=False).order_by("-published_at")[:50]

        items_xml = ""
        for ep in episodes:
            pub_date = ep.published_at.strftime("%a, %d %b %Y %H:%M:%S +0000") if ep.published_at else ""
            items_xml += f"""
    <item>
      <title><![CDATA[{ep.title}]]></title>
      <description><![CDATA[{ep.description}]]></description>
      <enclosure url="{ep.audio_url}" type="audio/mpeg"/>
      <pubDate>{pub_date}</pubDate>
      <guid isPermaLink="false">{ep.id}</guid>
      <itunes:duration>{ep.duration_seconds or 0}</itunes:duration>
      <itunes:episodeType>full</itunes:episodeType>
    </item>"""

        rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
  xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
  xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title><![CDATA[{channel.title}]]></title>
    <description><![CDATA[{channel.description}]]></description>
    <link>{channel.rss_feed_url}</link>
    <image><url>{channel.cover_url}</url></image>
    <itunes:category text="{channel.category}"/>
    <itunes:image href="{channel.cover_url}"/>
    {items_xml}
  </channel>
</rss>"""

        from django.http import HttpResponse
        return HttpResponse(rss, content_type="application/rss+xml; charset=utf-8")


# ---------------------------------------------------------------------------
# Music
# ---------------------------------------------------------------------------

@extend_schema(tags=["Music"])
class KingdomMusicTrackViewSet(viewsets.ModelViewSet):
    queryset = KingdomMusicTrack.objects.all()
    serializer_class = KingdomMusicTrackSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["title", "artist", "album", "genre"]
    ordering_fields = ["created_at", "play_count", "title", "artist"]

    def get_queryset(self):
        qs = KingdomMusicTrack.objects.all()
        genre = self.request.query_params.get("genre")
        artist = self.request.query_params.get("artist")
        if genre:
            qs = qs.filter(genre=genre)
        if artist:
            qs = qs.filter(artist__icontains=artist)
        return qs

    def perform_create(self, serializer):
        serializer.save(uploaded_by=self.request.user)

    @extend_schema(
        summary="Increment track play count",
        responses={200: KingdomMusicTrackSerializer},
    )
    @action(detail=True, methods=["post"], url_path="play")
    def play(self, request, pk=None):
        track = self.get_object()
        track.play_count += 1
        track.save(update_fields=["play_count"])
        serializer = self.get_serializer(track)
        return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(tags=["Music"])
class MusicPlaylistViewSet(viewsets.ModelViewSet):
    queryset = MusicPlaylist.objects.all()
    serializer_class = MusicPlaylistSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["title"]
    ordering_fields = ["created_at", "title"]

    def perform_create(self, serializer):
        serializer.save(creator=self.request.user)


# ---------------------------------------------------------------------------
# Royalties
# ---------------------------------------------------------------------------

@extend_schema(tags=["Royalties"])
class RoyaltyRecordViewSet(viewsets.ModelViewSet):
    queryset = RoyaltyRecord.objects.all()
    serializer_class = RoyaltyRecordSerializer

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [IsAdminUser()]
        return [IsAuthenticated()]

    def get_queryset(self):
        qs = RoyaltyRecord.objects.all()
        track_id = self.request.query_params.get("track")
        period_month = self.request.query_params.get("period_month")
        period_year = self.request.query_params.get("period_year")
        if track_id:
            qs = qs.filter(track_id=track_id)
        if period_month:
            qs = qs.filter(period_month=period_month)
        if period_year:
            qs = qs.filter(period_year=period_year)
        return qs


# ---------------------------------------------------------------------------
# Ebooks
# ---------------------------------------------------------------------------

@extend_schema(tags=["Ebooks"])
class EbookViewSet(viewsets.ModelViewSet):
    queryset = Ebook.objects.all()
    serializer_class = EbookSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["title", "author", "genre"]
    ordering_fields = ["created_at", "price", "download_count"]

    def perform_create(self, serializer):
        serializer.save(uploaded_by=self.request.user)

    @extend_schema(
        summary="Purchase an ebook",
        responses={201: EbookPurchaseSerializer, 400: None},
    )
    @action(detail=True, methods=["post"], url_path="purchase")
    def purchase(self, request, pk=None):
        ebook = self.get_object()

        already_purchased = EbookPurchase.objects.filter(
            ebook=ebook, buyer=request.user
        ).exists()
        if already_purchased:
            return Response(
                {"detail": "You have already purchased this ebook."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        purchase = EbookPurchase.objects.create(
            ebook=ebook,
            buyer=request.user,
            price_paid=ebook.price,
        )
        ebook.download_count += 1
        ebook.save(update_fields=["download_count"])

        serializer = EbookPurchaseSerializer(purchase)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# PPV
# ---------------------------------------------------------------------------

@extend_schema(tags=["PPV"])
class PPVEventViewSet(viewsets.ModelViewSet):
    queryset = PPVEvent.objects.all()
    serializer_class = PPVEventSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["title"]
    ordering_fields = ["scheduled_at", "created_at"]

    def perform_create(self, serializer):
        serializer.save(creator=self.request.user)

    @extend_schema(
        summary="Purchase a PPV ticket",
        responses={201: PPVTicketSerializer, 400: None},
    )
    @action(detail=True, methods=["post"], url_path="purchase")
    def purchase(self, request, pk=None):
        event = self.get_object()

        already_has_ticket = PPVTicket.objects.filter(
            event=event, buyer=request.user
        ).exists()
        if already_has_ticket:
            return Response(
                {"detail": "You already have a ticket for this event."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ticket = PPVTicket.objects.create(
            event=event,
            buyer=request.user,
            price_paid=event.price,
            access_granted=True,
        )
        serializer = PPVTicketSerializer(ticket)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="Get stream URL if user holds a valid ticket",
        responses={200: None, 403: None, 404: None},
    )
    @action(detail=True, methods=["get"], url_path="stream")
    def stream(self, request, pk=None):
        event = self.get_object()

        ticket = PPVTicket.objects.filter(
            event=event, buyer=request.user, access_granted=True
        ).first()
        if not ticket:
            return Response(
                {"detail": "No valid ticket found for this event."},
                status=status.HTTP_403_FORBIDDEN,
            )

        return Response(
            {
                "stream_url": event.stream_url,
                "event_id": str(event.id),
                "title": event.title,
                "scheduled_at": event.scheduled_at,
            },
            status=status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------

@extend_schema(tags=["News"])
class NewsArticleViewSet(viewsets.ModelViewSet):
    queryset = NewsArticle.objects.all().order_by("-published_at")
    serializer_class = NewsArticleSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["title", "author", "source", "category"]
    ordering_fields = ["published_at", "credibility_score"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [AllowAny()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


# ---------------------------------------------------------------------------
# Creator Analytics
# ---------------------------------------------------------------------------

@extend_schema(
    tags=["Analytics"],
    summary="Aggregated creator analytics: plays, downloads, and revenue",
    responses={200: None},
)
class CreatorAnalyticsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        # Podcast plays
        podcast_plays = (
            PodcastEpisode.objects.filter(channel__creator=user)
            .aggregate(total=Sum("play_count"))["total"] or 0
        )

        # Music plays
        music_plays = (
            KingdomMusicTrack.objects.filter(uploaded_by=user)
            .aggregate(total=Sum("play_count"))["total"] or 0
        )

        # Ebook downloads
        ebook_downloads = (
            Ebook.objects.filter(uploaded_by=user)
            .aggregate(total=Sum("download_count"))["total"] or 0
        )

        # Ebook revenue
        ebook_revenue = (
            EbookPurchase.objects.filter(ebook__uploaded_by=user)
            .aggregate(total=Sum("price_paid"))["total"] or Decimal("0.00")
        )

        # PPV revenue
        ppv_revenue = (
            PPVTicket.objects.filter(event__creator=user)
            .aggregate(total=Sum("price_paid"))["total"] or Decimal("0.00")
        )

        # Royalty revenue
        royalty_revenue = (
            RoyaltyRecord.objects.filter(track__uploaded_by=user)
            .aggregate(total=Sum("total_royalty"))["total"] or Decimal("0.00")
        )

        total_revenue = ebook_revenue + ppv_revenue + royalty_revenue

        return Response(
            {
                "podcast_plays": podcast_plays,
                "music_plays": music_plays,
                "total_plays": podcast_plays + music_plays,
                "ebook_downloads": ebook_downloads,
                "ebook_revenue": str(ebook_revenue),
                "ppv_revenue": str(ppv_revenue),
                "royalty_revenue": str(royalty_revenue),
                "total_revenue": str(total_revenue),
            },
            status=status.HTTP_200_OK,
        )
