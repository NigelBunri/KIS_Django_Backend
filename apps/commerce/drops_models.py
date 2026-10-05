"""
Market Drops model for apps.commerce.

A MarketDrop is a shop-scheduled, time-boxed promotional listing: a
countdown-driven showcase of specific products over a start/end window.
This deliberately does NOT model a live-video broadcast (`is_live` is
always computed, never stored) - KIS has no live-shopping-stream infra
today, so a drop is "live" purely in the sense of "currently within its
scheduled window," matching exactly what the existing frontend
(MarketDropsPage.tsx) already renders for a non-video drop: countdown
timer, product count, "Watch live drop"/"Set reminder" CTA. The frontend's
own "Start a Live Drop" button already discloses real live video as
"Coming soon" - this model doesn't change that, it just gives the
scheduled/countdown half of the feature (which was fully built on the
frontend already) a real backend instead of a hardcoded empty list.

This module is imported from the bottom of models.py, same as
shipping_models.py/returns_models.py and for the same reason (Django
app-registry registration on every startup path).
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class MarketDrop(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey("commerce.Shop", on_delete=models.CASCADE, related_name="drops")
    title = models.CharField(max_length=200)
    cover_url = models.URLField(blank=True, default="")
    products = models.ManyToManyField("commerce.Product", related_name="drops", blank=True)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["starts_at"]
        indexes = [
            models.Index(fields=["shop", "starts_at"]),
            models.Index(fields=["starts_at", "ends_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.shop_id})"

    @property
    def is_live(self) -> bool:
        now = timezone.now()
        return self.starts_at <= now < self.ends_at

    @property
    def has_ended(self) -> bool:
        return timezone.now() >= self.ends_at
