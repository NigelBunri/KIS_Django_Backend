from django.contrib.postgres.search import SearchVector
from django.db.models import TextField, Value
from django.db.models.functions import Coalesce
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.notifications.services import notify_engagement
from .models import ChannelContent, ChannelContentComment, ChannelContentReaction


@receiver(post_save, sender=ChannelContent)
def update_content_search_vector(sender, instance, **kwargs):
    """Keeps search_vector in sync with title/description/text_plain/tags
    on every save, so BroadcastSearchView's real full-text search (see
    that view) never goes stale. A plain .update() on this one row, not
    instance.save() - re-entering save() here would re-trigger this same
    signal. tags is a JSONField (a list), which SearchVector can't index
    directly - joined into plain text first.
    """
    tags = instance.tags if isinstance(instance.tags, list) else []
    tags_text = " ".join(str(t) for t in tags)
    empty_text = Value("", output_field=TextField())
    ChannelContent.objects.filter(pk=instance.pk).update(
        search_vector=(
            SearchVector("title", weight="A")
            + SearchVector(Coalesce("description", empty_text), weight="B")
            + SearchVector(Coalesce("text_plain", empty_text), weight="B")
            + SearchVector(Value(tags_text, output_field=TextField()), weight="C")
        )
    )


@receiver(post_save, sender=ChannelContentComment)
def notify_content_commented(sender, instance, created, **kwargs):
    if not created:
        return
    content = instance.content
    notify_engagement(
        owner_user_id=content.channel.owner_user_id,
        actor_user=instance.user,
        notification_type="channel.content.commented",
        verb=f"commented on {content.title or content.channel.display_name}",
        target_type="channel_content",
        target_id=content.id,
        target_title=instance.body[:200],
        dedup_key=f"channel.content.commented:{instance.id}",
    )
    # A reply is also worth telling the parent commenter about,
    # independently of the content owner above (skip if they're the same
    # person, or if this is itself a reply-to-yourself - notify_engagement
    # already no-ops on self-notification either way).
    if instance.parent_id and instance.parent.user_id:
        notify_engagement(
            owner_user_id=instance.parent.user_id,
            actor_user=instance.user,
            notification_type="channel.content.comment_replied",
            verb="replied to your comment",
            target_type="channel_content",
            target_id=content.id,
            target_title=instance.body[:200],
            dedup_key=f"channel.content.comment_replied:{instance.id}",
        )


@receiver(post_save, sender=ChannelContentReaction)
def notify_content_reacted(sender, instance, created, **kwargs):
    # unique_together = ("content", "user") - a re-save changes the
    # reaction type, not a new one; only the first ever reaction notifies.
    if not created:
        return
    content = instance.content
    notify_engagement(
        owner_user_id=content.channel.owner_user_id,
        actor_user=instance.user,
        notification_type="channel.content.reacted",
        verb=f"reacted to {content.title or content.channel.display_name}",
        target_type="channel_content",
        target_id=content.id,
        target_title=content.title,
    )
