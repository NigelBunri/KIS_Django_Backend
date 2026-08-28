from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.notifications.services import notify_engagement
from .models import ChannelContentComment, ChannelContentReaction


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
