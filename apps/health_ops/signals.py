from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.notifications.services import notify_engagement
from .models import VideoEngineItemLike, VideoEngineItemComment


@receiver(post_save, sender=VideoEngineItemLike)
def notify_video_item_liked(sender, instance, created, **kwargs):
    # created is the only signal that matters here - the model has a
    # UniqueConstraint on (item, engine_session, user), so a like can't be
    # re-saved as a "new" row; there's nothing to gate against re-firing on.
    if not created:
        return
    item = instance.item
    notify_engagement(
        owner_user_id=item.created_by_id,
        actor_user=instance.user,
        notification_type="health.video_item.liked",
        verb="liked your video",
        target_type="health_video_item",
        target_id=item.id,
        target_title=item.title,
    )


@receiver(post_save, sender=VideoEngineItemComment)
def notify_video_item_commented(sender, instance, created, **kwargs):
    if not created:
        return
    item = instance.item
    notify_engagement(
        owner_user_id=item.created_by_id,
        actor_user=instance.user,
        notification_type="health.video_item.commented",
        verb="commented on your video",
        target_type="health_video_item",
        target_id=item.id,
        target_title=instance.body[:200],
        # Unlike the like/reaction receivers below, every comment is its
        # own event worth notifying about (a user can comment more than
        # once) - key on the comment's own id, not the default
        # (type, target, actor) key that's meant for togglable actions.
        dedup_key=f"health.video_item.commented:{instance.id}",
    )
