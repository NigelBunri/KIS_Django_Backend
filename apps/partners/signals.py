from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.partners.models import Partner, PartnerPostComment, PartnerPostReaction
from apps.partners.services import ensure_default_organization_app
from apps.notifications.services import notify_engagement


@receiver(post_save, sender=Partner)
def ensure_partner_bible_app(sender, instance: Partner, created: bool, **kwargs):
    if not created:
        return
    ensure_default_organization_app(instance)


@receiver(post_save, sender=PartnerPostComment)
def notify_partner_post_commented(sender, instance, created, **kwargs):
    if not created:
        return
    post = instance.post
    notify_engagement(
        owner_user_id=post.author_id,
        actor_user=instance.author,
        notification_type="partner.post.commented",
        verb="commented on your post",
        target_type="partner_post",
        target_id=post.id,
        target_title=instance.text[:200],
        dedup_key=f"partner.post.commented:{instance.id}",
    )


@receiver(post_save, sender=PartnerPostReaction)
def notify_partner_post_reacted(sender, instance, created, **kwargs):
    # unique_together = ("post", "user") - a re-save is an emoji change,
    # not a new reaction; only the first ever reaction notifies.
    if not created:
        return
    post = instance.post
    notify_engagement(
        owner_user_id=post.author_id,
        actor_user=instance.user,
        notification_type="partner.post.reacted",
        verb="reacted to your post",
        target_type="partner_post",
        target_id=post.id,
        target_title="Tap to view.",
    )
