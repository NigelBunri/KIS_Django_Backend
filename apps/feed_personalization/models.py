from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class FeedAffinityProfile(models.Model):
    """
    Lightweight per-user profile that is updated each time we log
    impressions/interactions so the ranking engine can adapt immediately.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="feed_affinity_profile",
    )
    broadcast_score = models.FloatField(default=0.0)
    community_score = models.FloatField(default=0.0)
    partner_score = models.FloatField(default=0.0)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "feed_affinity_profile"


class FeedInteraction(models.Model):
    """
    Event log for impressions/actions so we can compute online statistics.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="feed_interactions",
    )
    feed_type = models.CharField(max_length=32)
    event = models.CharField(max_length=32)
    weight = models.FloatField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "feed_interaction"
        indexes = [
            models.Index(fields=["user", "feed_type", "created_at"]),
        ]
