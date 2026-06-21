from django.urls import path, include
from rest_framework.routers import DefaultRouter

try:
    from rest_framework_nested.routers import NestedDefaultRouter
except ImportError:
    NestedDefaultRouter = None

from .media_extended_views import (
    PodcastChannelViewSet,
    PodcastEpisodeViewSet,
    PodcastRSSView,
    KingdomMusicTrackViewSet,
    MusicPlaylistViewSet,
    RoyaltyRecordViewSet,
    EbookViewSet,
    PPVEventViewSet,
    NewsArticleViewSet,
    CreatorAnalyticsView,
)

router = DefaultRouter()
router.register(r"podcasts", PodcastChannelViewSet, basename="podcast-channel")
router.register(r"music", KingdomMusicTrackViewSet, basename="music-track")
router.register(r"playlists", MusicPlaylistViewSet, basename="music-playlist")
router.register(r"royalties", RoyaltyRecordViewSet, basename="royalty-record")
router.register(r"ebooks", EbookViewSet, basename="ebook")
router.register(r"ppv", PPVEventViewSet, basename="ppv-event")
router.register(r"news", NewsArticleViewSet, basename="news-article")

# Nested router for episodes under a channel
try:
    if NestedDefaultRouter is None:
        nested_urls = []
    else:
        podcasts_router = NestedDefaultRouter(router, r"podcasts", lookup="channel")
        podcasts_router.register(r"episodes", PodcastEpisodeViewSet, basename="podcast-episode")
        nested_urls = podcasts_router.urls
except Exception:
    # Fallback: flat episodes route if drf-nested-routers is not installed
    nested_urls = []

urlpatterns = [
    path("", include(router.urls)),
    path("", include(nested_urls)),
    # Podcast RSS feed
    path("podcasts/<uuid:pk>/rss/", PodcastRSSView.as_view(), name="podcast-rss"),
    # Creator analytics
    path("creator/analytics/", CreatorAnalyticsView.as_view(), name="creator-analytics"),
]
