import uuid
from django.db import models
from django.utils import timezone

from apps.health_ops.models import TimeStampedUUIDModel
from apps.accounts.models import User


class PodcastChannel(TimeStampedUUIDModel):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    creator = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="podcast_channels"
    )
    cover_url = models.URLField(blank=True)
    category = models.CharField(max_length=200, blank=True)
    rss_feed_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)
    subscription_required = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class PodcastEpisode(TimeStampedUUIDModel):
    channel = models.ForeignKey(
        PodcastChannel, on_delete=models.CASCADE, related_name="episodes"
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    audio_url = models.URLField(blank=True)
    transcript = models.TextField(blank=True)
    duration_seconds = models.IntegerField(null=True, blank=True)
    chapter_markers = models.JSONField(default=list)
    episode_number = models.IntegerField(null=True, blank=True)
    season_number = models.IntegerField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    play_count = models.IntegerField(default=0)
    is_premium = models.BooleanField(default=False)

    class Meta:
        ordering = ["-published_at"]

    def __str__(self):
        return f"{self.channel.title} — {self.title}"


class KingdomMusicTrack(TimeStampedUUIDModel):
    GENRE_CHOICES = [
        ("gospel", "Gospel"),
        ("worship", "Worship"),
        ("christian_hiphop", "Christian Hip-Hop"),
        ("contemporary", "Contemporary"),
        ("hymn", "Hymn"),
        ("instrumental", "Instrumental"),
        ("other", "Other"),
    ]

    title = models.CharField(max_length=255)
    artist = models.CharField(max_length=200)
    album = models.CharField(max_length=255, blank=True)
    genre = models.CharField(max_length=30, choices=GENRE_CHOICES, default="gospel")
    audio_url = models.URLField(blank=True)
    cover_url = models.URLField(blank=True)
    duration_seconds = models.IntegerField(null=True, blank=True)
    lyrics = models.TextField(blank=True)
    ccli_number = models.CharField(max_length=50, blank=True)
    play_count = models.IntegerField(default=0)
    is_licensed = models.BooleanField(default=True)
    royalty_per_play = models.DecimalField(max_digits=8, decimal_places=6, default=0)
    uploaded_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="uploaded_tracks"
    )
    release_year = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} — {self.artist}"


class MusicPlaylist(TimeStampedUUIDModel):
    title = models.CharField(max_length=255)
    creator = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="music_playlists"
    )
    tracks = models.JSONField(default=list)
    cover_url = models.URLField(blank=True)
    is_public = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class RoyaltyRecord(TimeStampedUUIDModel):
    track = models.ForeignKey(
        KingdomMusicTrack, on_delete=models.CASCADE, related_name="royalty_records"
    )
    period_month = models.IntegerField()
    period_year = models.IntegerField()
    play_count = models.IntegerField(default=0)
    total_royalty = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-period_year", "-period_month"]

    def __str__(self):
        return f"Royalty {self.track.title} {self.period_year}/{self.period_month}"


class Ebook(TimeStampedUUIDModel):
    title = models.CharField(max_length=255)
    author = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    cover_url = models.URLField(blank=True)
    file_url = models.URLField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="USD")
    is_free = models.BooleanField(default=False)
    genre = models.CharField(max_length=200, blank=True)
    uploaded_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="uploaded_ebooks"
    )
    download_count = models.IntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class EbookPurchase(TimeStampedUUIDModel):
    ebook = models.ForeignKey(Ebook, on_delete=models.CASCADE, related_name="purchases")
    buyer = models.ForeignKey(User, on_delete=models.CASCADE, related_name="ebook_purchases")
    price_paid = models.DecimalField(max_digits=10, decimal_places=2)
    purchased_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-purchased_at"]

    def __str__(self):
        return f"{self.buyer} purchased {self.ebook.title}"


class PPVEvent(TimeStampedUUIDModel):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    creator = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="ppv_events"
    )
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="USD")
    stream_url = models.URLField(blank=True)
    scheduled_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    access_code = models.CharField(max_length=32, blank=True)
    thumbnail_url = models.URLField(blank=True)
    viewer_count = models.IntegerField(default=0)

    class Meta:
        ordering = ["-scheduled_at"]

    def __str__(self):
        return self.title


class PPVTicket(TimeStampedUUIDModel):
    event = models.ForeignKey(PPVEvent, on_delete=models.CASCADE, related_name="tickets")
    buyer = models.ForeignKey(User, on_delete=models.CASCADE, related_name="ppv_tickets")
    price_paid = models.DecimalField(max_digits=10, decimal_places=2)
    access_granted = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.buyer} — {self.event.title}"


class NewsArticle(TimeStampedUUIDModel):
    CATEGORY_CHOICES = [
        ("revival", "Revival"),
        ("persecution", "Persecution"),
        ("policy", "Policy"),
        ("business", "Business"),
        ("health", "Health"),
        ("education", "Education"),
        ("general", "General"),
    ]

    title = models.CharField(max_length=500)
    content = models.TextField()
    author = models.CharField(max_length=200)
    source = models.CharField(max_length=255, blank=True)
    source_url = models.URLField(blank=True)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default="general")
    thumbnail_url = models.URLField(blank=True)
    published_at = models.DateTimeField()
    is_featured = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="news_articles"
    )
    credibility_score = models.IntegerField(default=5)

    class Meta:
        ordering = ["-published_at"]

    def __str__(self):
        return self.title
