from rest_framework import serializers

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


class PodcastChannelSerializer(serializers.ModelSerializer):
    class Meta:
        model = PodcastChannel
        fields = [
            "id",
            "title",
            "description",
            "creator",
            "cover_url",
            "category",
            "rss_feed_url",
            "is_active",
            "subscription_required",
            "created_at",
            "updated_at",
        ]


class PodcastEpisodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = PodcastEpisode
        fields = [
            "id",
            "channel",
            "title",
            "description",
            "audio_url",
            "transcript",
            "duration_seconds",
            "chapter_markers",
            "episode_number",
            "season_number",
            "published_at",
            "play_count",
            "is_premium",
            "created_at",
            "updated_at",
        ]


class KingdomMusicTrackSerializer(serializers.ModelSerializer):
    class Meta:
        model = KingdomMusicTrack
        fields = [
            "id",
            "title",
            "artist",
            "album",
            "genre",
            "audio_url",
            "cover_url",
            "duration_seconds",
            "lyrics",
            "ccli_number",
            "play_count",
            "is_licensed",
            "royalty_per_play",
            "uploaded_by",
            "release_year",
            "created_at",
            "updated_at",
        ]


class MusicPlaylistSerializer(serializers.ModelSerializer):
    class Meta:
        model = MusicPlaylist
        fields = [
            "id",
            "title",
            "creator",
            "tracks",
            "cover_url",
            "is_public",
            "created_at",
            "updated_at",
        ]


class RoyaltyRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = RoyaltyRecord
        fields = [
            "id",
            "track",
            "period_month",
            "period_year",
            "play_count",
            "total_royalty",
            "paid_at",
            "created_at",
            "updated_at",
        ]


class EbookSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ebook
        fields = [
            "id",
            "title",
            "author",
            "description",
            "cover_url",
            "file_url",
            "price",
            "currency",
            "is_free",
            "genre",
            "uploaded_by",
            "download_count",
            "created_at",
            "updated_at",
        ]


class EbookPurchaseSerializer(serializers.ModelSerializer):
    class Meta:
        model = EbookPurchase
        fields = [
            "id",
            "ebook",
            "buyer",
            "price_paid",
            "purchased_at",
            "created_at",
            "updated_at",
        ]


class PPVEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = PPVEvent
        fields = [
            "id",
            "title",
            "description",
            "creator",
            "price",
            "currency",
            "stream_url",
            "scheduled_at",
            "ended_at",
            "access_code",
            "thumbnail_url",
            "viewer_count",
            "created_at",
            "updated_at",
        ]


class PPVTicketSerializer(serializers.ModelSerializer):
    class Meta:
        model = PPVTicket
        fields = [
            "id",
            "event",
            "buyer",
            "price_paid",
            "access_granted",
            "created_at",
            "updated_at",
        ]


class NewsArticleSerializer(serializers.ModelSerializer):
    class Meta:
        model = NewsArticle
        fields = [
            "id",
            "title",
            "content",
            "author",
            "source",
            "source_url",
            "category",
            "thumbnail_url",
            "published_at",
            "is_featured",
            "created_by",
            "credibility_score",
            "created_at",
            "updated_at",
        ]
