from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.notifications.services import notify_engagement
from .models import CommunityPostComment, CommunityPostReaction


@receiver(post_save, sender=CommunityPostComment)
def notify_post_commented(sender, instance, created, **kwargs):
    if not created:
        return
    post = instance.post
    notify_engagement(
        owner_user_id=post.author_id,
        actor_user=instance.author,
        notification_type="community.post.commented",
        verb="commented on your post",
        target_type="community_post",
        target_id=post.id,
        target_title=instance.text[:200],
        dedup_key=f"community.post.commented:{instance.id}",
    )


@receiver(post_save, sender=CommunityPostReaction)
def notify_post_reacted(sender, instance, created, **kwargs):
    # unique_together = ("post", "user") - a re-save is an emoji change,
    # not a new reaction, so only the first ever reaction notifies.
    if not created:
        return
    post = instance.post
    notify_engagement(
        owner_user_id=post.author_id,
        actor_user=instance.user,
        notification_type="community.post.reacted",
        verb="reacted to your post",
        target_type="community_post",
        target_id=post.id,
        # post.text is a rich-text JSON doc, not a plain string - no safe
        # plain-text preview available here, unlike the comment body above.
        target_title="Tap to view.",
    )
