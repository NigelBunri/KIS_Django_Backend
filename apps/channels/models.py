# apps/channels/models.py
import uuid

from django.db import models
from django.utils import timezone

from apps.accounts.models import User
from apps.chat.models import Conversation
from common.media_urls import normalize_image_payload


class Channel(models.Model):
    class ChannelType(models.TextChoices):
        TEXT = "text", "Text"
        ANNOUNCEMENT = "announcement", "Announcement"
        PRIVATE = "private", "Private"
        VOICE = "voice", "Voice"

    """
    Channel entity (broadcast-like or topic-based stream).

    - Can belong to a Partner and/or Community.
    - Backed by a Conversation of type CHANNEL.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    partner = models.ForeignKey(
        "partners.Partner",  # string ref: avoids circular import issues
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="channels",
        help_text="Owning partner (optional).",
    )

    community = models.ForeignKey(
        "communities.Community",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="channels",
        help_text="Owning community (optional).",
    )

    category = models.ForeignKey(
        "partners.PartnerServerCategory",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="channels",
        help_text="Optional partner server category used to group channels in the sidebar.",
    )

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    description = models.TextField(blank=True)
    channel_type = models.CharField(
        max_length=24,
        choices=ChannelType.choices,
        default=ChannelType.ANNOUNCEMENT,
        db_index=True,
    )
    order = models.PositiveIntegerField(default=0)

    avatar_url = models.URLField(
        blank=True,
        help_text="Optional avatar / icon for this channel.",
    )
    invite_messages = models.JSONField(
        default=list,
        blank=True,
        help_text="Optional invite-only messages shown before subscription.",
    )

    owner = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="owned_channels",
    )

    conversation = models.OneToOneField(
        Conversation,
        on_delete=models.CASCADE,
        related_name="channel",
        help_text="The chat conversation backing this channel.",
    )

    is_archived = models.BooleanField(
        default=False,
        help_text="Soft-archive the channel (and usually its conversation).",
    )

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_channel"
        unique_together = [
            ("community", "slug"),
            ("partner", "slug"),
        ]
        ordering = ["order", "name"]
        indexes = [
            models.Index(fields=["partner", "category", "order"]),
            models.Index(fields=["partner", "channel_type"]),
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        self.avatar_url = normalize_image_payload(self.avatar_url)
        super().save(*args, **kwargs)


class Subchannel(models.Model):
    """
    A topic-specific sub-unit nested inside a parent Channel.
    Members of the parent channel can post and read here.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    channel = models.ForeignKey(
        Channel,
        on_delete=models.CASCADE,
        related_name="subchannels",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)
    is_archived = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_subchannels",
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "channel_subchannel"
        ordering = ["order", "name"]
        unique_together = [("channel", "name")]

    def __str__(self) -> str:
        return f"{self.channel.name} / {self.name}"
